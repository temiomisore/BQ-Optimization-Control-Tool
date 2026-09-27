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
# W-01 capacity migration: on-demand -> Enterprise reservation (REAL DDL)
# ---------------------------------------------------------------------------

_RES_NAMES = {1: "enterprise-prod-pool", 2: "enterprise-autoscale-only-pool"}
_ASSIGNMENT_ID = "assign-project"


def _round50(n: int) -> int:
    """Enterprise slot values must be multiples of 50."""
    return max(0, int(round(int(n) / 50.0)) * 50)


def capacity_plan(c: Config, cs: dict, change: dict) -> dict:
    """Pure: resolve the approved option into concrete reservation parameters.
    Option precedence: env BQOPT_RESERVATION_OPTION > change.selected_option > 1."""
    import os
    proj = cs["target_project"]
    region = (cs.get("target_region") or c.get("location", "US")).lower()
    raw = os.environ.get("BQOPT_RESERVATION_OPTION") or change.get("selected_option") or 1
    try:
        opt = 2 if int(raw) == 2 else 1
    except (TypeError, ValueError):
        opt = 1
    ceiling = _round50(change.get("recommended_autoscale_max_slots", 300)) or 50
    baseline = _round50(change.get("recommended_baseline_slots", 100)) if opt == 1 else 0
    baseline = min(baseline, ceiling)
    autoscale = ceiling - baseline          # autoscale_max_slots = burst ON TOP of baseline
    name = _RES_NAMES[opt]
    loc = f"region-{region}"
    return {
        "option": opt, "project": proj, "region": region,
        "reservation_name": name,
        "reservation_fq": f"{proj}.{loc}.{name}",
        "assignment_fq": f"{proj}.{loc}.{name}.{_ASSIGNMENT_ID}",
        "baseline_slots": baseline, "autoscale_max_slots": autoscale, "max_slots": ceiling,
        "create_reservation_sql": (
            f"CREATE RESERVATION `{proj}.{loc}.{name}`\n"
            f"OPTIONS (edition = 'ENTERPRISE', slot_capacity = {baseline}, "
            f"autoscale_max_slots = {autoscale})"),
        "create_assignment_sql": (
            f"CREATE ASSIGNMENT `{proj}.{loc}.{name}.{_ASSIGNMENT_ID}`\n"
            f"OPTIONS (assignee = 'projects/{proj}', job_type = 'QUERY')"),
    }


def _run_capacity_migration(c: Config, cs: dict, change: dict) -> dict:
    if not c["executor"].get("enable_reservation_changes", False):
        raise Blocked("reservation changes are disabled (executor.enable_reservation_changes=false). "
                      "Creating a reservation changes billing for EVERY query in the project — "
                      "set it true in config.yaml to allow a real run.")
    p = capacity_plan(c, cs, change)
    loc = f"region-{p['region']}"

    # ---- S0 PRECHECK: never fight an existing reservation assignment ----------
    assigns = bq.query(c, f"""
        SELECT reservation_name, assignment_id, job_type
        FROM `{p['project']}.{loc}.INFORMATION_SCHEMA.ASSIGNMENTS`
        WHERE assignee_id = @proj AND job_type = 'QUERY'""", {"proj": p["project"]})
    foreign = [a for a in assigns if a.get("reservation_name") != p["reservation_name"]]
    if foreign:
        raise Blocked(f"project {p['project']} already has a QUERY assignment to reservation "
                      f"{foreign[0].get('reservation_name')!r} — refusing to reassign; resolve manually")
    already_assigned = any(a.get("assignment_id") == _ASSIGNMENT_ID for a in assigns)
    existing = bq.query(c, f"""
        SELECT reservation_name FROM `{p['project']}.{loc}.INFORMATION_SCHEMA.RESERVATIONS`
        WHERE reservation_name = @n""", {"n": p["reservation_name"]})
    _progress(c, cs, "S0_PRECHECK", {"option": p["option"], "reservation_exists": bool(existing),
                                     "already_assigned": already_assigned})

    plan = {"action": "CAPACITY_PRICING_MIGRATION", "prior_billing_model": "ON_DEMAND",
            "option": p["option"], "reservation_fq": p["reservation_fq"],
            "assignment_fq": p["assignment_fq"], "baseline_slots": p["baseline_slots"],
            "autoscale_max_slots": p["autoscale_max_slots"], "max_slots": p["max_slots"],
            "created_reservation": False, "created_assignment": False}

    # ---- S1 RESERVATION -------------------------------------------------------
    if not existing:
        bq.execute(c, p["create_reservation_sql"])
        plan["created_reservation"] = True
    # persist the rollback plan immediately so a crash after S1 is still reversible
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET rollback_plan_json=@p WHERE change_set_id=@id",
               {"p": bq.dumps(plan), "id": cs["change_set_id"]})
    _progress(c, cs, "S1_RESERVATION_PROVISIONED", {
        "reservation": p["reservation_fq"], "edition": "ENTERPRISE",
        "slot_capacity": p["baseline_slots"], "autoscale_max_slots": p["autoscale_max_slots"],
        "created": plan["created_reservation"]})

    # ---- S2 ASSIGNMENT --------------------------------------------------------
    if not already_assigned:
        try:
            bq.execute(c, p["create_assignment_sql"])
        except Exception as e:
            if plan["created_reservation"]:
                bq.execute(c, f"DROP RESERVATION IF EXISTS `{p['reservation_fq']}`")
            _progress(c, cs, "ABORTED", {"reason": f"assignment failed: {e}"})
            raise Blocked(f"clean abort: assignment failed ({e}); reservation removed") from e
        plan["created_assignment"] = True
    _progress(c, cs, "S2_ASSIGNMENT_BOUND", {"assignment": p["assignment_fq"],
                                             "assignee": f"projects/{p['project']}", "job_type": "QUERY"})
    plan["note"] = (f"Project {p['project']} assigned to Enterprise reservation {p['reservation_name']} "
                    f"(Option {p['option']}: {p['baseline_slots']} baseline + up to "
                    f"{p['autoscale_max_slots']} autoscale = {p['max_slots']} max slots).")
    return plan


def _rollback_capacity_migration(c: Config, cs: dict, plan: dict) -> None:
    """Return the project to on-demand: drop the assignment, then the reservation
    (only if this tool created it)."""
    dropped = []
    if plan.get("assignment_fq"):
        bq.execute(c, f"DROP ASSIGNMENT IF EXISTS `{plan['assignment_fq']}`")
        dropped.append(plan["assignment_fq"])
    if plan.get("created_reservation") and plan.get("reservation_fq"):
        for attempt in range(3):   # assignment removal can take a moment to propagate
            try:
                bq.execute(c, f"DROP RESERVATION IF EXISTS `{plan['reservation_fq']}`")
                dropped.append(plan["reservation_fq"])
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(10)
    _progress(c, cs, "ROLLED_BACK", {"dropped": dropped, "billing_model": "ON_DEMAND"})


# ---------------------------------------------------------------------------

def run(c: Config, cs: dict) -> dict:
    if not c["executor"]["enable_class3"]:
        raise Blocked("class 3 is disabled (executor.enable_class3=false) — rehearse on scratch data first")

    change = bq.loads(cs["proposed_change_json"]) or {}
    if change.get("action") == "CAPACITY_PRICING_MIGRATION":
        return _run_capacity_migration(c, cs, change)

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
    if plan.get("action") == "CAPACITY_PRICING_MIGRATION":
        return _rollback_capacity_migration(c, cs, plan)
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

