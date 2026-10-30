"""Live dropdown options for the Settings page's inline editors (`GET /api/settings/options`
and `GET /api/settings/table-columns`).

Every probe here is read-only and best-effort: if a table hasn't been populated yet or an API
isn't enabled, that section simply returns static / configured options and never fails the page.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Mapping

from . import attribution, bq, governance, regions, settings_catalog as sc
from .config import cfg

# Official BigQuery multi-regions and regional locations, grouped for <optgroup> dropdowns.
BQ_LOCATIONS: list[dict[str, str]] = [
    # Multi-regions
    {"value": "us", "label": "US multi-region", "group": "Multi-regions"},
    {"value": "eu", "label": "EU multi-region", "group": "Multi-regions"},
    # Americas
    {"value": "us-central1", "label": "Iowa", "group": "Americas"},
    {"value": "us-east1", "label": "South Carolina", "group": "Americas"},
    {"value": "us-east4", "label": "Northern Virginia", "group": "Americas"},
    {"value": "us-east5", "label": "Columbus", "group": "Americas"},
    {"value": "us-south1", "label": "Dallas", "group": "Americas"},
    {"value": "us-west1", "label": "Oregon", "group": "Americas"},
    {"value": "us-west2", "label": "Los Angeles", "group": "Americas"},
    {"value": "us-west3", "label": "Salt Lake City", "group": "Americas"},
    {"value": "us-west4", "label": "Las Vegas", "group": "Americas"},
    {"value": "northamerica-northeast1", "label": "Montréal", "group": "Americas"},
    {"value": "northamerica-northeast2", "label": "Toronto", "group": "Americas"},
    {"value": "northamerica-south1", "label": "Mexico", "group": "Americas"},
    {"value": "southamerica-east1", "label": "São Paulo", "group": "Americas"},
    {"value": "southamerica-west1", "label": "Santiago", "group": "Americas"},
    # Europe
    {"value": "europe-central2", "label": "Warsaw", "group": "Europe"},
    {"value": "europe-north1", "label": "Finland", "group": "Europe"},
    {"value": "europe-southwest1", "label": "Madrid", "group": "Europe"},
    {"value": "europe-west1", "label": "Belgium", "group": "Europe"},
    {"value": "europe-west2", "label": "London", "group": "Europe"},
    {"value": "europe-west3", "label": "Frankfurt", "group": "Europe"},
    {"value": "europe-west4", "label": "Netherlands", "group": "Europe"},
    {"value": "europe-west6", "label": "Zürich", "group": "Europe"},
    {"value": "europe-west8", "label": "Milan", "group": "Europe"},
    {"value": "europe-west9", "label": "Paris", "group": "Europe"},
    {"value": "europe-west10", "label": "Berlin", "group": "Europe"},
    {"value": "europe-west12", "label": "Turin", "group": "Europe"},
    # Asia Pacific, Middle East & Africa
    {"value": "asia-east1", "label": "Taiwan", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-east2", "label": "Hong Kong", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-northeast1", "label": "Tokyo", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-northeast2", "label": "Osaka", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-northeast3", "label": "Seoul", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-south1", "label": "Mumbai", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-south2", "label": "Delhi", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-southeast1", "label": "Singapore", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "asia-southeast2", "label": "Jakarta", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "australia-southeast1", "label": "Sydney", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "australia-southeast2", "label": "Melbourne", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "me-central1", "label": "Doha", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "me-central2", "label": "Dammam", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "me-west1", "label": "Tel Aviv", "group": "Asia Pacific, Middle East & Africa"},
    {"value": "africa-south1", "label": "Johannesburg", "group": "Asia Pacific, Middle East & Africa"},
]

_CRM_ANCESTRY_URL = "https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:getAncestry"

_DEFAULT_TABLES = {
    "employee_hierarchy_columns": "employee_hierarchy",
    "service_account_owner_columns": "service_account_owners",
}


def empty_options(c: Mapping) -> dict[str, Any]:
    """Safe empty shape returned to view-only visitors."""
    return {
        "restricted": True,
        "home_region": regions.home_region(c),
        "project_id": c["project_id"],
        "regions": [],
        "scopes": [],
        "projects": [],
        "datasets": [],
        "tables": [],
        "emails": [],
    }


def _probe_regions(c: Any) -> list[dict[str, Any]]:
    home = regions.home_region(c)
    seen: dict[str, dict[str, Any]] = {}
    try:
        rows = bq.query(c, f"""
            SELECT region, dataset_count, project_count, included
            FROM `{c.ops}.regions_seen`
            ORDER BY region""")
        for r in rows or []:
            reg = regions.normalize(r.get("region"))
            if reg:
                seen[reg] = {
                    "dataset_count": r.get("dataset_count"),
                    "project_count": r.get("project_count"),
                    "included": bool(r.get("included")),
                }
    except Exception:
        pass

    by_val = {loc["value"]: dict(loc) for loc in BQ_LOCATIONS}
    out: list[dict[str, Any]] = []
    all_codes = list(by_val) + [r for r in sorted(seen) if r not in by_val]
    for code in all_codes:
        meta = by_val.get(code, {"value": code, "label": code, "group": "Discovered"})
        info = seen.get(code)
        is_home = code == home
        discovered = is_home or (info is not None and (info.get("dataset_count") or 0) > 0) or (info is not None and info.get("included", False))
        out.append({
            "value": code,
            "label": meta["label"],
            "group": meta["group"],
            "home": is_home,
            "discovered": bool(discovered),
            "dataset_count": info.get("dataset_count") if info else None,
            "project_count": info.get("project_count") if info else None,
        })
    return out


def _probe_ancestry(c: Any) -> list[str]:
    """Parent folders / organization for project_id via Cloud Resource Manager (optional)."""
    try:
        from google.auth.transport.requests import AuthorizedSession
        creds = regions._credentials(c)
        session = AuthorizedSession(creds)
        headers: dict[str, str] = {}
        try:
            from google.oauth2.credentials import Credentials as UserCredentials
            if isinstance(creds, UserCredentials):
                headers["x-goog-user-project"] = c["project_id"]
        except Exception:
            pass
        resp = session.post(_CRM_ANCESTRY_URL.format(project_id=c["project_id"]), json={}, headers=headers, timeout=3)
        if resp.status_code != 200:
            return []
        out: list[str] = []
        for anc in (resp.json() or {}).get("ancestor") or []:
            rid = anc.get("resourceId") or {}
            rtype, rval = str(rid.get("type") or "").lower(), str(rid.get("id") or "").strip()
            if rtype in ("organization", "folder") and rval:
                scope = f"{rtype}s/{rval}"
                if scope not in out:
                    out.append(scope)
        return out
    except Exception:
        return []


def _probe_datasets_and_projects(c: Any) -> tuple[list[dict[str, str]], list[str]]:
    home_proj = str(c["project_id"])
    ops_ds = str(c.get("ops_dataset") or "optimizer_ops")
    cur_target = str(c.get("target_dataset") or "").strip()
    ds_map: dict[str, str | None] = {}      # home-project dataset_id -> region
    projects: set[str] = {home_proj}

    try:
        for item in bq.client(c).list_datasets(project=home_proj, max_results=200):
            ds_id = getattr(item, "dataset_id", None)
            if ds_id:
                ds_map.setdefault(str(ds_id), None)
    except Exception:
        pass

    try:
        rows = bq.query(c, f"""
            SELECT DISTINCT project_id, dataset_id, LOWER(COALESCE(location, region)) AS region
            FROM `{c.ops}.dataset_state_daily`
            WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
            LIMIT 500""")
        for r in rows or []:
            p, d = r.get("project_id"), r.get("dataset_id")
            if p:
                projects.add(str(p))
            if p == home_proj and d:
                ds_map[str(d)] = regions.normalize(r.get("region")) or ds_map.get(str(d))
    except Exception:
        pass

    try:
        rows = bq.query(c, f"""
            SELECT DISTINCT project_id
            FROM `{c.ops}.jobs_events`
            WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
              AND project_id IS NOT NULL
            LIMIT 100""")
        for r in rows or []:
            if r.get("project_id"):
                projects.add(str(r["project_id"]))
    except Exception:
        pass

    if c.get("reservation_admin_project"):
        projects.add(str(c["reservation_admin_project"]))

    datasets: list[dict[str, str]] = []
    for ds_id in sorted(ds_map):
        if ds_id == ops_ds and ds_id != cur_target:
            continue
        reg = ds_map[ds_id]
        label = f"{ds_id} ({reg})" if reg else ds_id
        datasets.append({"value": ds_id, "label": label})
    return datasets, sorted(projects, key=lambda p: (p != home_proj, p))


def _probe_tables(c: Any) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    try:
        rows = bq.query(c, f"""
            SELECT DISTINCT project_id, dataset_id, table_id, LOWER(region) AS region
            FROM `{c.ops}.table_state_daily`
            WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
              AND project_id IS NOT NULL AND dataset_id IS NOT NULL AND table_id IS NOT NULL
            ORDER BY project_id, dataset_id, table_id
            LIMIT 400""")
        for r in rows or []:
            fqn = f"{r['project_id']}.{r['dataset_id']}.{r['table_id']}"
            seen[fqn] = {
                "value": fqn,
                "project": str(r["project_id"]),
                "dataset": str(r["dataset_id"]),
                "table": str(r["table_id"]),
                "region": regions.normalize(r.get("region")) or "",
            }
    except Exception:
        pass

    if not seen:
        home_proj = str(c["project_id"])
        try:
            client = bq.client(c)
            for ds in list(client.list_datasets(project=home_proj, max_results=12)):
                ds_id = getattr(ds, "dataset_id", None)
                if not ds_id:
                    continue
                for tbl in list(client.list_tables(f"{home_proj}.{ds_id}", max_results=40)):
                    t_id = getattr(tbl, "table_id", None)
                    if t_id and not str(t_id).startswith("_stg_"):
                        fqn = f"{home_proj}.{ds_id}.{t_id}"
                        seen[fqn] = {"value": fqn, "project": home_proj, "dataset": str(ds_id),
                                     "table": str(t_id), "region": ""}
        except Exception:
            pass
    return [seen[k] for k in sorted(seen)]


def _probe_people(c: Any, editors: Iterable[str]) -> list[dict[str, str]]:
    roles: dict[str, list[str]] = {}

    def add(raw_email: Any, tag: str | None = None) -> None:
        e = str(raw_email or "").strip().lower()
        if not e or "@" not in e or attribution.is_service_account(e):
            return
        tags = roles.setdefault(e, [])
        if tag and tag not in tags:
            tags.append(tag)

    for e in sorted(sc.settings_admins(c)):
        add(e, "Settings admin")
    for e in sorted(editors or []):
        add(e, "Settings editor")
    for e in sorted(governance.finops_approvers(c)):
        add(e, "FinOps approver")
    if c.get("default_owner"):
        add(c.get("default_owner"), "Current fallback owner")

    try:
        rows = bq.query(c, f"""
            SELECT user_email, user_name, department, director_email, director_name
            FROM `{c.ops}.employee_hierarchy`
            LIMIT 200""")
        for r in rows or []:
            parts = [str(x).strip() for x in (r.get("user_name"), r.get("department")) if x]
            add(r.get("user_email"), " · ".join(parts) if parts else None)
            d_name = str(r.get("director_name") or "").strip()
            add(r.get("director_email"), d_name or "Director")
    except Exception:
        pass

    try:
        rows = bq.query(c, f"""
            SELECT DISTINCT owner_email, owner_team
            FROM `{c.ops}.service_account_owners`
            WHERE owner_email IS NOT NULL
            LIMIT 100""")
        for r in rows or []:
            team = str(r.get("owner_team") or "").strip()
            add(r.get("owner_email"), f"SA owner · {team}" if team else "SA owner")
    except Exception:
        pass

    out: list[dict[str, str]] = []
    for email in sorted(roles):
        tags = roles[email]
        label = f"{email} — {', '.join(tags[:2])}" if tags else email
        out.append({"value": email, "label": label})
    return out


def _build_scopes(c: Any, ancestry: list[str], projects: list[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def add(scope: str | None, note: str) -> None:
        s = str(scope or "").strip()
        if not s or s in seen or not sc._SCOPE_RE.match(s):
            return
        seen.add(s)
        out.append({"value": s, "label": f"{s} · {note}"})

    # Prefer org scopes first (recommended for auto discovery), then folders, then projects.
    configured = [c.get("asset_scope")]
    try:
        configured.append(cfg(ui=False).get("asset_scope"))
    except (Exception, SystemExit):
        pass

    for s in ancestry:
        if s.startswith("organizations/"):
            add(s, "Your organization (recommended)")
    for s in configured:
        if s and str(s).startswith("organizations/"):
            add(str(s), "Your organization (recommended)")
    for s in ancestry:
        if s.startswith("folders/"):
            add(s, "Parent folder")
    for s in configured:
        if s and str(s).startswith("folders/"):
            add(str(s), "Configured folder")

    home_proj = str(c["project_id"])
    add(f"projects/{home_proj}", "Home project")
    for p in projects:
        if p != home_proj:
            add(f"projects/{p}", "Discovered project")
    for s in configured:
        if s:
            add(str(s), "Configured scope")
    return out


def discover_options(c: Any, editors: Iterable[str] | None = None) -> dict[str, Any]:
    """Live & static choices for Settings dropdowns (runs read-only probes in parallel)."""
    eds = list(editors or [])
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_reg = pool.submit(_probe_regions, c)
        f_ds_proj = pool.submit(_probe_datasets_and_projects, c)
        f_tbl = pool.submit(_probe_tables, c)
        f_ppl = pool.submit(_probe_people, c, eds)
        f_anc = pool.submit(_probe_ancestry, c)

        reg_list = f_reg.result()
        datasets, proj_ids = f_ds_proj.result()
        tables = f_tbl.result()
        emails = f_ppl.result()
        ancestry = f_anc.result()

    home_proj = str(c["project_id"])
    projects = [
        {"value": p, "label": f"{p} · Home project" if p == home_proj else p}
        for p in proj_ids
    ]
    return {
        "restricted": False,
        "home_region": regions.home_region(c),
        "project_id": home_proj,
        "regions": reg_list,
        "scopes": _build_scopes(c, ancestry, proj_ids),
        "projects": projects,
        "datasets": datasets,
        "tables": tables,
        "emails": emails,
    }


def table_columns(c: Any, table: str | None = None, key: str | None = None) -> dict[str, Any]:
    """Column names + BigQuery types for a configured (or default) lookup table."""
    raw = str(table or "").strip().strip("`")
    is_default = False
    if not raw and key in _DEFAULT_TABLES:
        raw = f"{c['project_id']}.{c.get('ops_dataset') or 'optimizer_ops'}.{_DEFAULT_TABLES[key]}"
        is_default = True
    if not raw:
        return {"ok": True, "table": None, "is_default": False, "columns": []}
    if not sc._TABLE_RE.match(raw):
        return {"ok": False, "table": raw, "is_default": False, "columns": [],
                "error": "Use the full name project.dataset.table."}
    try:
        tbl = bq.client(c).get_table(raw)
        cols = [{"name": f.name, "type": f.field_type} for f in (tbl.schema or [])]
        return {"ok": True, "table": raw, "is_default": is_default, "columns": cols}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "table": raw, "is_default": is_default, "columns": [],
                "error": str(e).splitlines()[0][:200]}
