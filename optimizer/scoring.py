"""Scoring — pure logic, no GCP imports (design doc §6).

score = net_monthly_value * confidence / risk_weight[class]
confidence = base * d_summation * d_window * d_volatility * d_history
"""
from __future__ import annotations

from typing import Any

Finding = dict[str, Any]

HISTORY_CLAMP = (0.25, 1.25)


# Conservative ceiling on how much of a table's spend a native recommendation
# may claim as savings (i.e. assumed maximum scan-reduction fraction). Override
# per deployment with config key `native_cap_scan_reduction`.
NATIVE_CAP_SCAN_REDUCTION = 0.68


def apply_summation_cap(f: Finding, table_spend: dict[tuple, float],
                        max_scan_reduction: float = NATIVE_CAP_SCAN_REDUCTION) -> float:
    """Google's clustering estimates can exceed a table's real monthly spend
    (per-stage 'subquery summation'). Cap the claim at
    spend x max_scan_reduction — always computed from the table's REAL
    observed spend, never from a preset figure — and return the discount
    factor applied to confidence."""
    key = (f.get("target_project"), f.get("target_dataset"), f.get("target_table"))
    spend = table_spend.get(key)
    gross = float(f.get("gross_monthly_savings_usd") or 0)
    if f.get("source") == "NATIVE_RECOMMENDER" and spend is not None and spend > 0:
        ceiling = round(spend * float(max_scan_reduction), 2)
        if gross > ceiling:
            f["gross_monthly_savings_usd"] = ceiling
            f.setdefault("risk_notes", []).append("NATIVE_ESTIMATE_CAPPED_AT_TABLE_SPEND")
            return 0.5
    return 1.0


def confidence(f: Finding, *, table_cv: dict[tuple, float],
               rule_history: dict[str, float], d_summation: float) -> tuple[float, dict]:
    base = float(f.get("confidence_hint", 0.6))
    d_window = min(1.0, float(f.get("observation_days") or 0) / 30.0) or 0.2
    key = (f.get("target_project"), f.get("target_dataset"), f.get("target_table"))
    cv = table_cv.get(key, 0.0)
    d_volatility = 1.0 / (1.0 + cv)
    hist = rule_history.get(f["rule_id"], 1.0)
    d_history = max(HISTORY_CLAMP[0], min(HISTORY_CLAMP[1], hist))
    conf = base * d_summation * d_window * d_volatility * d_history
    return round(min(conf, 1.0), 4), {
        "base": base, "d_summation": d_summation, "d_window": round(d_window, 3),
        "d_volatility": round(d_volatility, 3), "d_history": round(d_history, 3)}


def score(f: Finding, cfg: dict, *, table_spend: dict[tuple, float],
          table_cv: dict[tuple, float], rule_history: dict[str, float]) -> Finding:
    d_sum = apply_summation_cap(
        f, table_spend,
        max_scan_reduction=float(cfg.get("native_cap_scan_reduction", NATIVE_CAP_SCAN_REDUCTION)))
    conf, factors = confidence(f, table_cv=table_cv, rule_history=rule_history, d_summation=d_sum)
    gross = float(f.get("gross_monthly_savings_usd") or 0)
    recurring = float(f.get("recurring_monthly_cost_usd") or 0)
    one_time = float(f.get("one_time_apply_cost_usd") or 0)
    amort = int(cfg.get("amortization_months", 12))
    net = gross - recurring - one_time / amort
    risk_w = float(cfg.get("risk_weights", {}).get(int(f["apply_class"]), 1.5))
    f.update(
        net_monthly_value_usd=round(net, 2),
        confidence=conf,
        confidence_factors=factors,
        score=round(max(net, 0.0) * conf / risk_w, 2),
    )
    return f
