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

from . import (attribution, bq, collector_driver, compiler, governance, rules, scoring, store,
               verifier)
from .config import cfg
from .executor import class1, class2, class3, pr_handoff, recommender_sync, router


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
    # Point the attribution source views at the customer's own employee-hierarchy and
    # service-account owner tables (config: employee_hierarchy_table/_columns,
    # service_account_owner_table/_columns). Nothing configured = local optimizer_ops tables.
    try:
        stmts = attribution.source_view_sql(c)
    except attribution.AttributionConfigError as e:
        sys.exit(f"[!] attribution config error: {e}")
    for stmt in stmts:
        bq.execute(c, stmt)
        print("  + Attribution source view:", stmt.splitlines()[0].replace("CREATE OR REPLACE VIEW ", ""))
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


# Findings that are not tied to one dataset stay in scope for dataset-scoped runs.
_CROSS_DATASET_TARGETS = (None, "queries", "PROJECT_WIDE_BILLING", "HUMAN_ADHOC_GOVERNANCE")


def _in_scope(item: dict, target_ds: str | None) -> bool:
    return (not target_ds or item.get("target_dataset") == target_ds
            or item.get("target_dataset") in _CROSS_DATASET_TARGETS)


def _annotate_query_runner(f: dict, sa_owners: dict) -> None:
    """Class 4 (SQL rewrite) cards: record who answers for the query's spend. A service
    account (PowerBI, ETL ...) resolves to its human owner through the SA owner table."""
    ev = f.setdefault("evidence", {})
    runner = ev.get("sample_user_email")
    acct = router.accountable_owner(runner, sa_owners)
    if acct:
        ev["accountable_owner"] = acct
    elif runner and attribution.is_service_account(runner):
        notes = f.setdefault("risk_notes", [])
        if "QUERY_RUN_BY_UNMAPPED_SERVICE_ACCOUNT" not in notes:
            notes.append("QUERY_RUN_BY_UNMAPPED_SERVICE_ACCOUNT")


def cmd_rules(c) -> None:
    expired = store.expire_stale(c)
    findings = rules.run(c)
    target_ds = c.get("target_dataset")
    if target_ds:
        findings = [f for f in findings if _in_scope(f, target_ds)]
        print(f"[*] Filtered rules to target dataset: {target_ds}")
    spend = rules.table_spend_map(c)
    cv = rules.table_volatility_map(c)
    hist = store.rule_history(c)
    sa_owners = attribution.load_sa_owner_map(c)
    if sa_owners:
        print(f"[*] Service-account owner map: {len(sa_owners)} service account(s) mapped to owners")
    for f in findings:
        scoring.score(f, c, table_spend=spend, table_cv=cv, rule_history=hist)
        f["execution_route"], _ = router.route(c, f.get("target_dataset"), f)
        f["owner_principal"], f["owner_source"] = router.resolve_owner(c, f, sa_owners)
        if int(f.get("apply_class") or 0) == 4:
            _annotate_query_runner(f, sa_owners)
    open_sets = store.open_sets(c)

    # Re-price (not duplicate) every card that is still awaiting its first approval and
    # was detected again, so reviewers always see this run's billing-aware number.
    refreshed = 0
    for cs_id, f in compiler.pending_refreshes(findings, open_sets, c):
        store.refresh_pending(c, cs_id, f)
        refreshed += 1
    if refreshed:
        print(f"[*] Re-priced {refreshed} pending card(s) still awaiting their first approval")

    # Unapproved cards priced by the old (not billing-aware) math and not detected again:
    # retire them so a stale number never stays on the board. Query cards from the
    # non-deterministic engines are re-priced from the query's current cost instead.
    legacy = [cs for cs in store.unapproved_legacy_pending(c) if _in_scope(cs, target_ds)]
    retired = 0
    for cs in compiler.stale_legacy_cards(findings, legacy):
        store.transition(c, cs["change_set_id"], "SUPERSEDED", "rules-engine",
                         "Retired: priced by the pre-billing-aware engine and not detected again; "
                         "re-created with billing-aware savings if the finding still applies")
        retired += 1
    repriced, unpriceable = rules.reprice_legacy_query_cards(
        c, compiler.legacy_to_reprice(findings, legacy))
    for cs_id, r in repriced:
        store.reprice_pending(c, cs_id, evidence=r["evidence"], gross=r["gross"], net=r["net"],
                              score=r["score"], basis=r["basis"],
                              note=(f"Re-priced with billing-aware math: "
                                    f"${r['old_gross']:,.2f} -> ${r['gross']:,.2f}/mo"))
    for cs in unpriceable:
        store.transition(c, cs["change_set_id"], "SUPERSEDED", "rules-engine",
                         "Retired: legacy estimate could not be re-priced (query no longer runs "
                         "in the 28-day window, or the card has no recorded reduction ratio)")
        retired += 1
    if retired or repriced:
        print(f"[*] Legacy estimates: re-priced {len(repriced)}, retired {retired}")

    sets, notes = compiler.compile(findings, open_sets, c)
    n = store.insert(c, sets)
    floors = sum(1 for f in findings if "DEMO_SYNTHETIC_FLOOR_APPLIED" in (f.get("risk_notes") or []))
    print(f"expired={expired} findings={len(findings)} inserted={n} suppressed={len(notes)} "
          f"refreshed={refreshed} retired={retired}"
          + (f" demo_floors={floors} (demo_mode is on: synthetic minimums applied)" if floors else ""))
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
        # Defense in depth (the review UI already refuses with 403): a billing, commitment,
        # reservation or project-level card runs only if FinOps approvers approved it.
        # Otherwise it goes back to the queue for a FinOps approver - recoverable, not FAILED.
        block = governance.executor_block_reason(cs, c)
        if block:
            store.send_back_for_reapproval(c, cs["change_set_id"], "executor", block)
            print(f"sent back {cs['change_set_id']} ({target}) for FinOps re-approval: {block}")
            continue
        if cs.get("execution_route") == "CI_PULL_REQUEST":
            # Code changes (Class 4 SQL rewrites) are never applied directly: hand them off
            # to an engineer as a ready-to-open pull request instead of skipping forever.
            try:
                pkg = pr_handoff.hand_off(c, cs)
                print(f"handed off {cs['change_set_id']} ({target}) for code review -> {pr_handoff.HANDOFF_STATE}\n"
                      f"    branch : {pkg['branch']}\n"
                      f"    file   : {pkg['file_path']}\n"
                      f"    open PR: {pkg['gh_command']}\n"
                      f"    next   : after the PR merges, click 'Mark PR merged' in the review UI "
                      f"(PR hand-offs tab) to start savings verification")
            except Exception as e:
                print(f"failed  {cs['change_set_id']} ({target}): PR hand-off error: {e}", file=sys.stderr)
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
        if cs.get("execution_route") == "CI_PULL_REQUEST" or int(cs["apply_class"]) == 4:
            # Code change shipped via PR: nothing to undo in BigQuery; the PR must be reverted in git.
            plan = bq.loads(cs.get("rollback_plan_json")) or {}
            print(f"[!] Class 4 code change: revert the PR in git"
                  f"{' (' + plan['pr_url'] + ')' if plan.get('pr_url') else ''}; no BigQuery objects to restore")
        else:
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
