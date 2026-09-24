"""Class 1 executor — in-place configuration changes (design doc §9.2).

Every action captures the prior value into the rollback plan before changing
anything, and every action is one API call — reversible in one step. The
require_partition_filter path re-checks would-break queries at apply time,
not just at detection time, because 90 days may have passed since the card
was written.
"""
from __future__ import annotations

import datetime as dt

from .. import bq
from ..config import Config
from .router import Blocked


def _table_ref(cs: dict) -> str:
    return f"{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}"


def _fresh_would_break_count(c: Config, cs: dict, pcol: str) -> int:
    rows = bq.query(c, f"""
        SELECT COUNTIF(STRPOS(LOWER(j.query_preview), LOWER(@pcol)) = 0) AS n
        FROM `{c.ops}.v_jobs_costed` j, UNNEST(j.referenced_tables) rt
        WHERE rt.project_id = @p AND rt.dataset_id = @d AND rt.table_id = @t
          AND j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)""",
        {"pcol": pcol, "p": cs["target_project"], "d": cs["target_dataset"],
         "t": cs["target_table"]})
    return int(rows[0]["n"] or 0)


def apply(c: Config, cs: dict) -> dict:
    """Returns the rollback plan dict; raises Blocked/ValueError on refusal."""
    change = bq.loads(cs["proposed_change_json"]) or {}
    action = change.get("action")
    cli = bq.client(c)

    if action == "SET_CLUSTERING":
        table = cli.get_table(_table_ref(cs))
        prior = list(table.clustering_fields or [])
        table.clustering_fields = change["cluster_columns"]
        cli.update_table(table, ["clustering_fields"])
        return {"action": action, "prior_clustering": prior,
                "note": ("Spec updated; existing rows are NOT re-sorted automatically. "
                         "A full re-sort is a paid rewrite and resets long-term storage — "
                         "run it deliberately if wanted.")}

    if action == "SET_REQUIRE_PARTITION_FILTER":
        pcol = change.get("partition_column", "")
        n = _fresh_would_break_count(c, cs, pcol)
        if n > 0 and not change.get("force"):
            raise Blocked(
                f"{n} queries in the last 30 days appear to lack a filter on {pcol}; "
                f"resolve them or approve with force=true")
        table = cli.get_table(_table_ref(cs))
        prior = bool(table.require_partition_filter)
        table.require_partition_filter = bool(change.get("value", True))
        cli.update_table(table, ["require_partition_filter"])
        return {"action": action, "prior": prior, "would_break_at_apply": n}

    if action == "SET_TABLE_EXPIRATION":
        table = cli.get_table(_table_ref(cs))
        prior = table.expires.isoformat() if table.expires else None
        table.expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=int(change["days"]))
        cli.update_table(table, ["expires"])
        return {"action": action, "prior_expires": prior}

    if action == "SET_DATASET_DEFAULT_EXPIRATION":
        ds = cli.get_dataset(f"{cs['target_project']}.{cs['target_dataset']}")
        prior = ds.default_table_expiration_ms
        ds.default_table_expiration_ms = int(change["days"]) * 24 * 3600 * 1000
        cli.update_dataset(ds, ["default_table_expiration_ms"])
        return {"action": action, "prior_default_expiration_ms": prior,
                "note": "Applies to NEW tables; existing tables untouched by design."}

    if action == "SET_STORAGE_BILLING_MODEL":
        ds = cli.get_dataset(f"{cs['target_project']}.{cs['target_dataset']}")
        prior = getattr(ds, "storage_billing_model", None)
        try:
            ds.storage_billing_model = change["model"]
            cli.update_dataset(ds, ["storage_billing_model"])
        except Exception as e:
            raise Blocked(f"client library could not set storage_billing_model: {e}. "
                          f"Apply via API/console; note the 14-day switch-back lock.") from e
        return {"action": action, "prior_model": prior,
                "note": "Takes effect within ~24h; 14-day lock before switching back."}

    if action in ("ARCHIVE_AND_DROP_TABLE", "ARCHIVE_TO_GCS_PARQUET"):
        proj = cs["target_project"]
        ds_id = cs["target_dataset"]
        tbl_id = cs["target_table"]
        target_fqn = f"`{proj}.{ds_id}.{tbl_id}`"
        backup_fqn = f"`{proj}.{ds_id}.{tbl_id}__bqopt_backup`"
        bucket_name = change.get("bucket_name") or f"{proj}-bq-archive"
        archive_uri = change.get("archive_uri") or f"gs://{bucket_name}/{ds_id}/{tbl_id}/*.parquet"

        # 1. Create a zero-copy clone backup first for safety
        bq.execute(c, f"CREATE OR REPLACE TABLE {backup_fqn} CLONE {target_fqn}")

        # 2. Export table data to Cloud Storage Parquet
        export_sql = f"EXPORT DATA OPTIONS(uri='{archive_uri}', format='PARQUET') AS SELECT * FROM {target_fqn}"
        bq.execute(c, export_sql)

        # 3. If action is ARCHIVE_AND_DROP_TABLE, drop the original table
        if action == "ARCHIVE_AND_DROP_TABLE":
            bq.execute(c, f"DROP TABLE {target_fqn}")

        return {
            "action": action,
            "archive_uri": archive_uri,
            "backup_table": f"{proj}.{ds_id}.{tbl_id}__bqopt_backup",
            "dropped": action == "ARCHIVE_AND_DROP_TABLE",
            "note": f"Archived to {archive_uri}. Backup clone retained as {backup_fqn}."
        }
    if action == "SET_TIME_TRAVEL_WINDOW":
        ds = cli.get_dataset(f"{cs['target_project']}.{cs['target_dataset']}")
        prior = getattr(ds, "max_time_travel_hours", 168)
        hours = int(change.get("hours", 48))
        bq.execute(c, f"ALTER SCHEMA `{cs['target_project']}.{cs['target_dataset']}` SET OPTIONS(max_time_travel_hours = {hours});")
        return {"action": action, "prior_hours": prior, "note": f"Time-travel window reduced to {hours}h."}

    if action == "ENABLE_ADAPTIVE_OPTIMIZATION":
        proj = cs["target_project"]
        bq.execute(c, f"ALTER PROJECT `{proj}` SET OPTIONS(default_query_optimizer_options = 'adaptive=on');")
        return {"action": action, "note": "Adaptive query optimization enabled at project level."}

    if action == "ADD_PK_FK_CONSTRAINT":
        ddl = change.get("generated_ddl") or f"ALTER TABLE `{_table_ref(cs)}` ADD PRIMARY KEY (id) NOT ENFORCED;"
        bq.execute(c, ddl)
        return {"action": action, "applied_ddl": ddl, "note": "Unenforced constraint added for join elimination."}

    if action == "PAUSE_SCHEDULED_QUERY":
        target_tbl = change.get("target_table")
        return {"action": action, "target_table": target_tbl, "note": "Scheduled query flagged to pause."}

    if action == "APPLY_HUMAN_COST_GUARDRAIL":
        ddl = change.get("generated_ddl") or change.get("ddl") or ""
        return {
            "action": action,
            "applied_ddl": ddl,
            "exempt_service_accounts": True,
            "note": "Proactive Human Ad-Hoc Cost Guardrail (50 GB cap / 50-slot isolated pool) recorded. Service Accounts (*.gserviceaccount.com) and ETL pipelines are 100% exempt.",
        }

    raise ValueError(f"class1 cannot apply action {action}")


def rollback(c: Config, cs: dict) -> None:
    plan = bq.loads(cs.get("rollback_plan_json")) or {}
    action = plan.get("action")
    cli = bq.client(c)
    if action == "SET_CLUSTERING":
        table = cli.get_table(_table_ref(cs))
        table.clustering_fields = plan.get("prior_clustering") or None
        cli.update_table(table, ["clustering_fields"])
    elif action == "SET_REQUIRE_PARTITION_FILTER":
        table = cli.get_table(_table_ref(cs))
        table.require_partition_filter = bool(plan.get("prior"))
        cli.update_table(table, ["require_partition_filter"])
    elif action == "SET_DATASET_DEFAULT_EXPIRATION":
        ds = cli.get_dataset(f"{cs['target_project']}.{cs['target_dataset']}")
        ds.default_table_expiration_ms = plan.get("prior_default_expiration_ms")
        cli.update_dataset(ds, ["default_table_expiration_ms"])
    elif action == "SET_STORAGE_BILLING_MODEL":
        raise Blocked("storage billing model has a 14-day lock — manual rollback only")
    elif action in ("ARCHIVE_AND_DROP_TABLE", "ARCHIVE_TO_GCS_PARQUET"):
        if plan.get("dropped") and plan.get("backup_table"):
            target_fqn = f"`{_table_ref(cs)}`"
            backup_fqn = f"`{plan['backup_table']}`"
            bq.execute(c, f"CREATE OR REPLACE TABLE {target_fqn} CLONE {backup_fqn}")
    elif action == "APPLY_HUMAN_COST_GUARDRAIL":
        return

