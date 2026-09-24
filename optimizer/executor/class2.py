"""Class 2 — additive objects with recurring cost (design doc §9.3).

v1 implements materialized-view creation from a proposed DDL plus watchdog
registration. The watchdog check itself runs in verifier.watchdogs().
"""
from __future__ import annotations

from .. import bq
from ..config import Config


def apply(c: Config, cs: dict) -> dict:
    change = bq.loads(cs["proposed_change_json"]) or {}
    action = change.get("action")
    if action == "CREATE_MATERIALIZED_VIEW":
        ddl = change.get("ddl") or change.get("generated_ddl")
        bq.execute(c, ddl)
        bq.execute(c, f"""
            INSERT INTO `{c.ops}.cost_watchdogs`
              (change_set_id, kind, target, monthly_limit_usd, created_at)
            VALUES (@id, 'MV_REFRESH', @target, CAST(@limit AS NUMERIC), CURRENT_TIMESTAMP())""",
            {"id": cs["change_set_id"], "target": change.get("target"),
             "limit": float(change.get("monthly_limit_usd", 50))})
        return {"applied_ddl": ddl, "rollback": f"DROP MATERIALIZED VIEW {change.get('target')}"}

    if action == "CREATE_SEARCH_INDEX":
        ddl = change.get("generated_ddl") or f"CREATE SEARCH INDEX ON {change.get('target')}(ALL COLUMNS);"
        bq.execute(c, ddl)
        bq.execute(c, f"""
            INSERT INTO `{c.ops}.cost_watchdogs`
              (change_set_id, kind, target, monthly_limit_usd, created_at)
            VALUES (@id, 'INDEX_STORAGE', @target, CAST(@limit AS NUMERIC), CURRENT_TIMESTAMP())""",
            {"id": cs["change_set_id"], "target": change.get("target"),
             "limit": float(change.get("monthly_limit_usd", 20))})
        return {"applied_ddl": ddl, "rollback": f"DROP SEARCH INDEX IF EXISTS ON {change.get('target')}"}

    raise ValueError(f"class2 cannot apply action {action}")


def rollback(c: Config, cs: dict) -> None:
    plan = bq.loads(cs.get("rollback_plan_json")) or {}
    if plan.get("rollback"):
        bq.execute(c, plan["rollback"])
