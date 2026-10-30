"""Setup check: can the tool do its job with the permissions it has?

Runs small, read-only probes as the identity the tool runs under and says, in plain English,
what works, what doesn't, and the exact command that fixes it. Used by the review UI's
Settings page (`GET /api/setup-check`) and by `python -m optimizer.cli check`, which can run
as any account, e.g. the nightly job's:

    gcloud run jobs execute <job> --args=-m,optimizer.cli,check --wait

Every probe is read-only: SESSION_USER(), LIMIT 1 reads of INFORMATION_SCHEMA views (10 MB
minimum bill each), dataset/table metadata, IAM testPermissions, one Cloud Asset search and
one Data Transfer list.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import time
from typing import Any, Callable

from . import bq, regions
from .config import Config

OK, WARN, FAIL, INFO, SKIP = "ok", "warn", "fail", "info", "skip"


# ----------------------------------------------------------------------------- pure helpers

def classify(err: Any) -> str:
    """'api_disabled' | 'not_enabled' | 'denied' | 'not_found' | 'other' from an error message."""
    m = str(err).lower()
    if "service_disabled" in m or "has not been used in project" in m or "it is disabled" in m:
        return "api_disabled"
    if "enable_info_schema_storage" in m or "hasn't been enabled" in m or "has not been enabled" in m:
        return "not_enabled"
    if any(s in m for s in ("access denied", "permission", "403", "does not have", "forbidden")):
        return "denied"
    if "not found" in m or "404" in m:
        return "not_found"
    return "other"


def short(err: Any, limit: int = 280) -> str:
    """First meaningful line of a Google API error, without the HTTP verb / URL prefix."""
    text = str(err).strip().split("\n")[0]
    if ": " in text and text.split(" ", 1)[0].isdigit() and " https://" in text:
        text = text.split(": ", 1)[1]
    return text if len(text) <= limit else text[: limit - 1] + "…"


def member(who: str | None) -> str:
    if not who:
        return "serviceAccount:YOUR_SERVICE_ACCOUNT"
    return ("serviceAccount:" if who.endswith(".gserviceaccount.com") else "user:") + who


def scope_kind(c: Config) -> tuple[str, str]:
    """('organizations'|'folders'|'projects', id) for the discovery scope."""
    scope = str(c.get("asset_scope") or f"projects/{c['project_id']}")
    kind, _, sid = scope.partition("/")
    return (kind, sid) if kind in ("organizations", "folders", "projects") and sid else ("projects", c["project_id"])


def org_id(c: Config) -> str:
    kind, sid = scope_kind(c)
    return sid if kind == "organizations" else "YOUR_ORG_ID"


def grant(scope: str, sid: str, who: str | None, role: str) -> str:
    """gcloud command that grants `role` to `who` on a project, folder or organization."""
    cmd = {"projects": "gcloud projects", "folders": "gcloud resource-manager folders",
           "organizations": "gcloud organizations"}[scope]
    return f"{cmd} add-iam-policy-binding {sid} --member={member(who)} --role={role}"


def grant_sql(kind: str, fqn: str, who: str | None, role: str) -> str:
    """BigQuery DCL that grants `role` on one dataset (SCHEMA) or TABLE."""
    return f'GRANT `{role}` ON {kind} `{fqn}` TO "{member(who)}";'


def bq_sql(project_id: str, sql: str, location: str | None = None) -> str:
    """Cloud Shell `bq query` command wrapping GoogleSQL in single quotes so Bash
    never evaluates backticks (`project`) or parentheses."""
    loc = f" --location={location}" if location else ""
    escaped = sql.replace("'", "'\"'\"'")
    return f"bq --project_id={project_id}{loc} query --use_legacy_sql=false '{escaped}'"


def result(id: str, title: str, why: str, status: str, detail: str, fix: str | None = None,
           region: str | None = None, optional: bool = False) -> dict:
    return {"id": id, "title": title, "why": why, "status": status, "detail": detail,
            "fix": fix, "region": region, "optional": optional}


def summarize(checks: list[dict]) -> dict:
    counts = {s: sum(1 for x in checks if x["status"] == s) for s in (OK, WARN, FAIL, INFO, SKIP)}
    blocking = [x for x in checks if x["status"] == FAIL and not x["optional"]]
    return {**counts, "ready": not blocking, "blocking": len(blocking)}


# ----------------------------------------------------------------------------- probes

def _probe(c: Config, sql: str, location: str | None = None) -> str | None:
    """Run a tiny read-only query. None on success, else the error."""
    try:
        bq.query(c, sql, location=location)
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)


def check_identity(c: Config, ctx: dict) -> dict:
    why = "Every step of the tool runs BigQuery queries in its own project."
    try:
        rows = bq.query(c, "SELECT SESSION_USER() AS who")
        ctx["who"] = rows[0]["who"] if rows else None
        return result("run_queries", "Run BigQuery queries", why, OK,
                      f"Running as {ctx['who']} in project {c['project_id']}.")
    except Exception as e:  # noqa: BLE001
        creds = getattr(bq.client(c), "_credentials", None)
        ctx["who"] = getattr(creds, "service_account_email", None) if creds else None
        return result("run_queries", "Run BigQuery queries", why, FAIL, short(e),
                      grant("projects", c["project_id"], ctx.get("who"), "roles/bigquery.jobUser"))


def check_ops_dataset(c: Config, ctx: dict) -> dict:
    from google.api_core.exceptions import NotFound
    title, why = f"Tool dataset ({c['ops_dataset']})", "Where the tool keeps its history, cards and audit trail."
    try:
        ds = bq.client(c).get_dataset(c.ops)
    except NotFound:
        return result("ops_dataset", title, why, FAIL, f"{c.ops} doesn't exist yet.",
                      "python -m optimizer.cli init")
    except Exception as e:  # noqa: BLE001
        return result("ops_dataset", title, why, FAIL, short(e),
                      bq_sql(c["project_id"], grant_sql("SCHEMA", c.ops, ctx.get("who"), "roles/bigquery.dataEditor"),
                             location=regions.home_region(c)))
    home, actual = regions.home_region(c), regions.normalize(ds.location) or ""
    if actual != home:
        return result("ops_dataset", title, why, WARN,
                      f"{c.ops} is in {ds.location}, but the config's home location is {c.get('location')}.",
                      f'Set location: "{ds.location}" in config.yaml (or the BQOPT_LOCATION env var).')
    return result("ops_dataset", title, why, OK, f"{c.ops} exists in {ds.location}.")


def check_ops_write(c: Config, ctx: dict) -> dict:
    from google.api_core.exceptions import NotFound
    title = f"Read and write {c['ops_dataset']}"
    why = "The collector, rules engine and approvals all write to the tool's tables."
    need = ["bigquery.tables.getData", "bigquery.tables.updateData", "bigquery.tables.update"]
    fix_grant = bq_sql(c["project_id"], grant_sql("SCHEMA", c.ops, ctx.get("who"), "roles/bigquery.dataEditor"),
                       location=regions.home_region(c))
    try:
        got = set((bq.client(c).test_iam_permissions(f"{c.ops}.change_sets", need) or {}).get("permissions", []))
    except NotFound:
        return result("ops_write", title, why, SKIP, "The tool's tables aren't created yet.",
                      "python -m optimizer.cli init")
    except Exception as e:  # noqa: BLE001
        return result("ops_write", title, why, FAIL, short(e), fix_grant)
    missing = [p for p in need if p not in got]
    if missing:
        return result("ops_write", title, why, FAIL, "Missing " + ", ".join(missing) + ".", fix_grant)
    return result("ops_write", title, why, OK, "Can read and write the tool's tables.")


def check_discovery(c: Config, ctx: dict, route: list[str], info: dict) -> dict:
    title, why = "Find every region (Cloud Asset Inventory)", (
        'With regions: auto, the tool asks Cloud Asset Inventory where your BigQuery datasets live, '
        "so new regions are picked up automatically.")
    if info.get("mode") != regions.AUTO:
        return result("region_discovery", title, why, INFO,
                      "Regions come from a fixed list in the config: " + ", ".join(route) + ".")
    found = info.get("discovered") or {}
    if info.get("source") == "asset_inventory":
        parts = [f"{r} ({d.get('datasets', 0)} datasets)" for r, d in sorted(found.items())]
        return result("region_discovery", title, why, OK,
                      f"Found {len(found)} region(s) in {info.get('scope')}: " + ", ".join(parts) + ".")
    err = info.get("error") or "discovery failed"
    kind, sid = scope_kind(c)
    if classify(err) == "api_disabled":
        fix = f"gcloud services enable cloudasset.googleapis.com --project={c['project_id']}"
    else:
        fix = grant(kind, sid, ctx.get("who"), "roles/cloudasset.viewer")
    others = [r for r in route if r != regions.home_region(c)]
    status = WARN if others else FAIL
    fallback = (f" Using the last known regions instead: {', '.join(route)}." if others
                else " Only the home location will be collected.")
    return result("region_discovery", title, why, status, short(err) + fallback, fix)


def check_query_history(c: Config, ctx: dict, region: str) -> dict:
    view = str(c.get("jobs_view") or "JOBS").upper()
    org = view.endswith("_BY_ORGANIZATION")
    title = f"Query history in {region}"
    why = ("Query history from every project in the organization (JOBS_BY_ORGANIZATION)." if org
           else "Query history for this project (JOBS).")
    err = _probe(c, f"SELECT 1 AS ok FROM `region-{region}`.INFORMATION_SCHEMA.{view} "
                    f"WHERE creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR) LIMIT 1",
                 location=region)
    if err is None:
        return result(f"query_history:{region}", title, why, OK, f"Can read {view} in {region}.", region=region)
    fix = (grant("organizations", org_id(c), ctx.get("who"), "roles/bigquery.resourceViewer") if org
           else grant("projects", c["project_id"], ctx.get("who"), "roles/bigquery.resourceViewer"))
    return result(f"query_history:{region}", title, why, FAIL, short(err), fix, region=region)


def check_table_metadata(c: Config, ctx: dict, region: str) -> dict:
    title = f"Table details in {region}"
    why = "Table names, columns, partitioning and clustering power the table-level cards."
    err = _probe(c, f"SELECT 1 AS ok FROM `{c['project_id']}.region-{region}`.INFORMATION_SCHEMA.TABLES LIMIT 1",
                 location=region)
    if err is None:
        return result(f"table_metadata:{region}", title, why, OK, f"Can read TABLES in {region}.", region=region)
    return result(f"table_metadata:{region}", title, why, FAIL,
                  short(err) + " (Basic Owner/Viewer roles don't include table metadata.)",
                  grant("projects", c["project_id"], ctx.get("who"), "roles/bigquery.metadataViewer"),
                  region=region)


def check_table_storage(c: Config, ctx: dict, region: str) -> dict:
    title = f"Table sizes in {region}"
    why = "Table sizes (TABLE_STORAGE) price the storage cards. Without them the tool keeps going but skips sizes."
    err = _probe(c, f"SELECT 1 AS ok FROM `{c['project_id']}.region-{region}`.INFORMATION_SCHEMA.TABLE_STORAGE LIMIT 1",
                 location=region)
    if err is None:
        return result(f"table_storage:{region}", title, why, OK, f"TABLE_STORAGE is on in {region}.", region=region)
    if classify(err) == "not_enabled":
        proj_cmd = bq_sql(
            c["project_id"],
            f"ALTER PROJECT `{c['project_id']}` SET OPTIONS (`region-{region}.enable_info_schema_storage` = TRUE);",
            location=region,
        )
        org_cmd = bq_sql(
            c["project_id"],
            f"ALTER ORGANIZATION SET OPTIONS (`region-{region}.enable_info_schema_storage` = TRUE);",
            location=region,
        )
        return result(f"table_storage:{region}", title, why, WARN,
                      f"Table size views are turned off in {region}. A BigQuery admin turns them on once "
                      "(data appears right away; full history takes about a day).",
                      f"{proj_cmd}\n# Or for every project in your organization at once:\n# {org_cmd}",
                      region=region)
    return result(f"table_storage:{region}", title, why, WARN, short(err),
                  grant("projects", c["project_id"], ctx.get("who"), "roles/bigquery.metadataViewer"), region=region)


def check_recommendations(c: Config, ctx: dict, region: str) -> dict:
    view = str(c.get("recs_view") or "RECOMMENDATIONS").upper()
    title = f"Google's recommendations in {region}"
    why = "Google's own partition/cluster recommendations are merged into the cards (optional)."
    err = _probe(c, f"SELECT 1 AS ok FROM `region-{region}`.INFORMATION_SCHEMA.{view} LIMIT 1", location=region)
    if err is None:
        return result(f"recommendations:{region}", title, why, OK, f"Can read {view} in {region}.",
                      region=region, optional=True)
    kind = classify(err)
    if kind in ("not_found", "other"):
        return result(f"recommendations:{region}", title, why, INFO, short(err), region=region, optional=True)
    fix = (grant("organizations", org_id(c), ctx.get("who"), "roles/recommender.bigqueryPartitionClusterViewer")
           if view.endswith("_BY_ORGANIZATION")
           else grant("projects", c["project_id"], ctx.get("who"), "roles/recommender.bigqueryPartitionClusterViewer"))
    return result(f"recommendations:{region}", title, why, WARN, short(err), fix, region=region, optional=True)


def check_reservations(c: Config, ctx: dict, region: str) -> dict:
    admin = c.get("reservation_admin_project")
    title = f"Reservations in {region}"
    why = f"Slot reservations in {admin} price reservation jobs at their edition's slot rate."
    err = _probe(c, f"SELECT 1 AS ok FROM `{admin}.region-{region}`.INFORMATION_SCHEMA.RESERVATIONS LIMIT 1",
                 location=region)
    if err is None:
        return result(f"reservations:{region}", title, why, OK, f"Can read reservations in {admin}.", region=region)
    return result(f"reservations:{region}", title, why, FAIL, short(err),
                  grant("projects", admin, ctx.get("who"), "roles/bigquery.resourceViewer"), region=region)


def check_lookup_table(c: Config, ctx: dict, key: str, label: str) -> dict:
    from google.api_core.exceptions import NotFound
    fqn = str(c.get(key))
    title, why = f"{label} ({fqn})", f"Read by the tool for {label.lower()}."
    cl = bq.client(c)
    try:
        t = cl.get_table(fqn)
        got = set((cl.test_iam_permissions(t, ["bigquery.tables.getData"]) or {}).get("permissions", []))
    except NotFound:
        return result(f"table:{key}", title, why, FAIL, f"Table {fqn} doesn't exist (or isn't visible).",
                      f"Check {key} in config.yaml.")
    except Exception as e:  # noqa: BLE001
        return result(f"table:{key}", title, why, FAIL, short(e),
                      bq_sql(c["project_id"], grant_sql("TABLE", fqn, ctx.get("who"), "roles/bigquery.dataViewer")))
    if "bigquery.tables.getData" not in got:
        return result(f"table:{key}", title, why, FAIL, "Can see the table but can't read its rows.",
                      bq_sql(c["project_id"], grant_sql("TABLE", f"{t.project}.{t.dataset_id}.{t.table_id}",
                                                        ctx.get("who"), "roles/bigquery.dataViewer")))
    return result(f"table:{key}", title, why, OK, f"Can read {t.project}.{t.dataset_id}.{t.table_id}.")


def check_transfers(c: Config, ctx: dict) -> dict:
    title = "Scheduled queries (Data Transfer Service)"
    why = "The tool pauses scheduled queries that write to a table while it rebuilds it (optional)."
    try:
        from google.cloud import bigquery_datatransfer_v1 as dts
        client = dts.DataTransferServiceClient(credentials=getattr(bq.client(c), "_credentials", None))
        pager = client.list_transfer_configs(request={"parent": f"projects/{c['project_id']}", "page_size": 1},
                                             timeout=20)
        next(iter(pager), None)
        return result("transfers", title, why, OK, "Can list scheduled queries.", optional=True)
    except ImportError:
        return result("transfers", title, why, SKIP, "Data Transfer client library not installed.", optional=True)
    except Exception as e:  # noqa: BLE001
        return result("transfers", title, why, WARN, short(e),
                      grant("projects", c["project_id"], ctx.get("who"), "roles/bigquery.user"), optional=True)


def check_cross_project(c: Config, ctx: dict) -> dict:
    home = regions.home_region(c)
    title = "Table details across all projects (org)"
    why = "Coming next: table-level cards for every project, not just the tool's own (optional today)."
    err = _probe(c, f"SELECT 1 AS ok FROM `region-{home}`.INFORMATION_SCHEMA.TABLE_STORAGE_BY_ORGANIZATION LIMIT 1",
                 location=home)
    if err is None:
        return result("cross_project", title, why, OK, "Ready: can read table sizes for the whole organization.",
                      optional=True)
    if classify(err) == "not_enabled":
        fix = bq_sql(c["project_id"], f"ALTER ORGANIZATION SET OPTIONS (`region-{home}.enable_info_schema_storage` = TRUE);",
                     location=home)
    else:
        fix = grant("organizations", org_id(c), ctx.get("who"), "roles/bigquery.metadataViewer")
    return result("cross_project", title, why, INFO, short(err), fix, optional=True)


# ----------------------------------------------------------------------------- runner

def run_checks(c: Config, max_workers: int = 8) -> dict:
    """Run every check. Never raises: a crashing probe becomes a FAIL row."""
    started = time.time()
    ctx: dict = {}
    checks: list[dict] = [check_identity(c, ctx)]
    route, info = regions.plan_route(c, run_id="setup-check", record_result=False)
    checks.append(check_discovery(c, ctx, route, info))

    jobs: list[Callable[[], dict]] = [lambda: check_ops_dataset(c, ctx), lambda: check_ops_write(c, ctx),
                                      lambda: check_transfers(c, ctx)]
    for r in route:
        jobs += [lambda r=r: check_query_history(c, ctx, r), lambda r=r: check_table_metadata(c, ctx, r),
                 lambda r=r: check_table_storage(c, ctx, r), lambda r=r: check_recommendations(c, ctx, r)]
        if c.get("reservation_admin_project"):
            jobs.append(lambda r=r: check_reservations(c, ctx, r))
    for key, label in (("employee_hierarchy_table", "HR hierarchy table"),
                       ("service_account_owner_table", "Service-account owner table"),
                       ("billing_export_table", "Billing export table")):
        if c.get(key):
            jobs.append(lambda key=key, label=label: check_lookup_table(c, ctx, key, label))
    if scope_kind(c)[0] == "organizations":
        jobs.append(lambda: check_cross_project(c, ctx))

    def _safe(fn: Callable[[], dict]) -> dict:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            return result("internal", "Check could not run", "", FAIL, short(e))

    with cf.ThreadPoolExecutor(max_workers=max_workers) as pool:
        checks += list(pool.map(_safe, jobs))

    order = {"run_queries": 0, "ops_dataset": 1, "ops_write": 2, "region_discovery": 3}
    checks.sort(key=lambda x: (x["region"] is not None, route.index(x["region"]) if x["region"] in route else 0,
                               x["optional"], order.get(x["id"], 9)))
    return {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "duration_s": round(time.time() - started, 1), "identity": ctx.get("who"),
            "project_id": c["project_id"], "home": regions.home_region(c), "route": route,
            "mode": info.get("mode"), "checks": checks, "summary": summarize(checks)}


_ICONS = {OK: "✅", WARN: "⚠️ ", FAIL: "❌", INFO: "ℹ️ ", SKIP: "⏭️ "}


def print_report(rep: dict) -> None:
    print(f"[*] Setup check as {rep.get('identity') or 'unknown identity'} in {rep['project_id']} "
          f"(regions: {', '.join(rep['route'])}) — {rep['duration_s']}s")
    for x in rep["checks"]:
        tag = " (optional)" if x["optional"] else ""
        print(f"  {_ICONS.get(x['status'], '  ')} {x['title']}{tag}: {x['detail']}")
        if x["fix"] and x["status"] in (FAIL, WARN, INFO):
            for line in str(x["fix"]).split("\n"):
                print(f"       fix: {line}")
    s = rep["summary"]
    print(f"Summary: {s['ok']} ok, {s['warn']} warnings, {s['fail']} need action, {s['info']} info. "
          + ("Ready to run." if s["ready"] else f"{s['blocking']} blocking issue(s)."))
