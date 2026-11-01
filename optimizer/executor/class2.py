"""Class 2 — additive objects with recurring cost (design doc §9.3).

v1 implements materialized-view creation from a proposed DDL plus watchdog
registration. The watchdog check itself runs in verifier.watchdogs().
"""
from __future__ import annotations

from .. import bq, security
from ..config import Config


def _validated_target(change: dict, cs: dict) -> str:
    raw = change.get("target")
    if raw:
        p, d, t = security.validate_fqn(raw)
        return f"{p}.{d}.{t}"
    p = security.validate_project_id(cs["target_project"])
    d = security.validate_dataset_id(cs["target_dataset"])
    t = security.validate_table_id(cs["target_table"])
    return f"{p}.{d}.{t}"


def apply(c: Config, cs: dict) -> dict:
    change = bq.loads(cs["proposed_change_json"]) or {}
    action = change.get("action")
    loc = bq.location_for(cs.get("target_region"))   # DDL on the target runs in its region
    if action == "CREATE_MATERIALIZED_VIEW":
        target = _validated_target(change, cs)
        ddl = change.get("ddl") or change.get("generated_ddl") or ""
        bq.assert_single_statement_ddl(ddl, ("CREATE_MATERIALIZED_VIEW",))
        bq.execute(c, ddl, location=loc)
        bq.execute(c, f"""
            INSERT INTO `{c.ops}.cost_watchdogs`
              (change_set_id, kind, target, monthly_limit_usd, created_at)
            VALUES (@id, 'MV_REFRESH', @target, CAST(@limit AS NUMERIC), CURRENT_TIMESTAMP())""",
            {"id": cs["change_set_id"], "target": target,
             "limit": float(change.get("monthly_limit_usd", 50))})
        rollback_sql = f"DROP MATERIALIZED VIEW IF EXISTS `{target}`"
        return {"action": action, "target": target, "applied_ddl": ddl, "rollback": rollback_sql}

    if action == "CREATE_SEARCH_INDEX":
        target = _validated_target(change, cs)
        ddl = change.get("generated_ddl") or f"CREATE SEARCH INDEX ON `{target}`(ALL COLUMNS);"
        bq.assert_single_statement_ddl(ddl, ("CREATE_SEARCH_INDEX",))
        bq.execute(c, ddl, location=loc)
        bq.execute(c, f"""
            INSERT INTO `{c.ops}.cost_watchdogs`
              (change_set_id, kind, target, monthly_limit_usd, created_at)
            VALUES (@id, 'INDEX_STORAGE', @target, CAST(@limit AS NUMERIC), CURRENT_TIMESTAMP())""",
            {"id": cs["change_set_id"], "target": target,
             "limit": float(change.get("monthly_limit_usd", 20))})
        rollback_sql = f"DROP SEARCH INDEX IF EXISTS ON `{target}`"
        return {"action": action, "target": target, "applied_ddl": ddl, "rollback": rollback_sql}

    raise ValueError(f"class2 cannot apply action {action}")


def rollback(c: Config, cs: dict) -> None:
    plan = bq.loads(cs.get("rollback_plan_json")) or {}
    loc = bq.location_for(cs.get("target_region"))
    action = plan.get("action")
    if plan.get("target") and action in ("CREATE_MATERIALIZED_VIEW", "CREATE_SEARCH_INDEX"):
        p, d, t = security.validate_fqn(plan["target"])
        qtarget = f"`{p}.{d}.{t}`"
        sql = (
            f"DROP MATERIALIZED VIEW IF EXISTS {qtarget}"
            if action == "CREATE_MATERIALIZED_VIEW"
            else f"DROP SEARCH INDEX IF EXISTS ON {qtarget}"
        )
        bq.execute(c, sql, location=loc)
        return
    if plan.get("rollback"):
        rb_sql = str(plan["rollback"])
        bq.assert_single_statement_ddl(rb_sql, ("DROP_MATERIALIZED_VIEW", "DROP_SEARCH_INDEX"))
        bq.execute(c, rb_sql, location=loc)
