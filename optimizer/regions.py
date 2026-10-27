"""Region discovery: which BigQuery locations hold data, so the collector knows where to go.

Why this exists: INFORMATION_SCHEMA views are region-qualified (`region-us-east4`), so BigQuery
itself can answer "what's in this region?" but not "which regions do I have?". With
`regions: auto` the collector asks Cloud Asset Inventory for every BigQuery dataset in
`asset_scope` (ideally the organization) and visits each location it finds, plus
`regions_include`, minus `regions_exclude`. The home location (config `location`, where
optimizer_ops lives) is always visited.

Each run is recorded in optimizer_ops.regions_seen. A region that appears after the first
discovery run is flagged (`is_new_alert`) so the dashboard can say "new region detected" —
e.g. someone created a dataset in a region where no reservation exists.

Needs: the Cloud Asset API (cloudasset.googleapis.com) and roles/cloudasset.viewer on the scope.
If discovery fails, the collector falls back to the last known regions (never stops collecting).
"""
from __future__ import annotations

import re
from typing import Iterable

from . import bq
from .config import Config

AUTO = "auto"
ASSET_TYPE = "bigquery.googleapis.com/Dataset"
_ASSET_URL = "https://cloudasset.googleapis.com/v1/{scope}:searchAllResources"
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")

REGIONS_SEEN_DDL = """
CREATE TABLE IF NOT EXISTS `{ops}.regions_seen` (
  region         STRING NOT NULL,   -- lowercase location, e.g. 'us', 'us-east4'
  first_seen_at  TIMESTAMP,
  last_seen_at   TIMESTAMP,
  dataset_count  INT64,             -- datasets found there by the last discovery run
  project_count  INT64,
  included       BOOL,              -- on the collector's route in the last run
  source         STRING,            -- 'asset_inventory' | 'config'
  is_new_alert   BOOL,              -- first seen after a baseline existed -> flag in the UI
  run_id         STRING
)"""


# ----------------------------------------------------------------------------- pure helpers

def normalize(loc) -> str | None:
    """'US' -> 'us', 'region-us-east4' -> 'us-east4'. Invalid names -> None."""
    if loc is None:
        return None
    r = str(loc).strip().lower()
    if r.startswith("region-"):
        r = r[len("region-"):]
    return r if _REGION_RE.match(r) and r != AUTO else None


def _norm_list(vals) -> list[str]:
    if vals is None:
        return []
    if isinstance(vals, str):
        vals = vals.split(",")
    out = []
    for v in vals:
        n = normalize(v)
        if n and n not in out:
            out.append(n)
    return out


def home_region(c: Config) -> str:
    """The location of the ops dataset — the collector SQL writes there directly."""
    return normalize(c.get("location") or "US") or "us"


def is_auto(c: Config) -> bool:
    v = c.get("regions")
    if isinstance(v, str):
        return v.strip().lower() == AUTO
    return isinstance(v, (list, tuple)) and any(str(x).strip().lower() == AUTO for x in v)


def static_regions(c: Config) -> list[str]:
    """Regions listed explicitly in config (ignores the 'auto' keyword)."""
    v = c.get("regions")
    return _norm_list([] if isinstance(v, str) and v.strip().lower() == AUTO else v)


def resolve(c: Config, discovered: Iterable[str] | None = None,
            last_known: Iterable[str] | None = None) -> list[str]:
    """Tonight's route (pure). Auto mode: discovered locations (or the last known ones when
    discovery failed) + any listed regions + include - exclude. List mode: the configured list.
    The home location is always visited unless excluded. Home first, then alphabetical."""
    home = home_region(c)
    include = set(_norm_list(c.get("regions_include")))
    exclude = set(_norm_list(c.get("regions_exclude")))
    if is_auto(c):
        base = set(_norm_list(discovered if discovered is not None else last_known))
        base |= set(static_regions(c))
    else:
        base = set(static_regions(c))
    route = (base | include | {home}) - exclude
    return sorted(route, key=lambda r: (r != home, r))


def aggregate_assets(results: Iterable[dict]) -> dict[str, dict]:
    """Cloud Asset Inventory results -> {region: {"datasets": n, "projects": n}} (pure)."""
    counts: dict[str, int] = {}
    projects: dict[str, set] = {}
    for res in results:
        reg = normalize(res.get("location"))
        if not reg:
            continue
        counts[reg] = counts.get(reg, 0) + 1
        projects.setdefault(reg, set()).add(res.get("project") or "?")
    return {r: {"datasets": counts[r], "projects": len(projects[r])} for r in sorted(counts)}


def new_regions(previous: Iterable[str], current: Iterable[str]) -> list[str]:
    """Regions to flag as new: only once a baseline exists (the very first run flags nothing)."""
    prev = set(previous)
    return sorted(set(current) - prev) if prev else []


# ----------------------------------------------------------------------------- discovery

def _credentials(c: Config):
    creds = getattr(bq.client(c), "_credentials", None)
    if creds is None:
        import google.auth
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return creds


def discover(c: Config) -> dict[str, dict]:
    """Every BigQuery dataset in `asset_scope`, grouped by location (Cloud Asset Inventory
    searchAllResources). Raises on API / permission errors so the caller can fall back."""
    from google.auth.transport.requests import AuthorizedSession
    scope = c.get("asset_scope") or f"projects/{c['project_id']}"
    creds = _credentials(c)
    session = AuthorizedSession(creds)
    headers = {}
    try:  # user credentials need an explicit quota project; service accounts don't
        from google.oauth2.credentials import Credentials as UserCredentials
        if isinstance(creds, UserCredentials):
            headers["x-goog-user-project"] = c["project_id"]
    except Exception:
        pass
    params = {"assetTypes": ASSET_TYPE, "pageSize": 500}
    results: list[dict] = []
    for _ in range(200):  # pagination guard: 200 pages x 500 datasets
        resp = session.get(_ASSET_URL.format(scope=scope), params=params, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Cloud Asset Inventory search on {scope} failed "
                               f"(HTTP {resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        results.extend(body.get("results", []))
        token = body.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    return aggregate_assets(results)


def last_known(c: Config) -> list[str]:
    """Regions on the route in the last successful run (fallback when discovery fails)."""
    try:
        rows = bq.query(c, f"SELECT region FROM `{c.ops}.regions_seen` WHERE included")
        return _norm_list([r["region"] for r in rows])
    except Exception:
        return []


def record(c: Config, discovered: dict[str, dict], route: list[str], source: str,
           run_id: str = "") -> list[str]:
    """Upsert tonight's regions into optimizer_ops.regions_seen; return newly seen regions."""
    bq.execute(c, REGIONS_SEEN_DDL.format(ops=c.ops))
    previous = [r["region"] for r in bq.query(c, f"SELECT region FROM `{c.ops}.regions_seen`")]
    fresh = new_regions(previous, set(discovered) | set(route))
    names = sorted({r for r in set(discovered) | set(route) if _REGION_RE.match(r)})
    if not names:
        return []
    params: dict = {"src": source, "run": run_id, "alert": bool(previous)}
    selects = []
    for i, reg in enumerate(names):
        info = discovered.get(reg) or {}
        params.update({f"r{i}": reg, f"d{i}": int(info.get("datasets") or 0),
                       f"p{i}": int(info.get("projects") or 0), f"i{i}": reg in route})
        selects.append(f"SELECT @r{i} AS region, @d{i} AS dataset_count, @p{i} AS project_count, "
                       f"@i{i} AS included")
    bq.execute(c, f"""
        MERGE `{c.ops}.regions_seen` T
        USING ({' UNION ALL '.join(selects)}) S
        ON T.region = S.region
        WHEN MATCHED THEN UPDATE SET
          last_seen_at = CURRENT_TIMESTAMP(), dataset_count = S.dataset_count,
          project_count = S.project_count, included = S.included, source = @src, run_id = @run
        WHEN NOT MATCHED BY TARGET THEN INSERT
          (region, first_seen_at, last_seen_at, dataset_count, project_count, included, source,
           is_new_alert, run_id)
          VALUES (S.region, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), S.dataset_count,
                  S.project_count, S.included, @src, @alert, @run)
        WHEN NOT MATCHED BY SOURCE THEN UPDATE SET included = FALSE, dataset_count = 0""", params)
    return fresh


def plan_route(c: Config, run_id: str = "", record_result: bool = True) -> tuple[list[str], dict]:
    """Decide which regions to collect this run. Never raises for discovery problems:
    falls back to the last known regions (auto mode) and reports the error in `info`."""
    info: dict = {"mode": AUTO if is_auto(c) else "list", "source": "config",
                  "discovered": {}, "new_regions": [], "error": None,
                  "scope": c.get("asset_scope") or f"projects/{c['project_id']}"}
    discovered = None
    if is_auto(c):
        try:
            discovered = discover(c)
            info["source"], info["discovered"] = "asset_inventory", discovered
        except Exception as e:  # noqa: BLE001
            info["source"], info["error"] = "last_known", str(e)[:500]
    route = resolve(c, list(discovered) if discovered is not None else None,
                    last_known(c) if (is_auto(c) and discovered is None) else None)
    if record_result and (discovered is not None or not is_auto(c)):
        try:
            info["new_regions"] = record(c, discovered or {}, route, info["source"], run_id)
        except Exception as e:  # noqa: BLE001
            info["record_error"] = str(e)[:300]
    return route, info


def dashboard_info(c: Config) -> dict:
    """What the review UI shows: regions on the route, newly detected regions, last run."""
    mode = AUTO if is_auto(c) else "list"
    days = int(c.get("region_alert_days", 7) or 7)
    try:
        rows = bq.query(c, f"""
            SELECT region, first_seen_at, last_seen_at, dataset_count, project_count, included, source,
                   COALESCE(is_new_alert, FALSE)
                     AND first_seen_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY) AS alert
            FROM `{c.ops}.regions_seen`
            ORDER BY region""")
    except Exception:
        rows = []

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    regions = [{"region": r["region"], "dataset_count": r.get("dataset_count"),
                "project_count": r.get("project_count"), "first_seen_at": _iso(r.get("first_seen_at")),
                "last_seen_at": _iso(r.get("last_seen_at")), "source": r.get("source"),
                "alert": bool(r.get("alert")), "home": r["region"] == home_region(c)}
               for r in rows if r.get("included")]
    discovered = bool(regions)
    if not regions:  # nothing recorded yet: show what the collector would use from config
        regions = [{"region": r, "dataset_count": None, "project_count": None, "first_seen_at": None,
                    "last_seen_at": None, "source": "config", "alert": False,
                    "home": r == home_region(c)} for r in resolve(c, None, None)]
    last_run = max((r["last_seen_at"] for r in regions if r.get("last_seen_at")), default=None)
    regions.sort(key=lambda r: (not r["home"], r["region"]))   # home first, then alphabetical
    return {"mode": mode, "home": home_region(c), "discovered": discovered,
            "scope": c.get("asset_scope") or f"projects/{c['project_id']}",
            "regions": regions, "alerts": [r for r in regions if r["alert"]], "last_run": last_run}
