"""bq-optimizer CLI — every worker entrypoint.

  python -m optimizer.cli init               # run DDL (01, 04, 03)
  python -m optimizer.cli collect-backfill   # driver: billing model + DTS snapshot
  python -m optimizer.cli rules              # expire sweep -> rules -> score -> compile -> insert
  python -m optimizer.cli execute            # apply APPROVED sets (route + class dispatch)
  python -m optimizer.cli verify             # VERIFYING checks + watchdogs
  python -m optimizer.cli sync-recommender   # push REJECTED dismissals
  python -m optimizer.cli rollback --id ID   # manual rollback of an applied set
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import bq, collector_driver, compiler, rules, scoring, store, verifier
from .config import cfg
from .executor import class1, class2, class3, recommender_sync, router


def cmd_init(c) -> None:
    from google.cloud import bigquery
    client = bq.client(c)
    ds_id = f"{c['project_id']}.{c['ops_dataset']}"
    ds = bigquery.Dataset(ds_id)
    ds.location = c.get("location", "US")
    ds.description = "BigQuery optimization control plane — telemetry and findings"
    client.create_dataset(ds, exists_ok=True)
    print(f"[*] Initialized dataset: {ds_id} (Location: {ds.location})")

    for f in ("sql/01_ops_schema.sql", "sql/04_change_sets.sql", "sql/03_derived_views.sql"):
        bq.run_file(c, f)
        print("  + Ran DDL:", f)
    print("✅ Control plane schema initialized successfully!")


def cmd_collect(c) -> None:
    print(f"[*] Running BigQuery metadata & query collector on project: {c['project_id']}...")
    import re
    with open("sql/02_collector_run.sql") as f:
        sql = f.read()
    if c.get("regions"):
        regions_list = [f"'{r}'" for r in c["regions"]]
        sql = re.sub(r"DECLARE regions\s+ARRAY<STRING>\s+DEFAULT\s+\[[^\]]*\];",
                     f"DECLARE regions               ARRAY<STRING> DEFAULT [{', '.join(regions_list)}];", sql)
    if c.get("jobs_view"):
        sql = re.sub(r"DECLARE jobs_view\s+STRING\s+DEFAULT\s+'[^']*';",
                     f"DECLARE jobs_view             STRING DEFAULT '{c['jobs_view']}';", sql)
    if c.get("reservation_admin_project"):
        sql = re.sub(r"DECLARE admin_project\s+STRING\s+DEFAULT\s+NULL;",
                     f"DECLARE admin_project         STRING DEFAULT '{c['reservation_admin_project']}';", sql)

    # Smart Auto-Watermark: 180-Day (6-Month) Historical Backfill on 1st Run -> 3-Day Incremental on Daily Runs
    try:
        cnt_rows = bq.query(c, f"SELECT COUNT(1) AS cnt FROM `{c.ops}.jobs_events`")
        existing_cnt = int(cnt_rows[0]["cnt"]) if cnt_rows else 0
    except Exception:
        existing_cnt = 0

    if existing_cnt == 0:
        effective_days = int(c.get("initial_backfill_days", 180))
        print(f"  + First-time historical load detected (0 rows in jobs_events): pulling full {effective_days}-day (6-month) INFORMATION_SCHEMA history...")
    else:
        effective_days = int(c.get("incremental_lookback_days", 3))
        print(f"  + Existing telemetry found ({existing_cnt:,} jobs): running fast {effective_days}-day daily incremental MERGE...")

    sql = re.sub(r"DECLARE lookback_days\s+INT64\s+DEFAULT\s+[^;]+;",
                 f"DECLARE lookback_days         INT64  DEFAULT {effective_days};", sql)
    bq.client(c).query(sql).result()
    print(f"  + Executed SQL collector: sql/02_collector_run.sql (lookback={effective_days}d)")
    target_ds = c.get("target_dataset")
    if target_ds:
        print(f"[*] Filtering telemetry tables to target dataset: {target_ds}...")
        for tbl in ("table_state_daily", "columns_daily", "table_partitions_daily", "dataset_state_daily"):
            bq.execute(c, f"DELETE FROM `{c.ops}.{tbl}` WHERE snapshot_date = CURRENT_DATE() AND dataset_id != @d", {"d": target_ds})
    driver_res = collector_driver.run(c)
    print("  + Backfilled driver metadata:", driver_res)
    print("✅ Telemetry collection completed successfully!")


def cmd_rules(c) -> None:
    expired = store.expire_stale(c)
    findings = rules.run(c)
    target_ds = c.get("target_dataset")
    if target_ds:
        findings = [
            f for f in findings
            if f.get("target_dataset") == target_ds
            or f.get("target_dataset") in (None, "queries", "PROJECT_WIDE_BILLING", "HUMAN_ADHOC_GOVERNANCE")
        ]
        print(f"[*] Filtered rules to target dataset: {target_ds}")
    spend = rules.table_spend_map(c)
    cv = rules.table_volatility_map(c)
    hist = store.rule_history(c)
    for f in findings:
        scoring.score(f, c, table_spend=spend, table_cv=cv, rule_history=hist)
        f["execution_route"], _ = router.route(c, f.get("target_dataset"), f)
        f["owner_principal"], f["owner_source"] = router.resolve_owner(c, f)
    sets, notes = compiler.compile(findings, store.open_sets(c))
    n = store.insert(c, sets)
    print(f"expired={expired} findings={len(findings)} inserted={n} suppressed={len(notes)}")
    for note in notes:
        print("  ", note)


def _dispatch_apply(c, cs) -> dict:
    cls = int(cs["apply_class"])
    if cls == 1 and c["executor"]["enable_class1"]:
        return class1.apply(c, cs)
    if cls == 2 and c["executor"]["enable_class2"]:
        return class2.apply(c, cs)
    if cls == 3:
        return class3.run(c, cs)
    raise router.Blocked(f"class {cls} disabled or unsupported")


def cmd_execute(c) -> None:
    target_ds = c.get("target_dataset")
    for cs in store.approved_ready(c):
        if target_ds and cs.get("target_dataset") != target_ds:
            continue
        target = f"{cs['target_dataset']}.{cs.get('target_table') or '*'}"
        if cs.get("execution_route") == "CI_PULL_REQUEST":
            print(f"skip {cs['change_set_id']} ({target}): CI_PULL_REQUEST route — "
                  f"emit the MR from proposed_change_json (phase-4 wiring)")
            continue
        try:
            router.check_window(c)
            router.check_rate_limit(c, cs)
            store.transition(c, cs["change_set_id"], "APPLYING", "executor")
            verifier.freeze_baseline(c, cs)          # idempotent safety net
            plan = _dispatch_apply(c, cs)
            store.transition(c, cs["change_set_id"], "APPLIED", "executor",
                             extra={"rollback_plan_json": bq.dumps(plan),
                                    "applied_at": bq.query(c, "SELECT CURRENT_TIMESTAMP() AS t")[0]["t"].isoformat()})
            store.transition(c, cs["change_set_id"], "VERIFYING", "executor")
            print(f"applied {cs['change_set_id']} ({target})")
        except router.Blocked as e:
            store.transition(c, cs["change_set_id"], "FAILED", "executor", str(e))
            recommender_sync.mark(cs.get("native_rec_names"), "FAILED")
            print(f"blocked {cs['change_set_id']} ({target}): {e}")
        except Exception as e:  # clean-abort already ran inside class3
            store.transition(c, cs["change_set_id"], "FAILED", "executor", repr(e))
            print(f"failed  {cs['change_set_id']} ({target}): {e}", file=sys.stderr)


def cmd_verify(c) -> None:
    for cs in store.in_state(c, "VERIFYING"):
        print(cs["change_set_id"], "->", verifier.check(c, cs))
    for alert in verifier.watchdogs(c):
        print(alert)


def cmd_sync(c) -> None:
    for cs in store.in_state(c, "REJECTED"):
        for note in recommender_sync.mark(cs.get("native_rec_names"), "DISMISSED"):
            print(note)


def cmd_pipeline(c) -> None:
    # Auto-initialize control plane dataset and tables if they do not exist
    from google.cloud import bigquery
    client = bq.client(c)
    ds_id = f"{c['project_id']}.{c['ops_dataset']}"
    try:
        client.get_dataset(ds_id)
    except Exception:
        print(f"[*] Control plane dataset '{ds_id}' not found. Auto-initializing schema...")
        cmd_init(c)

    print("=" * 60)
    print("▶ STEP 1/4: COLLECTOR (Ingesting metadata & telemetry)")
    print("=" * 60)
    cmd_collect(c)

    print("\n" + "=" * 60)
    print("▶ STEP 2/4: RULES ENGINE (Analyzing patterns & generating findings)")
    print("=" * 60)
    cmd_rules(c)

    print("\n" + "=" * 60)
    print("▶ STEP 3/4: EXECUTOR (Applying APPROVED changes)")
    print("=" * 60)
    cmd_execute(c)

    print("\n" + "=" * 60)
    print("▶ STEP 4/4: VERIFIER (Auditing applied changes & metrics)")
    print("=" * 60)
    cmd_verify(c)

    print("\n" + "=" * 60)
    print("✅ NIGHTLY PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 60)


def _get_user_actor() -> str:
    import os, subprocess
    try:
        res = subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True, timeout=2)
        email = res.stdout.strip()
        if email and "@" in email:
            return email
    except Exception:
        pass
    # Never fabricate an identity for the audit trail: use the OS user as-is,
    # or an explicit unknown marker — no invented email domains.
    return os.getenv("USER") or os.getenv("LOGNAME") or "unknown-operator"


def cmd_rollback(c, change_set_id: str, reason: str = "Manual 1-Click Rollback via CLI", category: str = "REGRESSION_PERFORMANCE", snooze_days: int = 90, actor: str | None = None) -> None:
    cs = store.get(c, change_set_id)
    if not cs:
        sys.exit("no such change set")
    actor_name = actor or _get_user_actor()
    store.transition(c, change_set_id, "ROLLING_BACK", actor_name, note=reason)
    try:
        (class3 if int(cs["apply_class"]) == 3 else class1).rollback(c, cs)
    except Exception as e:
        if "Not found" in str(e) or "404" in str(e):
            print(f"[!] Note: backup table already restored or absent ({e}); completing state transition to ROLLED_BACK")
        else:
            raise
    snooze_until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=snooze_days)).isoformat()
    store.transition(c, change_set_id, "ROLLED_BACK", actor_name, note=reason, extra={
        "rejection_reason": category,
        "rejection_note": f"Rollback post-mortem: {reason}",
        "snooze_until": snooze_until,
    })
    print(f"rolled back {change_set_id} (actor: {actor_name}, category: {category}, snooze: {snooze_days}d, reason: {reason})")


def main() -> None:
    p = argparse.ArgumentParser(prog="bq-optimizer", description="BigQuery Optimization Control Plane CLI")
    p.add_argument("command", choices=["init", "collect", "collector", "collect-backfill", "rules", "execute",
                                       "verify", "sync-recommender", "rollback", "pipeline"],
                   help="Command to run")
    p.add_argument("--id", help="change_set_id (for rollback)")
    p.add_argument("--reason", default="Manual 1-Click Rollback via CLI", help="Rollback audit reason note")
    p.add_argument("--category", default="REGRESSION_PERFORMANCE",
                   choices=["REGRESSION_PERFORMANCE", "REGRESSION_COST", "PIPELINE_BREAK", "DATA_MISMATCH", "OTHER"],
                   help="Rollback post-mortem category for audit and suppression")
    p.add_argument("--snooze-days", type=int, default=90,
                   help="Number of days to auto-snooze/suppress the table after rollback (default: 90)")
    p.add_argument("-p", "--project", help="GCP Project ID override")
    p.add_argument("-d", "--dataset", help="Target specific dataset (omit or use --all-datasets for project-wide)")
    p.add_argument("--all-datasets", action="store_true", help="Explicitly run project-wide across all datasets in the project")
    p.add_argument("-l", "--location", help="BigQuery location override (e.g. US, EU)")
    p.add_argument("-c", "--config", help="Path to custom config YAML file")
    p.add_argument("--org", "--organization", dest="org", action="store_true",
                   help="Collect telemetry across all projects in the organization (JOBS_BY_ORGANIZATION)")
    p.add_argument("--jobs-view", choices=["JOBS", "JOBS_BY_ORGANIZATION"],
                   help="Override jobs view scope (JOBS or JOBS_BY_ORGANIZATION)")
    p.add_argument("--lookback-days", type=int, help="Telemetry lookback window in days (e.g. 3, 7, 14, 30)")
    p.add_argument("--reservation-admin-project",
                   help="Reservation admin project for slot/capacity telemetry")
    
    a = p.parse_args()
    
    overrides = {}
    if a.project:
        overrides["project_id"] = a.project
    if a.dataset:
        overrides["target_dataset"] = a.dataset
    elif a.all_datasets:
        overrides["target_dataset"] = None
    if a.location:
        overrides["location"] = a.location
    if a.org:
        overrides["jobs_view"] = "JOBS_BY_ORGANIZATION"
    elif a.jobs_view:
        overrides["jobs_view"] = a.jobs_view
    if a.lookback_days:
        overrides["lookback_days"] = a.lookback_days
    if a.reservation_admin_project:
        overrides["reservation_admin_project"] = a.reservation_admin_project
        
    c = cfg(path=a.config, **overrides)
    
    try:
        if a.command == "init":
            cmd_init(c)
        elif a.command in ("collect", "collector"):
            cmd_collect(c)
        elif a.command == "collect-backfill":
            print(collector_driver.run(c))
        elif a.command == "rules":
            cmd_rules(c)
        elif a.command == "execute":
            cmd_execute(c)
        elif a.command == "verify":
            cmd_verify(c)
        elif a.command == "sync-recommender":
            cmd_sync(c)
        elif a.command == "rollback":
            cmd_rollback(c, a.id or sys.exit("--id required"),
                         reason=a.reason,
                         category=getattr(a, "category", "REGRESSION_PERFORMANCE"),
                         snooze_days=getattr(a, "snooze_days", 90))
        elif a.command == "pipeline":
            cmd_pipeline(c)
    except Exception as e:
        err_msg = str(e)
        if "404 Not found: Dataset" in err_msg or "optimizer_ops was not found" in err_msg:
            print(f"\n❌ Error: Control plane dataset '{c.ops}' was not found.")
            print("👉 Please initialize the control plane first by running:\n   python -m optimizer.cli init\n   or\n   ./scripts/demo_setup.sh\n")
            sys.exit(1)
        raise


if __name__ == "__main__":
    if sys.prefix == sys.base_prefix:
        import os
        for _vpy in [
            os.path.abspath(".venv/bin/python"),
            os.path.abspath(".venv/bin/python3"),
            os.path.expanduser("~/.venv-bq/bin/python"),
            os.path.expanduser("~/.venv-bq/bin/python3"),
        ]:
            if os.path.exists(_vpy):
                os.execv(_vpy, [_vpy, "-m", "optimizer.cli"] + sys.argv[1:])
    main()
