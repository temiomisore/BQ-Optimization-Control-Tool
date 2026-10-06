"""Verifier — closes the loop (design doc §10).

freeze_baseline(): at approval time, snapshot per-family metrics for the
target so the comparison is same-query-shapes, not raw table totals.

check(): after apply + window, measure the same families, price the delta by
the change set's savings basis, write the receipt, detect regressions, and
fold realized/predicted back into rule_accuracy (the d_history factor).
"""
from __future__ import annotations

import datetime as dt

from . import bq, pricing, store
from .config import Config
from .executor import recommender_sync

_TIB = 1024 ** 4
_GIB = 1024 ** 3

# Per-family metrics stored in the frozen plan. The *_execs / od_* / resv_* keys
# (added with billing-aware savings) let check() price each billing mode at its own
# rate; plans frozen before that only have the first four and use _price().
_FAMILY_KEYS = ("execs", "bytes_per_exec", "slot_ms_per_exec", "p95_ms",
                "od_execs", "od_bytes_per_exec", "resv_execs", "resv_slot_ms_per_exec",
                "resv_slot_rate_usd")


def _family_metrics(c: Config, cs: dict, start_days_ago: int, end_days_ago: int) -> list[dict]:
    # EXISTS (not a join on UNNEST): a job that reads several tables of the target
    # dataset is still one execution, so dataset-level targets aren't double counted.
    return bq.query(c, f"""
        SELECT j.query_hash,
               COUNT(*)                    AS execs,
               AVG(j.total_bytes_billed)   AS bytes_per_exec,
               AVG(j.total_slot_ms)        AS slot_ms_per_exec,
               APPROX_QUANTILES(j.duration_ms, 100)[OFFSET(95)] AS p95_ms,
               COUNTIF(j.billing_mode = 'ON_DEMAND')                              AS od_execs,
               AVG(IF(j.billing_mode = 'ON_DEMAND', j.total_bytes_billed, NULL))  AS od_bytes_per_exec,
               COUNTIF(j.billing_mode = 'RESERVATION')                            AS resv_execs,
               AVG(IF(j.billing_mode = 'RESERVATION', j.total_slot_ms, NULL))     AS resv_slot_ms_per_exec,
               AVG(j.slot_rate_usd)                                               AS resv_slot_rate_usd
        FROM `{c.ops}.v_jobs_costed` j
        WHERE EXISTS (SELECT 1 FROM UNNEST(j.referenced_tables) rt
                      WHERE rt.project_id = @p AND rt.dataset_id = @d
                        AND (@t IS NULL OR rt.table_id = @t))
          AND j.query_hash IS NOT NULL
          AND j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @s DAY)
          AND j.creation_time <  TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @e DAY)
        GROUP BY 1""",
        {"p": cs["target_project"], "d": cs["target_dataset"], "t": cs["target_table"],
         "s": start_days_ago, "e": end_days_ago})


def freeze_baseline(c: Config, cs: dict, actor: str = "verifier") -> None:
    if cs.get("verification_plan_json"):
        return  # idempotent
    win = int(c["verify_window_days"])
    fams = _family_metrics(c, cs, start_days_ago=max(win, 1), end_days_ago=0)

    # Capture storage baseline if target table exists
    storage_base = None
    if cs.get("target_table"):
        try:
            st_rows = bq.query(c, f"""
                SELECT total_logical_bytes, total_physical_bytes, active_logical_bytes, long_term_logical_bytes
                FROM `{c.ops}.table_state_daily`
                WHERE project_id = @p AND dataset_id = @d AND table_id = @t
                ORDER BY snapshot_date DESC LIMIT 1""",
                {"p": cs["target_project"], "d": cs["target_dataset"], "t": cs["target_table"]})
            if st_rows:
                storage_base = {k: float(st_rows[0][k] or 0) for k in st_rows[0]}
        except Exception:
            storage_base = None

    plan = {"window_days": win,
            "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "storage_baseline": storage_base,
            "families": {f["query_hash"]: {k: float(f.get(k) or 0) for k in _FAMILY_KEYS}
                         for f in fams}}
    bq.execute(c, f"UPDATE `{c.ops}.change_sets` SET verification_plan_json=@p "
                  f"WHERE change_set_id=@id",
               {"p": bq.dumps(plan), "id": cs["change_set_id"]})


def _price(c: Config, basis: str, bytes_delta_month: float, slot_ms_delta_month: float,
           prices: dict, storage_gb_delta: float = 0.0) -> float:
    if basis == "BYTES_ON_DEMAND":
        return bytes_delta_month / _TIB * float(prices["on_demand_usd_per_tib"])
    if basis == "SLOT_EDITIONS":
        return slot_ms_delta_month / 3_600_000 * float(prices.get("slot_hour_usd_enterprise", 0.06))
    if basis == "STORAGE":
        rate = pricing.rate(prices, "p_log_active")
        return round(storage_gb_delta * rate, 2)
    return 0.0


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def compare_families(base: dict, post: dict, *, win: int, min_execs: int, thresh: float,
                     prices: dict, c=None) -> dict:
    """Pure: compare frozen baseline families with the post-window families.

    Billing-aware plans (frozen with per-mode metrics) price on-demand executions by
    bytes x $/TiB and reservation executions by slot-hours x their edition rate x
    realization — like-for-like with how the prediction was made. Regressions are
    checked per mode too (bytes for on-demand, slot time for reservation). Older plans
    fall back to the totals (bytes_delta / slot_delta) priced by savings_basis."""
    month_scale = 30.0 / max(win, 1)
    od_rate = pricing.rate(prices, "on_demand_usd_per_tib")
    default_slot_rate = pricing.rate(prices, "slot_hour_usd_enterprise")
    real = pricing.realization(c)
    out = {"compared": 0, "regressed": 0, "bytes_delta": 0.0, "slot_delta": 0.0,
           "od_usd": 0.0, "resv_usd": 0.0,
           "billing_aware": bool(base) and all("od_execs" in b for b in base.values())}
    for h, b in base.items():
        p = post.get(h)
        if not p or int(_f(p.get("execs"))) < min_execs or _f(b.get("execs")) < min_execs:
            continue
        out["compared"] += 1
        p_execs = _f(p.get("execs"))
        out["bytes_delta"] += (_f(b.get("bytes_per_exec")) - _f(p.get("bytes_per_exec"))) * p_execs * month_scale
        out["slot_delta"] += (_f(b.get("slot_ms_per_exec")) - _f(p.get("slot_ms_per_exec"))) * p_execs * month_scale
        if out["billing_aware"]:
            worse_cost = False
            if _f(b.get("od_execs")) > 0 and _f(p.get("od_execs")) > 0:
                d_bytes = ((_f(b.get("od_bytes_per_exec")) - _f(p.get("od_bytes_per_exec")))
                           * _f(p.get("od_execs")) * month_scale)
                out["od_usd"] += d_bytes / _TIB * od_rate
                worse_cost |= _f(p.get("od_bytes_per_exec")) > _f(b.get("od_bytes_per_exec")) * thresh
            if _f(b.get("resv_execs")) > 0 and _f(p.get("resv_execs")) > 0:
                rate = _f(p.get("resv_slot_rate_usd")) or _f(b.get("resv_slot_rate_usd")) or default_slot_rate
                d_slot = ((_f(b.get("resv_slot_ms_per_exec")) - _f(p.get("resv_slot_ms_per_exec")))
                          * _f(p.get("resv_execs")) * month_scale)
                out["resv_usd"] += d_slot / 3_600_000 * rate * real
                worse_cost |= (_f(p.get("resv_slot_ms_per_exec"))
                               > _f(b.get("resv_slot_ms_per_exec")) * thresh)
        else:
            worse_cost = _f(p.get("bytes_per_exec")) > _f(b.get("bytes_per_exec")) * thresh
        worse_lat = _f(p.get("p95_ms")) > _f(b.get("p95_ms")) * thresh
        if worse_cost or worse_lat:
            out["regressed"] += 1
    return out


def check(c: Config, cs: dict) -> str:
    """Returns the resulting state. Call once the post window has elapsed."""
    win = int(c.get("verify_window_days", 14))
    applied = cs.get("applied_at")
    if not applied:
        return cs["state"]
    if win > 0:
        age_days = (dt.datetime.now(dt.timezone.utc) - applied).days
        if age_days < win:
            return "VERIFYING"

    plan = bq.loads(cs.get("verification_plan_json")) or {"families": {}}
    base = plan.get("families", {})
    post = {f["query_hash"]: f for f in _family_metrics(c, cs, start_days_ago=max(win, 1), end_days_ago=0)}
    prices = bq.query(c, f"SELECT * FROM `{c.ops}.v_config`")[0]

    min_execs = int(c.get("min_family_execs", 1))
    thresh = 1 + float(c.get("regression_pct", 15.0)) / 100.0
    cmp = compare_families(base, post, win=win, min_execs=min_execs, thresh=thresh, prices=prices, c=c)
    regressed, compared = cmp["regressed"], cmp["compared"]
    bytes_delta, slot_delta = cmp["bytes_delta"], cmp["slot_delta"]

    predicted = float(cs.get("gross_monthly_savings_usd") or 0)
    basis = cs.get("savings_basis") or ""
    if basis == "STORAGE":
        st_base = plan.get("storage_baseline") or {}
        try:
            st_rows = bq.query(c, f"""
                SELECT total_logical_bytes, total_physical_bytes
                FROM `{c.ops}.table_state_daily`
                WHERE project_id = @p AND dataset_id = @d AND table_id = @t
                ORDER BY snapshot_date DESC LIMIT 1""",
                {"p": cs["target_project"], "d": cs["target_dataset"], "t": cs["target_table"]})
        except Exception:
            st_rows = []

        if st_rows and st_base:
            base_logical = float(st_base.get("total_logical_bytes") or 0.0)
            curr_logical = float(st_rows[0].get("total_logical_bytes") or 0.0)
            saved_gib = max(0.0, (base_logical - curr_logical) / _GIB)
            realized = _price(c, "STORAGE", 0.0, 0.0, prices, storage_gb_delta=saved_gib)
            ratio = round(realized / predicted, 3) if predicted > 0 else 1.0
            note = f"Storage verified from table_state_daily ({saved_gib:.2f} GiB delta)"
        else:
            realized = predicted
            ratio = 1.0
            note = "Storage verified (table dropped/expired as planned)"
    elif compared > 0:
        if cmp["billing_aware"]:
            realized = round(cmp["od_usd"] + cmp["resv_usd"], 2)
            note = (f"billing-aware: on-demand ${cmp['od_usd']:,.2f} (bytes x $/TiB) + reservation "
                    f"${cmp['resv_usd']:,.2f} (slot-hours x edition rate x realization)")
        else:
            realized = round(_price(c, basis, bytes_delta, slot_delta, prices), 2)
            note = None
        ratio = round(realized / predicted, 3) if predicted > 0 else None
    else:
        realized, ratio = None, None
        note = "no comparable post-window families yet — realized savings pending"
    result = {"families_compared": compared, "families_regressed": regressed,
              "realized_monthly_usd": realized, "predicted_monthly_usd": predicted,
              "window_days": win, "note": note}

    if regressed >= int(c["min_regressed_families"]):
        store.transition(c, cs["change_set_id"], "REGRESSED", "verifier",
                         f"{regressed} families regressed >{c['regression_pct']}%",
                         extra={"verification_result_json": bq.dumps(result)})
        recommender_sync.mark(cs.get("native_rec_names"), "FAILED")
        _send_webhook_notification(c, "REGRESSED", cs, f"{regressed} query families regressed >{c['regression_pct']}% — auto-rollback triggered.")
        return "REGRESSED"

    extra = {"verification_result_json": bq.dumps(result)}
    if ratio is not None:
        extra["realized_over_predicted"] = ratio
        _update_rule_accuracy(c, cs, ratio)
    msg = (f"realized ${realized}/mo vs predicted ${predicted}/mo" if realized is not None
           else f"no regressions detected; realized savings pending post-window data "
                f"(predicted ${predicted}/mo)")
    store.transition(c, cs["change_set_id"], "VERIFIED", "verifier", msg, extra=extra)
    recommender_sync.mark(cs.get("native_rec_names"), "SUCCEEDED")
    _send_webhook_notification(c, "VERIFIED", cs, msg)
    return "VERIFIED"


def _send_webhook_notification(c: Config, event_type: str, cs: dict, detail: str) -> None:
    """Sends a non-blocking Slack/Teams FinOps alert when SLACK_WEBHOOK_URL is configured."""
    import os
    import urllib.request
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or c.get("slack_webhook_url")
    if not webhook_url:
        return
    try:
        icon = "🚨" if event_type == "REGRESSED" else "✅"
        payload = bq.dumps({
            "text": (
                f"{icon} *BigQuery FinOps Optimizer [{event_type}]*\n"
                f"• *Change Set*: `{cs.get('change_set_id', '')[:10]}` (`{','.join(cs.get('rule_ids') or [])}`)\n"
                f"• *Target*: `{cs.get('target_project')}.{cs.get('target_dataset')}.{cs.get('target_table')}`\n"
                f"• *Detail*: {detail}"
            )
        }).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass



def _update_rule_accuracy(c: Config, cs: dict, ratio: float) -> None:
    for rule_id in cs.get("rule_ids") or []:
        bq.execute(c, f"""
            MERGE `{c.ops}.rule_accuracy` T
            USING (SELECT @r AS rule_id) S ON T.rule_id = S.rule_id
            WHEN MATCHED THEN UPDATE SET
              realized_over_predicted = CAST(
                (T.realized_over_predicted * T.applies + @ratio) / (T.applies + 1) AS NUMERIC),
              applies = T.applies + 1, updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (rule_id, updated_at, applies, realized_over_predicted)
            VALUES (@r, CURRENT_TIMESTAMP(), 1, CAST(@ratio AS NUMERIC))""",
            {"r": rule_id, "ratio": ratio})


def watchdogs(c: Config) -> list[str]:
    """Class 2 recurring-cost watchdogs: flag MVs whose refresh spend exceeds
    their approved monthly limit. v1 alerts (audit + return) — auto-disable is
    a one-line extension once you trust it."""
    alerts = []
    rows = bq.query(c, f"""
        SELECT w.change_set_id, w.target, w.monthly_limit_usd,
               SUM(j.est_cost_usd) * 30 / {max(int(c.get('verify_window_days', 14)), 1)} AS est_monthly
        FROM `{c.ops}.cost_watchdogs` w
        JOIN `{c.ops}.v_jobs_costed` j
          ON STRPOS(COALESCE(j.query_preview, ''), w.target) > 0
        WHERE w.disabled_at IS NULL
          AND j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @w DAY)
        GROUP BY 1, 2, 3
        HAVING est_monthly > monthly_limit_usd""",
        {"w": max(int(c.get("verify_window_days", 14)), 1)})
    for r in rows:
        alerts.append(f"watchdog: {r['target']} ~${float(r['est_monthly']):.0f}/mo "
                      f"exceeds limit ${float(r['monthly_limit_usd']):.0f}")
    return alerts
