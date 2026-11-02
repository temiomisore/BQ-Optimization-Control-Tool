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
import re
import time

from .. import bq, security
from ..config import Config
from .router import Blocked


def _fq(cs: dict) -> str:
    p = security.validate_project_id(cs["target_project"])
    d = security.validate_dataset_id(cs["target_dataset"])
    t = security.validate_table_id(cs["target_table"])
    return f"{p}.{d}.{t}"


def _progress(c: Config, cs: dict, step: str, data: dict) -> dict:
    p = bq.loads(cs.get("progress_json")) or {"steps": {}}
    p["steps"][step] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), **data}
    p["last_step"] = step
    cs["progress_json"] = bq.dumps(p)
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET progress_json=@p WHERE change_set_id=@id",
               {"p": cs["progress_json"], "id": cs["change_set_id"]})
    return p


def _partition_expr(change: dict, col_type: str) -> str:
    qcol = security.quote_ident(change["partition_column"])
    gran = security.validate_partition_granularity(change.get("granularity", "DAY"))
    if col_type in ("TIMESTAMP", "DATETIME"):
        return f"{'DATE' if gran == 'DAY' else 'TIMESTAMP_TRUNC'}({qcol}"        \
               + (")" if gran == "DAY" else f", {gran})")
    return qcol  # DATE column partitions directly


# ---------------------------------------------------------------------------
# W-01 capacity migration: on-demand -> Enterprise reservation (REAL DDL)
# ---------------------------------------------------------------------------

_RES_NAMES = {1: "enterprise-prod-pool", 2: "enterprise-autoscale-only-pool"}
_ASSIGNMENT_ID = "assign-project"


def _round50(n: int) -> int:
    """Enterprise slot values must be multiples of 50."""
    return max(0, int(round(int(n) / 50.0)) * 50)


def _region_of(reservation_fq: str | None) -> str | None:
    """'proj.region-us-east4.name' -> 'us-east4' (older rollback plans have no region key)."""
    m = re.search(r"region-([a-z0-9-]+)", reservation_fq or "")
    return m.group(1) if m else None


def capacity_plan(c: Config, cs: dict, change: dict) -> dict:
    """Pure: resolve the approved option into concrete reservation parameters.
    Option precedence: env BQOPT_RESERVATION_OPTION > change.selected_option > 1."""
    import os
    proj = security.validate_project_id(cs["target_project"])
    region = security.validate_region(cs.get("target_region") or c.get("location", "US"))
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
    rloc = bq.location_for(p["region"])   # reservation statements run in that region

    # ---- S0 PRECHECK: never fight an existing reservation assignment ----------
    assigns = bq.query(c, f"""
        SELECT reservation_name, assignment_id, job_type
        FROM `{p['project']}.{loc}.INFORMATION_SCHEMA.ASSIGNMENTS`
        WHERE assignee_id = @proj AND job_type = 'QUERY'""", {"proj": p["project"]}, location=rloc)
    foreign = [a for a in assigns if a.get("reservation_name") != p["reservation_name"]]
    if foreign:
        raise Blocked(f"project {p['project']} already has a QUERY assignment to reservation "
                      f"{foreign[0].get('reservation_name')!r} — refusing to reassign; resolve manually")
    already_assigned = any(a.get("assignment_id") == _ASSIGNMENT_ID for a in assigns)
    existing = bq.query(c, f"""
        SELECT reservation_name FROM `{p['project']}.{loc}.INFORMATION_SCHEMA.RESERVATIONS`
        WHERE reservation_name = @n""", {"n": p["reservation_name"]}, location=rloc)
    _progress(c, cs, "S0_PRECHECK", {"option": p["option"], "reservation_exists": bool(existing),
                                     "already_assigned": already_assigned})

    plan = {"action": "CAPACITY_PRICING_MIGRATION", "prior_billing_model": "ON_DEMAND",
            "option": p["option"], "region": p["region"], "reservation_fq": p["reservation_fq"],
            "assignment_fq": p["assignment_fq"], "baseline_slots": p["baseline_slots"],
            "autoscale_max_slots": p["autoscale_max_slots"], "max_slots": p["max_slots"],
            "created_reservation": False, "created_assignment": False}

    # ---- S1 RESERVATION -------------------------------------------------------
    if not existing:
        bq.execute(c, p["create_reservation_sql"], location=rloc)
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
            bq.execute(c, p["create_assignment_sql"], location=rloc)
        except Exception as e:
            if plan["created_reservation"]:
                bq.execute(c, f"DROP RESERVATION IF EXISTS `{p['reservation_fq']}`", location=rloc)
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
    rloc = bq.location_for(plan.get("region") or _region_of(plan.get("reservation_fq")))
    if plan.get("assignment_fq"):
        afq = security.validate_assignment_fq(plan["assignment_fq"])
        bq.execute(c, f"DROP ASSIGNMENT IF EXISTS `{afq}`", location=rloc)
        dropped.append(afq)
    # Keep the reservation alive until routing has propagated back to on-demand; dropping it
    # immediately makes queries fail for a few minutes ("project does not have the reservation").
    res_short = (plan.get("reservation_fq") or "").split(".")[-1]
    routed_back = False
    for _ in range(18):                      # up to ~3 minutes
        rid = bq.probe_reservation(c, location=rloc)
        if not rid or not res_short or not str(rid).endswith(res_short):
            routed_back = True
            break
        time.sleep(10)
    if plan.get("created_reservation") and plan.get("reservation_fq"):
        rfq = security.validate_reservation_fq(plan["reservation_fq"])
        for attempt in range(6):
            try:
                bq.execute(c, f"DROP RESERVATION IF EXISTS `{rfq}`", location=rloc)
                dropped.append(rfq)
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(10)
    _progress(c, cs, "ROLLED_BACK", {"dropped": dropped, "billing_model": "ON_DEMAND",
                                     "routing_confirmed_on_demand": routed_back})


# ---------------------------------------------------------------------------
# W-03 reservation right-sizing: ALTER RESERVATION (+ optional On-Demand return)
# ---------------------------------------------------------------------------

def _assignee_uri(assignee_type: str | None, assignee_id: str | None) -> str:
    aid = str(assignee_id or "").strip()
    if not aid:
        raise Blocked("missing assignee_id on reservation assignment")
    if aid.startswith("projects/"):
        tail = aid[len("projects/"):]
        if not re.fullmatch(r"\d{4,25}", tail):
            tail = security.validate_project_id(tail)
        return f"projects/{tail}"
    if aid.startswith(("folders/", "organizations/")):
        prefix, tail = aid.split("/", 1)
        if not re.fullmatch(r"\d{4,25}", tail):
            raise Blocked(f"invalid {prefix} assignee id: {aid!r}")
        return f"{prefix}/{tail}"
    atype = str(assignee_type or "PROJECT").strip().upper()
    if atype == "PROJECT":
        if not re.fullmatch(r"\d{4,25}", aid):
            aid = security.validate_project_id(aid)
        return f"projects/{aid}"
    if atype == "FOLDER" and re.fullmatch(r"\d{4,25}", aid):
        return f"folders/{aid}"
    if atype == "ORGANIZATION" and re.fullmatch(r"\d{4,25}", aid):
        return f"organizations/{aid}"
    raise Blocked(f"unsupported reservation assignee ({assignee_type!r}: {assignee_id!r})")


def reservation_resize_plan(c: Config, cs: dict, change: dict) -> dict:
    """Pure: resolve W-03 Option 1 / Option 2 into concrete ALTER RESERVATION parameters."""
    import os
    proj = security.validate_project_id(cs["target_project"])
    region = security.validate_region(cs.get("target_region") or c.get("location", "US"))
    raw_name = change.get("reservation_name") or cs.get("target_table")
    res_fq = security.validate_reservation_fq(f"{proj}.region-{region}.{raw_name}")
    res_name = res_fq.split(".")[-1]
    raw = os.environ.get("BQOPT_RESERVATION_OPTION") or change.get("selected_option") or 1
    try:
        opt = 2 if int(raw) == 2 else 1
    except (TypeError, ValueError):
        opt = 1
    edition = str(change.get("edition") or "ENTERPRISE").strip().upper().replace("-", "_").replace(" ", "_")
    opt2_mode = str(change.get("option_2_mode") or "PURE_AUTOSCALE").strip().upper()

    if opt == 2 and opt2_mode == "ON_DEMAND":
        return_to_od = True
        baseline, autoscale, ceiling = 0, 0, 0
    else:
        return_to_od = False
        ceiling = _round50(change.get("recommended_autoscale_max_slots", 300)) or 50
        if edition == "STANDARD":
            ceiling = min(ceiling, 1600)
        baseline = _round50(change.get("recommended_baseline_slots", 100)) if opt == 1 else 0
        baseline = min(baseline, ceiling)
        autoscale = max(0, ceiling - baseline)

    alter_sql = (
        f"ALTER RESERVATION `{res_fq}`\n"
        f"SET OPTIONS (slot_capacity = {baseline}, autoscale_max_slots = {autoscale})"
    )
    bq.assert_single_statement_ddl(alter_sql, ("ALTER_RESERVATION",))
    return {
        "option": opt,
        "project": proj,
        "region": region,
        "reservation_name": res_name,
        "reservation_fq": res_fq,
        "edition": edition,
        "baseline_slots": baseline,
        "autoscale_max_slots": autoscale,
        "max_slots": ceiling,
        "return_to_on_demand": return_to_od,
        "alter_reservation_sql": alter_sql,
    }


def _run_reservation_resize(c: Config, cs: dict, change: dict) -> dict:
    if not c["executor"].get("enable_reservation_changes", False):
        raise Blocked("reservation changes are disabled (executor.enable_reservation_changes=false). "
                      "Resizing a reservation changes slot capacity for assigned workloads — "
                      "set it true in config.yaml to allow a real run.")
    p = reservation_resize_plan(c, cs, change)
    loc = f"region-{p['region']}"
    rloc = bq.location_for(p["region"])

    existing = bq.query(c, f"""
        SELECT reservation_name, edition, slot_capacity,
               autoscale.max_slots AS autoscale_max_slots
        FROM `{p['project']}.{loc}.INFORMATION_SCHEMA.RESERVATIONS`
        WHERE LOWER(reservation_name) = @n""", {"n": p["reservation_name"].lower()}, location=rloc)
    if not existing:
        raise Blocked(f"reservation {p['reservation_fq']!r} not found in {loc}")
    cur = existing[0]
    orig_base = max(0, int(cur.get("slot_capacity") or 0))
    raw_auto = cur.get("autoscale_max_slots")
    if raw_auto is None and isinstance(cur.get("autoscale"), dict):
        raw_auto = cur["autoscale"].get("max_slots")
    orig_auto = max(0, int(raw_auto or 0))
    orig_edition = str(cur.get("edition") or p["edition"]).upper()

    to_drop: list[dict] = []
    if p["return_to_on_demand"]:
        arows = bq.query(c, f"""
            SELECT assignment_id, assignee_type, assignee_id
            FROM `{p['project']}.{loc}.INFORMATION_SCHEMA.ASSIGNMENTS`
            WHERE LOWER(reservation_name) = @n
              AND UPPER(COALESCE(job_type, 'QUERY')) = 'QUERY'""",
            {"n": p["reservation_name"].lower()}, location=rloc)
        for a in (arows or []):
            afq = security.validate_assignment_fq(f"{p['reservation_fq']}.{a['assignment_id']}")
            auri = _assignee_uri(a.get("assignee_type"), a.get("assignee_id"))
            to_drop.append({
                "assignment_fq": afq,
                "assignee_type": str(a.get("assignee_type") or "PROJECT").upper(),
                "assignee_id": str(a.get("assignee_id") or ""),
                "assignee_uri": auri,
            })

    _progress(c, cs, "S0_PRECHECK", {
        "option": p["option"],
        "prior_baseline_slots": orig_base,
        "prior_autoscale_max_slots": orig_auto,
        "return_to_on_demand": p["return_to_on_demand"],
        "assignments_to_drop": len(to_drop),
    })

    plan: dict = {
        "action": "RESIZE_RESERVATION",
        "option": p["option"],
        "region": p["region"],
        "reservation_fq": p["reservation_fq"],
        "edition": orig_edition,
        "prior_baseline_slots": orig_base,
        "prior_autoscale_max_slots": orig_auto,
        "new_baseline_slots": p["baseline_slots"],
        "new_autoscale_max_slots": p["autoscale_max_slots"],
        "return_to_on_demand": p["return_to_on_demand"],
        "dropped_assignments": [],
    }
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET rollback_plan_json=@p WHERE change_set_id=@id",
               {"p": bq.dumps(plan), "id": cs["change_set_id"]})

    for item in to_drop:
        bq.execute(c, f"DROP ASSIGNMENT IF EXISTS `{item['assignment_fq']}`", location=rloc)
        plan["dropped_assignments"].append(item)
        bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET rollback_plan_json=@p WHERE change_set_id=@id",
                   {"p": bq.dumps(plan), "id": cs["change_set_id"]})

    bq.assert_single_statement_ddl(p["alter_reservation_sql"], ("ALTER_RESERVATION",))
    bq.execute(c, p["alter_reservation_sql"], location=rloc)

    if p["return_to_on_demand"]:
        plan["note"] = (
            f"Unassigned {len(plan['dropped_assignments'])} QUERY assignment(s) from {p['reservation_name']} "
            f"(returning jobs to On-Demand) and set capacity to 0 slots."
        )
    else:
        plan["note"] = (
            f"Resized {orig_edition} reservation {p['reservation_name']} from "
            f"{orig_base}+{orig_auto} slots to {p['baseline_slots']}+{p['autoscale_max_slots']} slots "
            f"({p['max_slots']} max)."
        )
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET rollback_plan_json=@p WHERE change_set_id=@id",
               {"p": bq.dumps(plan), "id": cs["change_set_id"]})
    _progress(c, cs, "S1_RESERVATION_RESIZED", {
        "reservation": p["reservation_fq"],
        "slot_capacity": p["baseline_slots"],
        "autoscale_max_slots": p["autoscale_max_slots"],
        "dropped_assignments": len(plan["dropped_assignments"]),
    })
    return plan


def _rollback_reservation_resize(c: Config, cs: dict, plan: dict) -> None:
    """Restore previous slot_capacity + autoscale_max_slots first, then re-bind any dropped QUERY assignments."""
    rloc = bq.location_for(plan.get("region") or _region_of(plan.get("reservation_fq")))
    rfq = security.validate_reservation_fq(plan["reservation_fq"])
    orig_base = _round50(plan.get("prior_baseline_slots", 0))
    orig_auto = _round50(plan.get("prior_autoscale_max_slots", 0))
    alter_sql = (
        f"ALTER RESERVATION `{rfq}`\n"
        f"SET OPTIONS (slot_capacity = {orig_base}, autoscale_max_slots = {orig_auto})"
    )
    bq.assert_single_statement_ddl(alter_sql, ("ALTER_RESERVATION",))
    bq.execute(c, alter_sql, location=rloc)

    restored_assigns: list[str] = []
    for item in (plan.get("dropped_assignments") or []):
        afq = security.validate_assignment_fq(item["assignment_fq"])
        auri = _assignee_uri(item.get("assignee_type"), item.get("assignee_id"))
        bq.execute(
            c,
            f"CREATE ASSIGNMENT `{afq}`\nOPTIONS (assignee = '{auri}', job_type = 'QUERY')",
            location=rloc,
        )
        restored_assigns.append(afq)

    _progress(c, cs, "ROLLED_BACK", {
        "reservation": rfq,
        "restored_baseline_slots": orig_base,
        "restored_autoscale_max_slots": orig_auto,
        "restored_assignments": restored_assigns,
    })


# ---------------------------------------------------------------------------

def run(c: Config, cs: dict) -> dict:
    if not c["executor"]["enable_class3"]:
        raise Blocked("class 3 is disabled (executor.enable_class3=false) — rehearse on scratch data first")

    change = bq.loads(cs["proposed_change_json"]) or {}
    if change.get("action") == "CAPACITY_PRICING_MIGRATION":
        return _run_capacity_migration(c, cs, change)
    if change.get("action") == "RESIZE_RESERVATION":
        return _run_reservation_resize(c, cs, change)

    if change.get("action") not in ("REPARTITION",):
        raise Blocked(f"class3 v1 only implements REPARTITION, CAPACITY_PRICING_MIGRATION and RESIZE_RESERVATION (got {change.get('action')}); "
                      f"CONSOLIDATE_SHARDS and ARCHIVE_TO_GCS_THEN_DROP are runbook-only for now")

    cli = bq.client(c)
    loc = bq.location_for(cs.get("target_region"))   # every statement on the target runs in its region
    proj = security.validate_project_id(cs["target_project"])
    ds_id = security.validate_dataset_id(cs["target_dataset"])
    tbl_id = security.validate_table_id(cs["target_table"])
    fq = f"{proj}.{ds_id}.{tbl_id}"
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M")
    new_name = security.validate_table_id(f"{tbl_id}__bqopt_new")
    bak_name = security.validate_table_id(f"{tbl_id}__bqopt_bak_{ts}")
    fq_new = f"{proj}.{ds_id}.{new_name}"
    fq_bak = f"{proj}.{ds_id}.{bak_name}"
    paused_transfers: list[str] = []

    def abort(reason: str) -> None:
        """Clean abort — pre-swap only. Leaves the world exactly as found."""
        _resume_transfers(paused_transfers)
        try:
            bq.execute(c, f"DROP TABLE IF EXISTS `{fq_new}`", location=loc)
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
        SELECT ddl FROM `{proj}.{ds_id}.INFORMATION_SCHEMA.TABLES`
        WHERE table_name = @t""", {"t": tbl_id}, location=loc)[0]["ddl"]
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
                rls_rows = bq.query(c, f"SELECT * FROM `{proj}.{ds_id}.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES` WHERE table_name = @t", {"t": tbl_id}, location=loc)
                for rp in rls_rows:
                    row_policies.append({"policy_id": rp.get("row_access_policy_id"),
                                         "grantees": list(rp.get("grantees") or []),
                                         "filter_predicate": rp.get("filter_predicate")})
            except Exception:
                row_policies = []
    except Exception as e:
        abort(f"could not enumerate row access policies ({e}) — will not risk dropping RLS")
    snap_tbl = security.validate_table_id(f"{tbl_id}__bqopt_snap_{ts}")
    snap = f"{proj}.{ds_id}.{snap_tbl}"
    hold_days = max(1, min(365, int(c["executor"]["backup_hold_days"])))
    bq.execute(c, f"""CREATE SNAPSHOT TABLE `{snap}` CLONE `{fq}`
        OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {hold_days} DAY))""", location=loc)
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
        parent = f"projects/{proj}"
        for tc in dcli.list_transfer_configs(parent=parent):
            dest = (tc.destination_dataset_id or "")
            if dest == ds_id and tbl_id in str(tc.params):
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
    raw_clusters = change.get("cluster_columns") or []
    if raw_clusters:
        if not isinstance(raw_clusters, list) or len(raw_clusters) > 4:
            abort("cluster_columns must be a list of at most 4 column names")
        try:
            qclusters = [
                security.quote_ident(security.validate_column_name(col, col_types.keys()))
                for col in raw_clusters
            ]
        except security.SecurityValidationError as e:
            abort(f"invalid cluster_columns: {e}")
        cluster_sql = "CLUSTER BY " + ", ".join(qclusters)
    try:
        bq.execute(c, f"""
            CREATE TABLE `{fq_new}`
            PARTITION BY {part_expr}
            {cluster_sql}
            AS SELECT * FROM `{fq}`""", location=loc)
    except Exception as e:
        abort(f"build failed: {e}")
    _progress(c, cs, "S3_BUILD", {"partition_by": part_expr,
                                  "cluster_by": change.get("cluster_columns")})

    # ---- S4 DELTA ---------------------------------------------------------
    delta_col = change.get("delta_column")
    if delta_col:
        try:
            qdelta = security.quote_ident(security.validate_column_name(delta_col, col_types.keys()))
            n = bq.execute(c, f"""
                INSERT INTO `{fq_new}`
                SELECT * FROM `{fq}` WHERE {qdelta} > @b""",
                {"b": build_start.isoformat()}, location=loc)
        except Exception as e:
            abort(f"delta sync failed: {e}")
        _progress(c, cs, "S4_DELTA", {"rows": n})
    else:
        _progress(c, cs, "S4_DELTA", {"skipped": "no delta_column; relying on quiesce"})

    # ---- S5 VALIDATE ------------------------------------------------------
    counts = bq.query(c, f"""
        SELECT (SELECT COUNT(*) FROM `{fq}`) AS old_n,
               (SELECT COUNT(*) FROM `{fq_new}`) AS new_n""", location=loc)[0]
    if counts["old_n"] != counts["new_n"]:
        abort(f"row count mismatch old={counts['old_n']} new={counts['new_n']}")
    sums = bq.query(c, f"""
        SELECT (SELECT BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) FROM `{fq}` t)     AS old_h,
               (SELECT BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))) FROM `{fq_new}` t) AS new_h""", location=loc)[0]
    if sums["old_h"] != sums["new_h"]:
        abort("checksum mismatch between original and rebuilt table")
    _progress(c, cs, "S5_VALIDATE", {"rows": int(counts["old_n"]), "checksum": "match"})

    # ---- S6 SWAP ----------------------------------------------------------
    bq.execute(c, f"ALTER TABLE `{fq}` RENAME TO `{bak_name}`", location=loc)
    try:
        bq.execute(c, f"ALTER TABLE `{fq_new}` RENAME TO `{tbl_id}`", location=loc)
    except Exception as e:
        bq.execute(c, f"ALTER TABLE `{fq_bak}` RENAME TO `{tbl_id}`", location=loc)
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
        pid = security.validate_rls_policy_id(rp["policy_id"])
        validated_grantees = [security.validate_iam_grantee(g) for g in (rp.get("grantees") or [])]
        if not validated_grantees:
            raise Blocked(f"row access policy {pid!r} has no valid IAM grantees")
        grantees = ", ".join(f'"{g}"' for g in validated_grantees)
        pred = str(rp.get("filter_predicate") or "").strip()
        rls_sql = (
            f"CREATE ROW ACCESS POLICY `{pid}` ON `{fq}` "
            f"GRANT TO ({grantees}) FILTER USING ({pred})"
        )
        bq.assert_single_statement_ddl(rls_sql, ("CREATE_ROW_ACCESS_POLICY",))
        bq.execute(c, rls_sql, location=loc)
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
    qpcol = security.quote_ident(pcol)
    canary = bq.query(c, f"SELECT COUNT(*) AS n FROM `{fq}` "
                         f"WHERE {qpcol} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)"
                      if col_types[pcol] != "DATE" else
                      f"SELECT COUNT(*) AS n FROM `{fq}` "
                      f"WHERE {qpcol} >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)", location=loc)[0]
    _progress(c, cs, "S8_POSTCHECK", {"recent_rows": int(canary["n"])})

    # ---- S9 HOLD ----------------------------------------------------------
    hold = max(1, min(365, int(c["executor"]["backup_hold_days"])))
    bq.execute(c, f"""ALTER TABLE `{fq_bak}` SET OPTIONS (
        expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {hold} DAY))""", location=loc)
    _progress(c, cs, "S9_HOLD", {"backup_expires_days": hold,
                                 "note": "new table has no time-travel history; snapshot is the pre-swap anchor"})
    return rollback_plan


def rollback(c: Config, cs: dict) -> None:
    """Post-swap reversal within the hold window: reverse renames + rebind."""
    plan = bq.loads(cs.get("rollback_plan_json")) or {}
    if plan.get("action") == "CAPACITY_PRICING_MIGRATION":
        return _rollback_capacity_migration(c, cs, plan)
    if plan.get("action") == "RESIZE_RESERVATION":
        return _rollback_reservation_resize(c, cs, plan)
    proj = security.validate_project_id(cs["target_project"])
    ds_id = security.validate_dataset_id(cs["target_dataset"])
    tbl_id = security.validate_table_id(cs["target_table"])
    fq = f"{proj}.{ds_id}.{tbl_id}"
    raw_bak = plan.get("backup")
    if not raw_bak:
        raise Blocked("no backup recorded; restore from snapshot " + str(plan.get("snapshot")))
    bp, bd, bt = security.validate_fqn(raw_bak)
    bak = f"{bp}.{bd}.{bt}"
    cli = bq.client(c)
    loc = bq.location_for(cs.get("target_region"))
    hold = max(1, min(365, int(c["executor"]["backup_hold_days"])))
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M")
    regressed_name = security.validate_table_id(f"{tbl_id}__bqopt_regressed_{ts}")
    regressed = f"{proj}.{ds_id}.{regressed_name}"

    try:
        cli.get_table(bak)
        bak_exists = True
    except Exception:
        bak_exists = False

    if bak_exists:
        bq.execute(c, f"ALTER TABLE `{fq}` RENAME TO `{regressed_name}`", location=loc)
        bq.execute(c, f"ALTER TABLE `{bak}` RENAME TO `{tbl_id}`", location=loc)
        bq.execute(c, f"ALTER TABLE `{fq}` SET OPTIONS (expiration_timestamp = NULL)", location=loc)
        bq.execute(c, f"ALTER TABLE `{regressed}` SET OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {hold} DAY))", location=loc)
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

