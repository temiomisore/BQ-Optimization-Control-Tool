"""Change-set store — all reads/writes of optimizer_ops.change_sets.

DML (not streaming inserts) throughout, so rows are immediately updatable and
the state machine never fights the streaming buffer. Every transition appends
to state_history; nothing is ever overwritten silently.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from . import bq
from .config import Config

Finding = dict[str, Any]

REJECTION_SNOOZE_DAYS = {
    "BREAKS_PIPELINE": 90,
    "TABLE_DEPRECATED": 365,
    "SAVINGS_NOT_CREDIBLE": 60,
    "WRONG_OWNER": 14,
    "TIMING": 30,
    "OTHER": 60,
    "REGRESSION_PERFORMANCE": 90,
    "REGRESSION_COST": 90,
    "PIPELINE_BREAK": 90,
    "DATA_MISMATCH": 90,
    "MANUAL_REVERT": 90,
}


def _t(c: Config) -> str:
    return f"`{c.ops}.change_sets`"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def open_sets(c: Config) -> list[dict]:
    return bq.query(c, f"""
        SELECT change_set_id, rule_ids, apply_class, state,
               target_project, target_dataset, target_table
        FROM {_t(c)}
        WHERE state IN ('PENDING_REVIEW','APPROVED','SCHEDULED','APPLYING','APPLIED','VERIFYING')
           OR (state IN ('REJECTED', 'ROLLED_BACK') AND snooze_until > CURRENT_TIMESTAMP())
           OR (state = 'VERIFIED' AND applied_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY))""")


def get(c: Config, change_set_id: str) -> dict | None:
    rows = bq.query(c, f"SELECT * FROM {_t(c)} WHERE change_set_id = @id",
                    {"id": change_set_id})
    return rows[0] if rows else None


def pending(c: Config) -> list[dict]:
    return bq.query(c, f"SELECT * FROM `{c.ops}.v_pending_review`")


def approved_ready(c: Config) -> list[dict]:
    return bq.query(c, f"SELECT * FROM `{c.ops}.v_approved_ready`")


def in_state(c: Config, *states: str) -> list[dict]:
    return bq.query(
        c, f"SELECT * FROM {_t(c)} WHERE state IN UNNEST(@s)", {"s": list(states)})


def rule_history(c: Config) -> dict[str, float]:
    rows = bq.query(c, f"SELECT rule_id, realized_over_predicted FROM `{c.ops}.rule_accuracy`")
    return {r["rule_id"]: float(r["realized_over_predicted"] or 1.0) for r in rows}


def recent_structural_change(c: Config, target: tuple, days: int) -> bool:
    rows = bq.query(c, f"""
        SELECT COUNT(*) AS n FROM {_t(c)}
        WHERE target_project=@p AND target_dataset=@d AND COALESCE(target_table,'')=COALESCE(@t,'')
          AND apply_class = 3 AND applied_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)""",
        {"p": target[0], "d": target[1], "t": target[2], "days": days})
    return int(rows[0]["n"]) > 0


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------

def insert(c: Config, sets: Iterable[Finding], actor: str = "rules-engine") -> int:
    n = 0
    stale = int(c["evidence_stale_days"])
    for f in sets:
        bq.execute(c, f"""
          INSERT INTO {_t(c)} (
            change_set_id, created_at, rule_ids, apply_class, source, native_rec_names,
            target_project, target_dataset, target_table, target_region,
            finding_summary, evidence_json, observation_days, current_config_ddl,
            proposed_change_json, execution_route, owner_principal, owner_source,
            gross_monthly_savings_usd, recurring_monthly_cost_usd, one_time_apply_cost_usd,
            net_monthly_value_usd, savings_basis, confidence, confidence_factors_json, score,
            blast_radius_json, risk_notes, state, state_history, expires_at)
          VALUES (
            @id, CURRENT_TIMESTAMP(), @rule_ids, @cls, @source, @native,
            @tp, @td, @tt, @tr,
            @summary, @evidence, @obs, @ddl,
            @proposed, @route, @owner, @owner_src,
            CAST(@gross AS NUMERIC), CAST(@recur AS NUMERIC), CAST(@once AS NUMERIC),
            CAST(@net AS NUMERIC), @basis, CAST(@conf AS NUMERIC), @factors, CAST(@score AS NUMERIC),
            @blast, @risks, 'PENDING_REVIEW',
            [STRUCT('PENDING_REVIEW' AS state, CURRENT_TIMESTAMP() AS `at`, @actor AS actor,
                    CAST(NULL AS STRING) AS note)],
            TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @stale DAY))""",
          {
            "id": f["change_set_id"], "rule_ids": f.get("rule_ids") or [f["rule_id"]],
            "cls": int(f["apply_class"]), "source": f.get("source"),
            "native": f.get("native_rec_names") or [],
            "tp": f.get("target_project"), "td": f.get("target_dataset"),
            "tt": f.get("target_table"), "tr": f.get("target_region"),
            "summary": f.get("finding_summary"), "evidence": bq.dumps(f.get("evidence") or {}),
            "obs": int(f.get("observation_days") or 0), "ddl": f.get("current_config_ddl"),
            "proposed": bq.dumps(f.get("proposed_change") or {}),
            "route": f.get("execution_route", "DIRECT_GUARDED"),
            "owner": f.get("owner_principal"), "owner_src": f.get("owner_source", "UNKNOWN"),
            "gross": float(f.get("gross_monthly_savings_usd") or 0),
            "recur": float(f.get("recurring_monthly_cost_usd") or 0),
            "once": float(f.get("one_time_apply_cost_usd") or 0),
            "net": float(f.get("net_monthly_value_usd") or 0),
            "basis": f.get("savings_basis"), "conf": float(f.get("confidence") or 0),
            "factors": bq.dumps(f.get("confidence_factors") or {}),
            "score": float(f.get("score") or 0),
            "blast": bq.dumps(f.get("blast_radius") or {}),
            "risks": f.get("risk_notes") or [], "actor": actor, "stale": stale,
          })
        n += 1
    return n


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def transition(c: Config, change_set_id: str, new_state: str, actor: str,
               note: str | None = None, extra: dict[str, Any] | None = None) -> None:
    sets = ["state = @state",
            ("state_history = ARRAY_CONCAT(state_history, "
             "[STRUCT(@state AS state, CURRENT_TIMESTAMP() AS `at`, @actor AS actor, @note AS note)])")]
    params: dict[str, Any] = {"id": change_set_id, "state": new_state,
                              "actor": actor, "note": note}
    for col, val in (extra or {}).items():
        pname = f"x_{col}"
        if col.endswith("_usd") or col in ("realized_over_predicted",):
            sets.append(f"{col} = CAST(@{pname} AS NUMERIC)")
        elif col in ("applied_at", "snooze_until", "expires_at"):
            sets.append(f"{col} = TIMESTAMP(@{pname})")
        else:
            sets.append(f"{col} = @{pname}")
        params[pname] = val
    bq.execute(c, f"UPDATE {_t(c)} SET {', '.join(sets)} WHERE change_set_id = @id", params)


def approve(c: Config, change_set_id: str, principal: str, role: str = "approver",
            note: str | None = None) -> str:
    """Records an approval. For Class 3 changes, requires two-person approval per Design Doc §8
    (Owner + Platform Admin) before transitioning state to APPROVED.
    Returns 'APPROVED' if fully approved, or 'PARTIALLY_APPROVED' if awaiting 2nd signature."""
    cs = get(c, change_set_id)
    if not cs:
        raise ValueError(f"Change set {change_set_id} not found")

    cls = int(cs.get("apply_class") or 1)
    approvals = cs.get("approvals") or []

    # Class 3 two-person approval enforcement
    if cls == 3 and len(approvals) == 0:
        actual_role = role if role != "approver" else "owner_approver"
        bq.execute(c, f"""
            UPDATE {_t(c)} SET
              approvals = ARRAY_CONCAT(COALESCE(approvals, []),
                  [STRUCT(@p AS principal, CURRENT_TIMESTAMP() AS `at`, @role AS role)]),
              state_history = ARRAY_CONCAT(state_history,
                  [STRUCT('PENDING_REVIEW' AS state, CURRENT_TIMESTAMP() AS `at`, @p AS actor,
                          'First approval recorded (1/2). Awaiting platform signoff.' AS note)])
            WHERE change_set_id = @id AND state = 'PENDING_REVIEW'""",
            {"id": change_set_id, "p": principal, "role": actual_role})
        return "PARTIALLY_APPROVED"

    actual_role = role if role != "approver" else ("platform_approver" if cls == 3 else "approver")
    bq.execute(c, f"""
        UPDATE {_t(c)} SET
          state = 'APPROVED',
          approvals = ARRAY_CONCAT(COALESCE(approvals, []),
              [STRUCT(@p AS principal, CURRENT_TIMESTAMP() AS `at`, @role AS role)]),
          expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @ttl DAY),
          state_history = ARRAY_CONCAT(state_history,
              [STRUCT('APPROVED' AS state, CURRENT_TIMESTAMP() AS `at`, @p AS actor,
                      @note AS note)])
        WHERE change_set_id = @id AND state = 'PENDING_REVIEW'""",
        {"id": change_set_id, "p": principal, "role": actual_role,
         "ttl": int(c["approval_ttl_days"]), "note": note or f"Approved by {principal}"})
    return "APPROVED"


def reject(c: Config, change_set_id: str, principal: str, reason: str, note: str | None) -> None:
    days = REJECTION_SNOOZE_DAYS.get(reason, 60)
    transition(c, change_set_id, "REJECTED", principal, note, extra={
        "rejection_reason": reason, "rejection_note": note,
        "snooze_until": (dt.datetime.now(dt.timezone.utc)
                         + dt.timedelta(days=days)).isoformat()})


def unsnooze(c: Config, change_set_id: str, principal: str, note: str | None = None) -> None:
    """Un-snoozes a rejected or rolled-back change set so it can be re-evaluated in the review queue.
    Clears active approvals so the new review cycle requires fresh sign-off while preserving state_history."""
    bq.execute(c, f"""
        UPDATE {_t(c)} SET
          state = 'PENDING_REVIEW',
          snooze_until = NULL,
          approvals = [],
          state_history = ARRAY_CONCAT(state_history,
              [STRUCT('PENDING_REVIEW' AS state, CURRENT_TIMESTAMP() AS `at`, @p AS actor,
                      @note AS note)])
        WHERE change_set_id = @id""",
        {"id": change_set_id, "p": principal,
         "note": note or f"Un-snoozed by {principal} for re-evaluation"})


def expire_stale(c: Config) -> int:
    return bq.execute(c, f"""
        UPDATE {_t(c)} SET state = 'EXPIRED',
          state_history = ARRAY_CONCAT(state_history,
            [STRUCT('EXPIRED' AS state, CURRENT_TIMESTAMP() AS `at`, 'sweeper' AS actor,
                    'evidence or approval TTL elapsed' AS note)])
        WHERE state IN ('PENDING_REVIEW','APPROVED')
          AND expires_at < CURRENT_TIMESTAMP()""")
