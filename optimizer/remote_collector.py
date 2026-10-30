"""Cross-region collection: bring other regions' metadata into the master tables at HQ.

A BigQuery query job runs in exactly one location, so the collector SQL
(sql/02_collector_run.sql) can only collect the HOME location (where optimizer_ops lives).
For every other region on the route (see optimizer/regions.py) this module, step by step:

  1. reads a watermark from the master table at HQ        (query job, home location)
  2. fetches only newer rows from that region's INFORMATION_SCHEMA (query job, that region)
  3. uploads them to a short-lived staging table in optimizer_ops  (load job — not a
     cross-region query, so it is allowed)
  4. MERGEs them into the master table (event tables) or replaces today's rows for the region
     (snapshot tables), then drops the staging table.

No dataset is created outside the home location. The SELECTs mirror sql/02_collector_run.sql
step for step (same output columns, same semantics) — keep the two in sync. One deliberate
difference: hourly slots use the real SUM(period_slot_ms), with no synthetic demo floor.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable

from . import bq, regions
from .config import Config

OVERLAP = dt.timedelta(days=1)      # re-read the last day so jobs still RUNNING last time get final numbers
CHUNK = dt.timedelta(days=1)        # default fetch window (config remote_fetch_chunk_hours); first-run backfills load day by day
RETENTION_DAYS = 180                # INFORMATION_SCHEMA.JOBS* history limit
_JOBS_VIEWS = {"JOBS", "JOBS_BY_PROJECT", "JOBS_BY_USER", "JOBS_BY_FOLDER", "JOBS_BY_ORGANIZATION"}
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9.:-]{0,62}$")


# ----------------------------------------------------------------------------- pure helpers

def windows(start: dt.datetime, end: dt.datetime, step: dt.timedelta = CHUNK) -> list[tuple]:
    """Split [start, end) into consecutive windows of at most `step`."""
    out, s = [], start
    while s < end:
        e = min(s + step, end)
        out.append((s, e))
        s = e
    return out


def floor_hour(t: dt.datetime) -> dt.datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def fetch_start(watermark: dt.datetime | None, now: dt.datetime, backfill_days: int) -> dt.datetime:
    """Where to start fetching: watermark minus the safety overlap, or the first-run backfill;
    never older than INFORMATION_SCHEMA's retention."""
    floor = now - dt.timedelta(days=RETENTION_DAYS)
    start = (watermark - OVERLAP) if watermark else (now - dt.timedelta(days=backfill_days))
    return max(start, floor)


def _src(region: str, project: str | None = None) -> str:
    if project:
        if not _PROJECT_RE.match(project):
            raise ValueError(f"invalid project_id {project!r}")
        return f"`{project}.region-{region}`.INFORMATION_SCHEMA"
    return f"`region-{region}`.INFORMATION_SCHEMA"


def _jobs_view(c: Config) -> str:
    v = str(c.get("jobs_view") or "JOBS").upper()
    if v not in _JOBS_VIEWS:
        raise ValueError(f"unsupported jobs_view {v!r}")
    return v


def jobs_sql(region: str, jobs_view: str) -> str:
    """Mirror of collector step 1 (job events) for one region; params @start, @end."""
    org = jobs_view == "JOBS_BY_ORGANIZATION"   # org view has no query text or dml_statistics
    preview = "CAST(NULL AS STRING)" if org else "SUBSTR(query, 1, 1024)"
    dml = (["CAST(NULL AS INT64)"] * 3 if org else
           ["dml_statistics.inserted_row_count", "dml_statistics.updated_row_count",
            "dml_statistics.deleted_row_count"])
    return f"""
      SELECT
        '{region}' AS region,
        project_id, job_id, parent_job_id, user_email,
        job_type, statement_type, priority, state, cache_hit,
        creation_time, start_time, end_time,
        TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS duration_ms,
        total_bytes_processed, total_bytes_billed, total_slot_ms,
        reservation_id, transaction_id,
        referenced_tables, destination_table, labels,
        query_info.query_hashes.normalized_literals AS query_hash,
        {preview} AS query_preview,
        query_info.resource_warning AS resource_warning,
        TO_JSON_STRING(query_info.performance_insights) AS performance_insights_json,
        TO_JSON_STRING(materialized_view_statistics)    AS mv_statistics_json,
        TO_JSON_STRING(bi_engine_statistics)            AS bi_engine_json,
        TO_JSON_STRING(error_result)                    AS error_json,
        total_modified_partitions,
        {dml[0]} AS dml_inserted, {dml[1]} AS dml_updated, {dml[2]} AS dml_deleted
      FROM {_src(region)}.{jobs_view}
      WHERE creation_time >= TIMESTAMP(@start) AND creation_time < TIMESTAMP(@end)"""


def slots_sql(region: str, jobs_view: str) -> str:
    """Mirror of collector step 2 (hourly slot rollup), real values only; params @start, @end."""
    view = "JOBS_TIMELINE_BY_ORGANIZATION" if jobs_view == "JOBS_BY_ORGANIZATION" else "JOBS_TIMELINE"
    return f"""
      SELECT '{region}' AS region,
             TIMESTAMP_TRUNC(period_start, HOUR) AS hour_ts,
             project_id,
             COALESCE(reservation_id, '(on-demand)') AS reservation_id,
             SUM(period_slot_ms) AS total_slot_ms,
             COUNT(DISTINCT job_id) AS jobs
      FROM {_src(region)}.{view}
      WHERE job_creation_time >= TIMESTAMP_SUB(TIMESTAMP(@start), INTERVAL 1 DAY)
        AND period_start >= TIMESTAMP(@start) AND period_start < TIMESTAMP(@end)
      GROUP BY 1, 2, 3, 4"""


_STORAGE_COLS = [("total_rows", "INT64"), ("total_partitions", "INT64"),
                 ("total_logical_bytes", "INT64"), ("active_logical_bytes", "INT64"),
                 ("long_term_logical_bytes", "INT64"), ("total_physical_bytes", "INT64"),
                 ("active_physical_bytes", "INT64"), ("long_term_physical_bytes", "INT64"),
                 ("time_travel_physical_bytes", "INT64"), ("fail_safe_physical_bytes", "INT64")]


def org_storage_sql(region: str) -> str:
    """One-shot org-wide table sizes in `region` (optional fast path when org metadataViewer +
    enable_info_schema_storage are active; caller falls back to per-project TABLE_STORAGE if not)."""
    cols = ", ".join(n for n, _ in _STORAGE_COLS)
    return f"""
      SELECT project_id, table_schema, table_name,
             {cols},
             storage_last_modified_time AS storage_last_modified
      FROM {_src(region)}.TABLE_STORAGE_BY_ORGANIZATION
      WHERE deleted = FALSE"""


def table_state_sql(region: str, with_storage: bool = True, project: str | None = None) -> str:
    """Mirror of collector step 3; without TABLE_STORAGE (not enabled / not permitted in the
    region) the size columns are NULL instead of skipping the whole snapshot."""
    src = _src(region, project)
    if with_storage:
        sizes = ", ".join(f"s.{n} AS {n}" for n, _ in _STORAGE_COLS)
        sizes += ", s.storage_last_modified_time AS storage_last_modified"
        join = (f"LEFT JOIN {src}.TABLE_STORAGE s ON s.project_id = t.table_catalog "
                f"AND s.table_schema = t.table_schema AND s.table_name = t.table_name AND s.deleted = FALSE")
    else:
        sizes = ", ".join(f"CAST(NULL AS {ty}) AS {n}" for n, ty in _STORAGE_COLS)
        sizes += ", CAST(NULL AS TIMESTAMP) AS storage_last_modified"
        join = ""
    return f"""
      SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region,
             t.table_catalog AS project_id, t.table_schema AS dataset_id, t.table_name AS table_id,
             t.table_type, t.creation_time AS table_created, t.ddl,
             opt.require_partition_filter, opt.partition_expiration_days, opt.expiration_timestamp,
             {sizes},
             @run AS run_id
      FROM {src}.TABLES t
      LEFT JOIN (
        SELECT table_catalog, table_schema, table_name,
               MAX(IF(option_name = 'require_partition_filter',  option_value, NULL)) AS require_partition_filter,
               MAX(IF(option_name = 'partition_expiration_days', option_value, NULL)) AS partition_expiration_days,
               MAX(IF(option_name = 'expiration_timestamp',      option_value, NULL)) AS expiration_timestamp
        FROM {src}.TABLE_OPTIONS
        GROUP BY 1, 2, 3
      ) opt
        ON opt.table_catalog = t.table_catalog AND opt.table_schema = t.table_schema
       AND opt.table_name = t.table_name
      {join}
      WHERE t.table_schema NOT IN (@ops_ds, 'optimizer_ops') AND INSTR(t.table_name, '__bqopt_') = 0"""


def columns_sql(region: str, project: str | None = None) -> str:
    """Mirror of collector step 4."""
    return f"""
      SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region,
             table_catalog AS project_id, table_schema AS dataset_id, table_name AS table_id,
             column_name, data_type, is_partitioning_column, clustering_ordinal_position,
             @run AS run_id
      FROM {_src(region, project)}.COLUMNS
      WHERE table_schema NOT IN (@ops_ds, 'optimizer_ops') AND INSTR(table_name, '__bqopt_') = 0"""


def partitions_sql(region: str, project: str, dataset: str) -> str:
    """Mirror of collector step 5 for one dataset (PARTITIONS is dataset-scoped)."""
    return f"""
      SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region,
             '{project}' AS project_id, table_schema AS dataset_id, table_name AS table_id,
             partition_id, total_rows, total_logical_bytes,
             last_modified_time AS last_modified, storage_tier,
             @run AS run_id
      FROM `{project}.{dataset}.INFORMATION_SCHEMA.PARTITIONS`
      WHERE INSTR(table_name, '__bqopt_') = 0"""


def datasets_sql(region: str, project: str | None = None) -> str:
    """Mirror of collector step 6 (storage_billing_model is backfilled by the driver)."""
    src = _src(region, project)
    return f"""
      SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region,
             s.catalog_name AS project_id, s.schema_name AS dataset_id, s.location,
             s.creation_time AS created, s.last_modified_time AS last_modified,
             COALESCE(o.options, []) AS options,
             CAST(NULL AS STRING) AS storage_billing_model,
             @run AS run_id
      FROM {src}.SCHEMATA s
      LEFT JOIN (
        SELECT catalog_name, schema_name,
               ARRAY_AGG(STRUCT(option_name AS name, option_value AS value)) AS options
        FROM {src}.SCHEMATA_OPTIONS
        GROUP BY 1, 2
      ) o USING (catalog_name, schema_name)
      WHERE s.schema_name NOT IN (@ops_ds, 'optimizer_ops')"""


def objects_sql(region: str, kind: str, project: str | None = None) -> str:
    """Mirror of collector step 7 (raw-JSON snapshots) for one object kind."""
    src = _src(region, project)
    if kind == "TABLE_CONSTRAINT":
        return f"""
          SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region, 'TABLE_CONSTRAINT' AS kind,
                 t.constraint_catalog AS project_id, t.constraint_schema AS dataset_id,
                 t.table_name AS object_id, TO_JSON_STRING(t) AS raw_json, @run AS run_id
          FROM {src}.TABLE_CONSTRAINTS t
          WHERE t.constraint_schema NOT IN (@ops_ds, 'optimizer_ops')"""
    view = {"MATERIALIZED_VIEW": "MATERIALIZED_VIEWS", "VIEW": "VIEWS"}[kind]
    return f"""
      SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region, '{kind}' AS kind,
             t.table_catalog AS project_id, t.table_schema AS dataset_id,
             t.table_name AS object_id, TO_JSON_STRING(t) AS raw_json, @run AS run_id
      FROM {src}.{view} t
      WHERE t.table_schema NOT IN (@ops_ds, 'optimizer_ops')"""


def reservations_sql(region: str, admin_project: str, kind: str) -> str:
    """Mirror of collector step 9 for one reservation-admin view."""
    view = {"RESERVATION": "RESERVATIONS", "ASSIGNMENT": "ASSIGNMENTS",
            "CAPACITY_COMMITMENT": "CAPACITY_COMMITMENTS"}[kind]
    return f"""
      SELECT CURRENT_DATE() AS snapshot_date, '{region}' AS region, '{kind}' AS kind,
             '{admin_project}' AS project_id, CAST(NULL AS STRING) AS dataset_id,
             CAST(NULL AS STRING) AS object_id, TO_JSON_STRING(t) AS raw_json, @run AS run_id
      FROM `{admin_project}.region-{region}`.INFORMATION_SCHEMA.{view} t"""


def recommendations_sql(region: str, recs_view: str) -> str:
    return f"""
      SELECT recommendation_id, '{region}' AS region, recommender, subtype, project_id,
             description, state, last_updated_time,
             TO_JSON_STRING(target_resources)   AS target_resources_json,
             TO_JSON_STRING(additional_details) AS additional_details_json,
             TO_JSON_STRING(t)                  AS raw_json
      FROM {_src(region)}.{recs_view} t"""


def insights_sql(region: str) -> str:
    return f"""
      SELECT insight_id, '{region}' AS region, insight_type AS recommender, project_id, state,
             last_updated_time, TO_JSON_STRING(t) AS raw_json
      FROM {_src(region)}.INSIGHTS t"""


# ----------------------------------------------------------------------------- BigQuery plumbing

def _q(c: Config, sql: str, params: dict | None, region: str) -> list[dict]:
    """Query job in `region`, passing only the @parameters the statement references."""
    used = set(re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", sql))
    return bq.query(c, sql, {k: v for k, v in (params or {}).items() if k in used} or None,
                    location=region)


def _stg(c: Config, table: str, region: str) -> str:
    return f"{c.ops}._stg_{table}_{re.sub(r'[^a-z0-9]', '_', region)}"


def _schema(c: Config, table: str, drop: tuple = ()) -> list:
    return [f for f in bq.client(c).get_table(f"{c.ops}.{table}").schema if f.name not in drop]


def _drop(c: Config, table_fqn: str) -> None:
    bq.client(c).delete_table(table_fqn, not_found_ok=True)


def _watermark(c: Config, table: str, col: str, region: str, extra: str = ""):
    rows = bq.query(c, f"SELECT MAX({col}) AS wm FROM `{c.ops}.{table}` WHERE region = @r {extra}",
                    {"r": region})
    return rows[0]["wm"] if rows and rows[0].get("wm") else None


def _stage_chunks(c: Config, region: str, table: str, sql: str, start: dt.datetime,
                  end: dt.datetime, schema: list) -> tuple[str, int]:
    """Fetch [start, end) window by window from the region; load into one staging table."""
    stg, total = _stg(c, table, region), 0
    step = dt.timedelta(hours=int(c.get("remote_fetch_chunk_hours", 24) or 24))
    for s, e in windows(start, end, step):
        rows = _q(c, sql, {"start": s.isoformat(), "end": e.isoformat()}, region)
        if rows:
            bq.load_rows(c, stg, rows, schema, "WRITE_TRUNCATE" if total == 0 else "WRITE_APPEND")
            total += len(rows)
    return stg, total


def _replace_today(c: Config, region: str, table: str, rows: list[dict],
                   kinds: list[str] | None = None,
                   exclude_project: str | None = None) -> int:
    """Snapshot tables: atomically replace today's rows for this region (and kinds).

    When `exclude_project` is given (home-region cross-project collection), rows belonging to
    `exclude_project` (already written by sql/02_collector_run.sql) are preserved untouched.
    """
    schema = _schema(c, table)
    cols = ", ".join(f"`{f.name}`" for f in schema)
    stg = _stg(c, table, f"{region}_xproj" if exclude_project else region)
    params: dict = {"r": region}
    extra_where = ""
    if exclude_project:
        extra_where += " AND project_id != @excl_proj"
        params["excl_proj"] = exclude_project
    if kinds:
        extra_where += " AND kind IN UNNEST(@kinds)"
        params["kinds"] = list(kinds)
    insert_sql = ""
    if rows:
        bq.load_rows(c, stg, rows, schema)
        insert_sql = f"INSERT INTO `{c.ops}.{table}` ({cols}) SELECT {cols} FROM `{stg}`;"
    bq.execute(c, f"""
        BEGIN TRANSACTION;
        DELETE FROM `{c.ops}.{table}` WHERE snapshot_date = CURRENT_DATE() AND region = @r{extra_where};
        {insert_sql}
        COMMIT TRANSACTION;""", params)
    if rows:
        _drop(c, stg)
    return len(rows)


def projects_for_region(c: Config, region: str, route_info: dict | None = None) -> list[str]:
    """Ordered list of GCP project IDs to scan for table/dataset metadata in `region`.

    Host `c['project_id']` comes first. In organization/folder scope (or JOBS_BY_ORGANIZATION),
    appends every project discovered by Cloud Asset Inventory in `region` — falling back to
    distinct project IDs seen in `jobs_events` when Cloud Asset Inventory wasn't available.
    """
    reg = regions.normalize(region) or region
    host = str(c["project_id"])
    out: list[str] = [host] if _PROJECT_RE.match(host) else []
    if c.get("target_dataset"):
        return out
    scope = str(c.get("asset_scope") or "")
    multi = (scope.startswith(("organizations/", "folders/"))
             or str(c.get("jobs_view") or "").upper() == "JOBS_BY_ORGANIZATION")
    if not multi:
        return out
    by_reg = (route_info or {}).get("projects_by_region") or {}
    discovered = by_reg.get(reg)
    if discovered is not None:
        for p in discovered:
            if _PROJECT_RE.match(p) and p not in out:
                out.append(p)
        return out
    # Fallback when Cloud Asset Inventory failed or regions came from a static config list:
    # find other projects that ran queries or owned referenced tables in this region.
    try:
        rows = bq.query(c, f"""
            SELECT DISTINCT pid FROM (
              SELECT project_id AS pid FROM `{c.ops}.jobs_events` WHERE region = @r
              UNION DISTINCT
              SELECT rt.project_id AS pid FROM `{c.ops}.jobs_events`, UNNEST(referenced_tables) rt
              WHERE region = @r
            ) WHERE pid IS NOT NULL""", {"r": reg})
        for r in rows:
            p = str(r.get("pid") or "")
            if _PROJECT_RE.match(p) and p not in out:
                out.append(p)
    except Exception:
        pass
    return out


def _fetch_org_storage_map(c: Config, region: str) -> dict[tuple[str, str, str], dict] | None:
    """Try reading TABLE_STORAGE_BY_ORGANIZATION once for `region`; None if not enabled/permitted."""
    try:
        rows = _q(c, org_storage_sql(region), None, region)
    except Exception:  # noqa: BLE001
        return None
    return {(r["project_id"], r["table_schema"], r["table_name"]): r for r in rows}


def _overlay_storage(rows: list[dict], org_map: dict[tuple[str, str, str], dict]) -> None:
    storage_keys = [n for n, _ in _STORAGE_COLS] + ["storage_last_modified"]
    for r in rows:
        hit = org_map.get((r.get("project_id"), r.get("dataset_id"), r.get("table_id")))
        if hit:
            for k in storage_keys:
                r[k] = hit.get(k)


def _proj_arg_for(c: Config, proj: str, projs: list[str], force_qualify: bool = False) -> str | None:
    if not force_qualify and len(projs) == 1 and proj == c["project_id"]:
        return None
    return proj


# ----------------------------------------------------------------------------- steps

def collect_jobs(c: Config, region: str, run_id: str, now: dt.datetime | None = None) -> tuple:
    now = now or dt.datetime.now(dt.timezone.utc)
    start = fetch_start(_watermark(c, "jobs_events", "creation_time", region), now,
                        int(c.get("initial_backfill_days", 180)))
    schema = _schema(c, "jobs_events", drop=("collected_at", "run_id"))
    stg, n = _stage_chunks(c, region, "jobs_events", jobs_sql(region, _jobs_view(c)), start, now, schema)
    if n:
        cols = [f.name for f in schema]
        bq.execute(c, f"""
            MERGE `{c.ops}.jobs_events` T
            USING (
              SELECT * EXCEPT(_rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY region, project_id, job_id
                                             ORDER BY end_time DESC) AS _rn
                FROM `{stg}`) WHERE _rn = 1
            ) S
            ON  T.region = S.region AND T.project_id = S.project_id AND T.job_id = S.job_id
            AND T.creation_time >= TIMESTAMP_SUB(TIMESTAMP(@start), INTERVAL 1 DAY)
            WHEN MATCHED THEN UPDATE SET
              state = S.state, end_time = S.end_time, duration_ms = S.duration_ms,
              total_bytes_processed = S.total_bytes_processed,
              total_bytes_billed    = S.total_bytes_billed,
              total_slot_ms         = S.total_slot_ms,
              error_json            = S.error_json,
              collected_at          = CURRENT_TIMESTAMP(),
              run_id                = @run
            WHEN NOT MATCHED THEN INSERT ({", ".join(cols)}, collected_at, run_id)
            VALUES ({", ".join("S." + x for x in cols)}, CURRENT_TIMESTAMP(), @run)""",
            {"start": start.isoformat(), "run": run_id})
        _drop(c, stg)
    return n, f"window from {start:%Y-%m-%d %H:%M} UTC"


def collect_hourly_slots(c: Config, region: str, run_id: str, now: dt.datetime | None = None) -> tuple:
    now = now or dt.datetime.now(dt.timezone.utc)
    wm = _watermark(c, "jobs_hourly_slots", "hour_ts", region, "AND run_id != 'demo-seed'")
    start = floor_hour(fetch_start(wm, now, int(c.get("initial_backfill_days", 180))))
    schema = _schema(c, "jobs_hourly_slots", drop=("collected_at", "run_id"))
    stg, n = _stage_chunks(c, region, "jobs_hourly_slots", slots_sql(region, _jobs_view(c)),
                           start, now, schema)
    if n:
        bq.execute(c, f"""
            MERGE `{c.ops}.jobs_hourly_slots` T
            USING (
              SELECT region, hour_ts, project_id, reservation_id,
                     SUM(total_slot_ms) AS total_slot_ms, MAX(jobs) AS jobs
              FROM `{stg}` GROUP BY 1, 2, 3, 4
            ) S
            ON  T.region = S.region AND T.hour_ts = S.hour_ts
            AND T.project_id = S.project_id AND T.reservation_id = S.reservation_id
            AND T.hour_ts >= TIMESTAMP_SUB(TIMESTAMP(@start), INTERVAL 1 DAY)
            WHEN MATCHED AND T.run_id != 'demo-seed' THEN UPDATE SET
              total_slot_ms = GREATEST(T.total_slot_ms, S.total_slot_ms),
              jobs = GREATEST(T.jobs, S.jobs),
              collected_at = CURRENT_TIMESTAMP(), run_id = @run
            WHEN NOT MATCHED THEN INSERT
              (region, hour_ts, project_id, reservation_id, total_slot_ms, jobs, collected_at, run_id)
            VALUES (S.region, S.hour_ts, S.project_id, S.reservation_id, S.total_slot_ms, S.jobs,
                    CURRENT_TIMESTAMP(), @run)""",
            {"start": start.isoformat(), "run": run_id})
        _drop(c, stg)
    return n, None


def collect_table_state(c: Config, region: str, run_id: str,
                        projects: list[str] | None = None,
                        exclude_project: str | None = None) -> tuple:
    projs = list(projects) if projects is not None else [c["project_id"]]
    if not projs:
        return 0, "0 projects to scan"
    params = {"run": run_id, "ops_ds": c["ops_dataset"]}
    force_qualify = exclude_project is not None or any(p != c["project_id"] for p in projs)
    org_storage = _fetch_org_storage_map(c, region) if force_qualify else None
    rows: list[dict] = []
    ok_projs, skipped_projs, no_storage = 0, [], []
    for proj in projs:
        p_arg = _proj_arg_for(c, proj, projs, force_qualify)
        try:
            if org_storage is not None:
                p_rows = _q(c, table_state_sql(region, False, p_arg), params, region)
                _overlay_storage(p_rows, org_storage)
            else:
                try:
                    p_rows = _q(c, table_state_sql(region, True, p_arg), params, region)
                except Exception as se:  # noqa: BLE001 — TABLE_STORAGE off/denied: keep tables, drop sizes
                    p_rows = _q(c, table_state_sql(region, False, p_arg), params, region)
                    no_storage.append((proj, se))
            rows.extend(p_rows)
            ok_projs += 1
        except Exception as pe:  # noqa: BLE001 — one locked-down project never aborts the rest
            skipped_projs.append((proj, pe))
    if ok_projs == 0 and skipped_projs:
        raise skipped_projs[0][1]
    if not force_qualify:
        note = (f"sizes unavailable (TABLE_STORAGE not enabled or not permitted here): "
                f"{str(no_storage[0][1])[:200]}") if no_storage else None
    else:
        parts = [f"{ok_projs}/{len(projs)} project(s) scanned"]
        if org_storage is not None:
            parts.append("org TABLE_STORAGE")
        elif no_storage:
            parts.append(f"sizes off in {len(no_storage)} project(s)")
        if skipped_projs:
            parts.append(f"skipped {len(skipped_projs)}: {', '.join(p for p, _ in skipped_projs[:5])}")
        note = "; ".join(parts)
    n = _replace_today(c, region, "table_state_daily", rows, exclude_project=exclude_project)
    return n, note


def collect_columns(c: Config, region: str, run_id: str,
                    projects: list[str] | None = None,
                    exclude_project: str | None = None) -> tuple:
    projs = list(projects) if projects is not None else [c["project_id"]]
    if not projs:
        return 0, "0 projects to scan"
    params = {"run": run_id, "ops_ds": c["ops_dataset"]}
    force_qualify = exclude_project is not None or any(p != c["project_id"] for p in projs)
    rows, ok_projs, skipped_projs = [], 0, []
    for proj in projs:
        p_arg = _proj_arg_for(c, proj, projs, force_qualify)
        try:
            rows.extend(_q(c, columns_sql(region, p_arg), params, region))
            ok_projs += 1
        except Exception as pe:  # noqa: BLE001
            skipped_projs.append((proj, pe))
    if ok_projs == 0 and skipped_projs:
        raise skipped_projs[0][1]
    note = None
    if force_qualify:
        note = f"{ok_projs}/{len(projs)} project(s) scanned" + (
            f"; skipped {len(skipped_projs)}: {', '.join(p for p, _ in skipped_projs[:5])}"
            if skipped_projs else "")
    return _replace_today(c, region, "columns_daily", rows, exclude_project=exclude_project), note


def collect_partitions(c: Config, region: str, run_id: str,
                       projects: list[str] | None = None,
                       exclude_project: str | None = None) -> tuple:
    projs = list(projects) if projects is not None else [c["project_id"]]
    if not projs:
        return 0, "0 projects to scan"
    force_qualify = exclude_project is not None or any(p != c["project_id"] for p in projs)
    rows, total_ds, skipped_ds, ok_projs, skipped_projs = [], 0, 0, 0, []
    for proj in projs:
        p_arg = _proj_arg_for(c, proj, projs, force_qualify)
        try:
            datasets = [
                r["schema_name"] for r in _q(
                    c, f"SELECT schema_name FROM {_src(region, p_arg)}.SCHEMATA "
                       f"WHERE schema_name NOT IN ('INFORMATION_SCHEMA', @ops_ds, 'optimizer_ops')",
                    {"ops_ds": c["ops_dataset"]}, region)
                if _IDENT_RE.match(r["schema_name"] or "")
            ]
            ok_projs += 1
        except Exception as pe:  # noqa: BLE001
            skipped_projs.append((proj, pe))
            continue
        total_ds += len(datasets)
        for ds in datasets:
            try:
                rows += _q(c, partitions_sql(region, proj, ds), {"run": run_id}, region)
            except Exception:  # noqa: BLE001 — one unreadable dataset never fails the step
                skipped_ds += 1
    if ok_projs == 0 and skipped_projs:
        raise skipped_projs[0][1]
    n = _replace_today(c, region, "table_partitions_daily", rows, exclude_project=exclude_project)
    msg = f"{total_ds} datasets scanned" + (f", {skipped_ds} skipped" if skipped_ds else "")
    if force_qualify:
        msg += f" across {ok_projs}/{len(projs)} project(s)"
        if skipped_projs:
            msg += f" (skipped projects: {', '.join(p for p, _ in skipped_projs[:5])})"
    return n, msg


def collect_dataset_state(c: Config, region: str, run_id: str,
                          projects: list[str] | None = None,
                          exclude_project: str | None = None) -> tuple:
    projs = list(projects) if projects is not None else [c["project_id"]]
    if not projs:
        return 0, "0 projects to scan"
    params = {"run": run_id, "ops_ds": c["ops_dataset"]}
    force_qualify = exclude_project is not None or any(p != c["project_id"] for p in projs)
    rows, ok_projs, skipped_projs = [], 0, []
    for proj in projs:
        p_arg = _proj_arg_for(c, proj, projs, force_qualify)
        try:
            rows.extend(_q(c, datasets_sql(region, p_arg), params, region))
            ok_projs += 1
        except Exception as pe:  # noqa: BLE001
            skipped_projs.append((proj, pe))
    if ok_projs == 0 and skipped_projs:
        raise skipped_projs[0][1]
    note = None
    if force_qualify:
        note = f"{ok_projs}/{len(projs)} project(s) scanned" + (
            f"; skipped {len(skipped_projs)}: {', '.join(p for p, _ in skipped_projs[:5])}"
            if skipped_projs else "")
    return _replace_today(c, region, "dataset_state_daily", rows, exclude_project=exclude_project), note


def collect_objects(c: Config, region: str, run_id: str,
                    projects: list[str] | None = None,
                    exclude_project: str | None = None) -> tuple:
    projs = list(projects) if projects is not None else [c["project_id"]]
    if not projs:
        return 0, "0 projects to scan"
    params = {"run": run_id, "ops_ds": c["ops_dataset"]}
    force_qualify = exclude_project is not None or any(p != c["project_id"] for p in projs)
    rows, kinds_seen, skipped_kinds = [], set(), set()
    for proj in projs:
        p_arg = _proj_arg_for(c, proj, projs, force_qualify)
        for kind in ("MATERIALIZED_VIEW", "VIEW", "TABLE_CONSTRAINT"):
            try:
                rows += _q(c, objects_sql(region, kind, p_arg), params, region)
                kinds_seen.add(kind)
            except Exception:  # noqa: BLE001
                skipped_kinds.add(kind)
    kinds = [k for k in ("MATERIALIZED_VIEW", "VIEW", "TABLE_CONSTRAINT") if k in kinds_seen]
    if not kinds:
        raise RuntimeError("no object views readable in this region")
    n = _replace_today(c, region, "object_state_raw_daily", rows, kinds, exclude_project=exclude_project)
    missed = [k for k in ("MATERIALIZED_VIEW", "VIEW", "TABLE_CONSTRAINT") if k not in kinds_seen and k in skipped_kinds]
    return n, ("skipped " + ", ".join(missed)) if missed else None


def collect_reservations(c: Config, region: str, run_id: str) -> tuple:
    admin = str(c.get("reservation_admin_project") or "")
    if not _PROJECT_RE.match(admin):
        raise ValueError(f"invalid reservation_admin_project {admin!r}")
    rows, kinds = [], ["RESERVATION", "ASSIGNMENT", "CAPACITY_COMMITMENT"]
    for kind in kinds:
        rows += _q(c, reservations_sql(region, admin, kind), {"run": run_id}, region)
    return _replace_today(c, region, "object_state_raw_daily", rows, kinds), None


def _merge_by_id(c: Config, region: str, table: str, id_col: str, rows: list[dict],
                 update_cols: list[str], run_id: str) -> int:
    """Recommender tables: upsert on the id (first_seen_at / last_seen_at maintained here)."""
    if not rows:
        return 0
    schema = _schema(c, table, drop=("first_seen_at", "last_seen_at", "run_id"))
    cols = [f.name for f in schema]
    stg = _stg(c, table, region)
    bq.load_rows(c, stg, rows, schema)
    sets = ", ".join(f"{x} = S.{x}" for x in update_cols)
    bq.execute(c, f"""
        MERGE `{c.ops}.{table}` T
        USING (SELECT * FROM `{stg}` WHERE TRUE
               QUALIFY ROW_NUMBER() OVER (PARTITION BY {id_col}) = 1) S
        ON T.{id_col} = S.{id_col}
        WHEN MATCHED THEN UPDATE SET {sets}, last_seen_at = CURRENT_TIMESTAMP(), run_id = @run
        WHEN NOT MATCHED THEN INSERT ({", ".join(cols)}, first_seen_at, last_seen_at, run_id)
        VALUES ({", ".join("S." + x for x in cols)}, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), @run)""",
        {"run": run_id})
    _drop(c, stg)
    return len(rows)


def collect_recommendations(c: Config, region: str, run_id: str) -> tuple:
    explicit = c.get("recs_view")
    if explicit:
        view = str(explicit).upper()
        if view not in ("RECOMMENDATIONS", "RECOMMENDATIONS_BY_ORGANIZATION"):
            raise ValueError(f"unsupported recs_view {view!r}")
        rows = _q(c, recommendations_sql(region, view), None, region)
        note = None
    elif str(c.get("jobs_view") or "").upper() == "JOBS_BY_ORGANIZATION":
        try:
            rows = _q(c, recommendations_sql(region, "RECOMMENDATIONS_BY_ORGANIZATION"), None, region)
            note = "RECOMMENDATIONS_BY_ORGANIZATION"
        except Exception:  # noqa: BLE001 — fall back to project-level recommendations
            rows = _q(c, recommendations_sql(region, "RECOMMENDATIONS"), None, region)
            note = "fallback to project RECOMMENDATIONS"
    else:
        rows = _q(c, recommendations_sql(region, "RECOMMENDATIONS"), None, region)
        note = None
    return _merge_by_id(c, region, "recommender_recommendations", "recommendation_id", rows,
                        ["state", "last_updated_time", "additional_details_json", "raw_json"], run_id), note


def collect_insights(c: Config, region: str, run_id: str) -> tuple:
    rows = _q(c, insights_sql(region), None, region)
    return _merge_by_id(c, region, "recommender_insights", "insight_id", rows,
                        ["state", "last_updated_time", "raw_json"], run_id), None


def _write_audit(c: Config, run_id: str, region: str, entries: list[tuple]) -> None:
    """One collector_audit row per step (same table the collector SQL writes to)."""
    if not entries:
        return
    params: dict = {"run": run_id, "reg": region}
    values = []
    for i, (step, status, n, msg) in enumerate(entries):
        params.update({f"s{i}": step, f"st{i}": status, f"n{i}": None if n is None else int(n),
                       f"m{i}": None if msg is None else str(msg)[:1000]})
        values.append(f"(@run, CURRENT_TIMESTAMP(), @s{i}, @reg, @st{i}, SAFE_CAST(@n{i} AS INT64), @m{i})")
    try:
        bq.execute(c, f"INSERT INTO `{c.ops}.collector_audit` "
                      f"(run_id, `at`, step, region, status, row_count, message) VALUES {', '.join(values)}",
                   params)
    except Exception as e:  # noqa: BLE001 — auditing must never fail the collection
        print(f"  ! could not write collector_audit for {region}: {e}")


def collect_extra_projects_home(c: Config, home: str, extra_projects: list[str], run_id: str) -> dict:
    """Collect table/column/partition/dataset/object metadata for non-host projects in the HOME
    region (after sql/02_collector_run.sql has collected the host project).

    Replaces only rows with `project_id != c['project_id']` for today in `home`, so re-runs are
    100% idempotent and the host project's snapshot is never touched.
    """
    reg = regions.normalize(home)
    if not reg:
        raise ValueError(f"invalid region {home!r}")
    projs = [p for p in extra_projects if _PROJECT_RE.match(p) and p != c["project_id"]]
    if not projs:
        return {}
    host = str(c["project_id"])
    steps: list[tuple[str, Callable[[], tuple]]] = [
        ("3_table_state_xproj", lambda: collect_table_state(c, reg, run_id, projs, exclude_project=host)),
        ("4_columns_xproj", lambda: collect_columns(c, reg, run_id, projs, exclude_project=host)),
        ("5_partitions_xproj", lambda: collect_partitions(c, reg, run_id, projs, exclude_project=host)),
        ("6_dataset_state_xproj", lambda: collect_dataset_state(c, reg, run_id, projs, exclude_project=host)),
        ("7_objects_xproj", lambda: collect_objects(c, reg, run_id, projs, exclude_project=host)),
    ]
    results, audit = {}, []
    for name, fn in steps:
        try:
            n, note = fn()
            results[name] = n
            audit.append((name, "OK", n, note))
        except Exception as e:  # noqa: BLE001
            results[name] = f"SKIPPED: {str(e)[:160]}"
            audit.append((name, "SKIPPED", 0, str(e)[:1000]))
    _write_audit(c, run_id, reg, audit)
    return results


def collect_region(c: Config, region: str, run_id: str,
                   projects: list[str] | None = None) -> dict:
    """Run every collector step for one non-home region. Each step is isolated: a failure is
    recorded as SKIPPED in collector_audit and the remaining steps still run."""
    reg = regions.normalize(region)
    if not reg:
        raise ValueError(f"invalid region {region!r}")
    if reg == regions.home_region(c):
        raise ValueError(f"{reg} is the home location; the collector SQL handles it directly")
    projs = list(projects) if projects is not None else [c["project_id"]]
    steps: list[tuple[str, Callable[[], tuple]]] = [
        ("1_jobs_events", lambda: collect_jobs(c, reg, run_id)),
        ("2_hourly_slots", lambda: collect_hourly_slots(c, reg, run_id)),
        ("3_table_state", lambda: collect_table_state(c, reg, run_id, projs)),
        ("4_columns", lambda: collect_columns(c, reg, run_id, projs)),
        ("5_partitions", lambda: collect_partitions(c, reg, run_id, projs)),
        ("6_dataset_state", lambda: collect_dataset_state(c, reg, run_id, projs)),
        ("7_objects", lambda: collect_objects(c, reg, run_id, projs)),
        ("8_recommendations", lambda: collect_recommendations(c, reg, run_id)),
        ("8_insights", lambda: collect_insights(c, reg, run_id)),
    ]
    if c.get("reservation_admin_project"):
        steps.append(("9_reservations", lambda: collect_reservations(c, reg, run_id)))
    results, audit = {}, []
    for name, fn in steps:
        try:
            n, note = fn()
            results[name] = n
            audit.append((name, "OK", n, note))
        except Exception as e:  # noqa: BLE001
            results[name] = f"SKIPPED: {str(e)[:160]}"
            audit.append((name, "SKIPPED", 0, str(e)[:1000]))
    _write_audit(c, run_id, reg, audit)
    return results
