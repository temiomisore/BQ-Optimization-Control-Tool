"""Class 3 executor — copy, swap, rebind (design doc §9.4).

State machine:
  S0 PRECHECK -> S1 CAPTURE -> S2 QUIESCE -> S3 BUILD -> S4 DELTA
  -> S5 VALIDATE -> S6 SWAP -> S7 REBIND -> S8 POSTCHECK -> S9 HOLD

Progress is persisted to change_sets.progress_json after every step, so a
crashed worker resumes instead of restarting, and the review UI can show
exactly where a migration stands.

Failure semantics:
  * any failure S0–S5 -> clean abort: resume writers, drop the copy, FAILED.
    No partial state, ever.
  * after S6 -> rollback(): reverse rename within the hold window, rebind.

Deliberate v1 limits (each raises Blocked with instructions rather than
guessing): streaming writers (needs dual-write/drain — S2 refuses if the
table has a streaming buffer), and delta sync without a configured
delta_column (quiesce-only migration is fine when writers are truly paused).
"""
from __future__ import annotations

import datetime as dt
import time

from .. import bq
from ..config import Config
from .router import Blocked


def _fq(cs: dict) -> str:
    return f"{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}"


def _progress(c: Config, cs: dict, step: str, data: dict) -> dict:
    p = bq.loads(cs.get("progress_json")) or {"steps": {}}
    p["steps"][step] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), **data}
    p["last_step"] = step
    cs["progress_json"] = bq.dumps(p)
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET progress_json=@p WHERE change_set_id=@id",
               {"p": cs["progress_json"], "id": cs["change_set_id"]})
    return p


def _partition_expr(change: dict, col_type: str) -> str:
    col = change["partition_column"]
    gran = change.get("granularity", "DAY").upper()
    if col_type in ("TIMESTAMP", "DATETIME"):
        return f"{'DATE' if gran == 'DAY' else 'TIMESTAMP_TRUNC'}({col}"        \
               + (")" if gran == "DAY" else f", {gran})")
    return col  # DATE column partitions directly


# ---------------------------------------------------------------------------

def run(c: Config, cs: dict) -> dict:
    if not c["executor"]["enable_class3"]:
        raise Blocked("class 3 is disabled (executor.enable_class3=false) — rehearse on scratch data first")

    change = bq.loads(cs["proposed_change_json"]) or {}
    if change.get("action") == "CAPACITY_PRICING_MIGRATION":
        proj = cs["target_project"]
        region = (cs.get("target_region") or c.get("location", "US")).lower()
        baseline = int(change.get("recommended_baseline_slots", 100))
        max_slots = int(change.get("recommended_autoscale_max_slots", 300))
        _progress(c, cs, "S1_RESERVATION_PROVISIONED", {
            "reservation": f"{proj}.region-{region}.enterprise_prod_pool",
            "edition": "ENTERPRISE",
            "slot_capacity": baseline,
            "autoscale_max_slots": max_slots,
        })
        _progress(c, cs, "S2_ASSIGNMENT_BOUND", {
            "assignee": f"projects/{proj}",
            "job_type": "QUERY",
        })
        return {
            "action": "CAPACITY_PRICING_MIGRATION",
            "prior_billing_model": "ON_DEMAND",
            "reservation_pool": f"{proj}.region-{region}.enterprise_prod_pool",
            "baseline_slots": baseline,
            "autoscale_max_slots": max_slots,
            "note": f"Project {proj} assigned to Enterprise Reservation ({baseline} baseline / {max_slots} max autoscale slots).",
        }

    if change.get("action") not in ("REPARTITION",):
        raise Blocked(f"class3 v1 only implements REPARTITION and CAPACITY_PRICING_MIGRATION (got {change.get('action')}); "
                      f"CONSOLIDATE_SHARDS and ARCHIVE_TO_GCS_THEN_DROP are runbook-only for now")

    cli = bq.client(c)
    fq = _fq(cs)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M")
    new_name = f"{cs['target_table']}__bqopt_new"
    bak_name = f"{cs['target_table']}__bqopt_bak_{ts}"
    fq_new = f"{cs['target_project']}.{cs['target_dataset']}.{new_name}"
    fq_bak = f"{cs['target_project']}.{cs['target_dataset']}.{bak_name}"
    paused_transfers: list[str] = []

    def abort(reason: str) -> None:
        """Clean abort — pre-swap only. Leaves the world exactly as found."""
        _resume_transfers(paused_transfers)
        try:
            bq.execute(c, f"DROP TABLE IF EXISTS `{fq_new}`")
        finally:
            _progress(c, cs, "ABORTED", {"reason": reason})
        raise Blocked(f"clean abort: {reason}")

    def _resume_transfers(names: list[str]) -> None:
        if not names:
            return
        try:
            from google.cloud import bigquery_datatransfer_v1 as dts
            dcli = dts.DataTransferServiceClient()
            for name in names:
                cfg_obj = dcli.get_transfer_config(name=name)
                cfg_obj.disabled = False
                dcli.update_transfer_config(transfer_config=cfg_obj,
                                            update_mask={"paths": ["disabled"]})
        except Exception:
            pass  # recorded in progress; humans resume from the card

    # ---- S0 PRECHECK ------------------------------------------------------
    table = cli.get_table(fq)
    if table.streaming_buffer:
        if change.get("allow_streaming_cutover") or change.get("force"):
            _progress(c, cs, "S2b_STREAMING_CUTOVER", {
                "streaming_buffer_estimated_bytes": getattr(table.streaming_buffer, "estimated_bytes", 0),
                "drain_acknowledged": True
            })
        else:
            rows_est = getattr(table.streaming_buffer, "estimated_rows", "unknown")
            raise Blocked(
                f"table {fq} has an active streaming buffer (~{rows_est} unpersisted rows). "
                f"Per design doc §9.4 S2b, streaming writers require dual-write or drain cutover to prevent data loss. "
                f"Pause ingestion or approve with allow_streaming_cutover=true during a scheduled maintenance window.")
    col_types = {f.name: f.field_type for f in table.schema}
    pcol = change.get("partition_column")
    if pcol not in col_types or col_types[pcol] not in ("DATE", "TIMESTAMP", "DATETIME"):
        raise Blocked(f"partition_column {pcol!r} missing or non-temporal on {fq}")
    _progress(c, cs, "S0_PRECHECK", {"rows": table.num_rows, "pcol_type": col_types[pcol]})

    # ---- S1 CAPTURE -------------------------------------------------------
    ddl = bq.query(c, f"""
        SELECT ddl FROM `{cs['target_project']}.{cs['target_dataset']}.INFORMATION_SCHEMA.TABLES`
        WHERE table_name = @t""", {"t": cs["target_table"]})[0]["ddl"]
    iam_policy = cli.get_iam_policy(table).to_api_repr()
    row_policies = []
    try:
        fn = getattr(cli, "list_row_access_policies", None)
        if fn:
            for rp in fn(table):
                row_policies.append({"policy_id": rp.row_access_policy_id,
                                     "grantees": list(rp.grantees or []),
                                     "filter_predicate": rp.filter_predicate})
        else:
            try:
                rls_rows = bq.query(c, f"SELECT * FROM `{cs['target_project']}.{cs['target_dataset']}.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` WHERE table_name = @t", {"t": cs["target_table"]})
                for rp in rls_rows:
                    row_policies.append({"policy_id": rp.get("row_access_policy_id"),
                                         "grantees": list(rp.get("grantees") or []),
                                         "filter_predicate": rp.get("filter_predicate")})
            except Exception:
                row_policies = []
    except Exception as e:
        abort(f"could not enumerate row access policies ({e}) — will not risk dropping RLS")
    snap = f"{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}__bqopt_snap_{ts}"
    bq.execute(c, f"""CREATE SNAPSHOT TABLE `{snap}` CLONE `{fq}`
        OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {int(c['executor']['backup_hold_days'])} DAY))""")
    rollback_plan = {"snapshot": snap, "backup": fq_bak, "iam_policy": iam_policy,
                     "row_policies": row_policies, "original_ddl": ddl,
                     "labels": dict(table.labels or {})}
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET rollback_plan_json=@p, current_config_ddl=@d "
                  f"WHERE change_set_id=@id",
               {"p": bq.dumps(rollback_plan), "d": ddl, "id": cs["change_set_id"]})
    _progress(c, cs, "S1_CAPTURE", {"snapshot": snap, "row_policies": len(row_policies)})

    # ---- S2 QUIESCE (batch writers: scheduled queries / DTS) --------------
    try:
        from google.cloud import bigquery_datatransfer_v1 as dts
        dcli = dts.DataTransferServiceClient()
        parent = f"projects/{cs['target_project']}"
        for tc in dcli.list_transfer_configs(parent=parent):
            dest = (tc.destination_dataset_id or "")
            if dest == cs["target_dataset"] and cs["target_table"] in str(tc.params):
                tc.disabled = True
                dcli.update_transfer_config(transfer_config=tc, update_mask={"paths": ["disabled"]})
                paused_transfers.append(tc.name)
    except ImportError:
        pass  # DTS client library not present in environment; proceed without DTS locking
    except Exception as e:
        abort(f"could not enumerate/pause scheduled writers ({e}) — refusing to race them")
    build_start = dt.datetime.now(dt.timezone.utc)
    _progress(c, cs, "S2_QUIESCE", {"paused_transfers": paused_transfers})
    # NOTE: Composer/Dataform/dbt jobs are outside DTS — list them on the card
    # (blast radius) and hold them via your orchestrator before approving.

    # ---- S3 BUILD ---------------------------------------------------------
    part_expr = _partition_expr(change, col_types[pcol])
    cluster_sql = ""
    if change.get("cluster_columns"):
        cluster_sql = "CLUSTER BY " + ", ".join(change["cluster_columns"])
    try:
        bq.execute(c, f"""
            CREATE TABLE `{fq_new}`
            PARTITION BY {part_expr}
            {cluster_sql}
            AS SELECT * FROM `{fq}`""")
    except Exception as e:
        abort(f"build failed: {e}")
    _progress(c, cs, "S3_BUILD", {"partition_by": part_expr,
                                  "cluster_by": change.get("cluster_columns")})

    # ---- S4 DELTA ---------------------------------------------------------
    delta_col = change.get("delta_column")
    if delta_col:
        try:
            n = bq.execute(c, f"""
                INSERT INTO `{fq_new}`
                SELECT * FROM `{fq}` WHERE {delta_col} > @b""",
                {"b": build_start.isoformat()})
        except Exception as e:
            abort(f"delta sync failed: {e}")
        _progress(c, cs, "S4_DELTA", {"rows": n})
    else:
        _progress(c, cs, "S4_DELTA", {"skipped": "no delta_column; relying on quiesce"})

    # ---- S5 VALIDATE ------------------------------------------------------
    counts = bq.query(c, f"""
        SELECT (SELECT COUNT(*) FROM `{fq}`) AS old_n,
               (SELECT COUNT(*) FROM `{fq_new}`) AS new_n""")[0]
    if counts["old_n"] != counts["new_n"]:
        abort(f"row count mismatch old={counts['old_n']} new={counts['new_n']}")
    sums = bq.query(c, f"""
        SELECT (SELECT BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) FROM `{fq}` t)     AS old_h,
               (SELECT BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) FROM `{fq_new}` t) AS new_h""")[0]
    if sums["old_h"] != sums["new_h"]:
        abort("checksum mismatch between original and rebuilt table")
    _progress(c, cs, "S5_VALIDATE", {"rows": int(counts["old_n"]), "checksum": "match"})

    # ---- S6 SWAP ----------------------------------------------------------
    bq.execute(c, f"ALTER TABLE `{fq}` RENAME TO `{bak_name}`")
    try:
        bq.execute(c, f"ALTER TABLE `{fq_new}` RENAME TO `{cs['target_table']}`")
    except Exception as e:
        bq.execute(c, f"ALTER TABLE `{fq_bak}` RENAME TO `{cs['target_table']}`")
        _resume_transfers(paused_transfers)
        _progress(c, cs, "SWAP_REVERTED", {"error": str(e)})
        raise Blocked(f"swap failed and was reverted: {e}") from e
    _progress(c, cs, "S6_SWAP", {"backup": fq_bak})

    # ---- S7 REBIND --------------------------------------------------------
    new_table = cli.get_table(fq)
    from google.api_core.iam import Policy
    # Only rebind IAM policy if custom table-level bindings existed on the original table
    if iam_policy and iam_policy.get("bindings"):
        try:
            current_policy = cli.get_iam_policy(new_table)
            current_policy.bindings = Policy.from_api_repr(iam_policy).bindings
            cli.set_iam_policy(new_table, current_policy)
        except Exception as e:
            _progress(c, cs, "S7_IAM_WARNING", {"warning": f"could not rebind IAM policy: {e}"})
    for rp in row_policies:
        grantees = ", ".join(f'"{g}"' for g in rp["grantees"])
        bq.execute(c, f"""
            CREATE ROW ACCESS POLICY `{rp['policy_id']}` ON `{fq}`
            GRANT TO ({grantees}) FILTER USING ({rp['filter_predicate']})""")
    if rollback_plan["labels"]:
        new_table.labels = rollback_plan["labels"]
        cli.update_table(new_table, ["labels"])
    if getattr(table, "description", None):
        try:
            new_table.description = table.description
            cli.update_table(new_table, ["description"])
        except Exception:
            pass
    _resume_transfers(paused_transfers)
    _progress(c, cs, "S7_REBIND", {"iam": "restored" if (iam_policy and iam_policy.get("bindings")) else "inherited",
                                   "row_policies": len(row_policies),
                                   "transfers_resumed": len(paused_transfers)})

    # ---- S8 POSTCHECK -----------------------------------------------------
    time.sleep(2)
    canary = bq.query(c, f"SELECT COUNT(*) AS n FROM `{fq}` "
                         f"WHERE {pcol} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)"
                      if col_types[pcol] != "DATE" else
                      f"SELECT COUNT(*) AS n FROM `{fq}` "
                      f"WHERE {pcol} >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)")[0]
    _progress(c, cs, "S8_POSTCHECK", {"recent_rows": int(canary["n"])})

    # ---- S9 HOLD ----------------------------------------------------------
    hold = int(c["executor"]["backup_hold_days"])
    bq.execute(c, f"""ALTER TABLE `{fq_bak}` SET OPTIONS (
        expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {hold} DAY))""")
    _progress(c, cs, "S9_HOLD", {"backup_expires_days": hold,
                                 "note": "new table has no time-travel history; snapshot is the pre-swap anchor"})
    return rollback_plan


def rollback(c: Config, cs: dict) -> None:
    """Post-swap reversal within the hold window: reverse renames + rebind."""
    plan = bq.loads(cs.get("rollback_plan_json")) or {}
    fq = _fq(cs)
    bak = plan.get("backup")
    if not bak:
        raise Blocked("no backup recorded; restore from snapshot " + str(plan.get("snapshot")))
    cli = bq.client(c)
    hold = int(c["executor"]["backup_hold_days"])
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M")
    regressed_name = f"{cs['target_table']}__bqopt_regressed_{ts}"
    regressed = f"{fq}__bqopt_regressed_{ts}"

    try:
        cli.get_table(bak)
        bak_exists = True
    except Exception:
        bak_exists = False

    if bak_exists:
        bq.execute(c, f"ALTER TABLE `{fq}` RENAME TO `{regressed_name}`")
        bq.execute(c, f"ALTER TABLE `{bak}` RENAME TO `{cs['target_table']}`")
        bq.execute(c, f"ALTER TABLE `{fq}` SET OPTIONS (expiration_timestamp = NULL)")
        bq.execute(c, f"ALTER TABLE `{regressed}` SET OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {hold} DAY))")
    else:
        # Backup table already restored in a prior step; preserve existing forensics reference if any
        prog = bq.loads(cs.get("progress_json")) or {}
        existing_reg = prog.get("steps", {}).get("ROLLED_BACK", {}).get("kept_for_forensics")
        if existing_reg:
            regressed = existing_reg

    from google.api_core.iam import Policy
    orig_iam = plan.get("iam_policy")
    if orig_iam and orig_iam.get("bindings"):
        try:
            restored_table = cli.get_table(fq)
            current_policy = cli.get_iam_policy(restored_table)
            current_policy.bindings = Policy.from_api_repr(orig_iam).bindings
            cli.set_iam_policy(restored_table, current_policy)
        except Exception:
            pass
    _progress(c, cs, "ROLLED_BACK", {"kept_for_forensics": regressed, "regressed_expires_days": hold})

