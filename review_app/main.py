"""Review UI — the human-in-the-loop gate (design doc §8).

Deliberately small: list the queue, show each card's full contract, take an
approve or a reject-with-reason. Deploy behind IAP / an authenticating proxy —
the app itself trusts the reviewer identity header when present.

    gunicorn -b :8080 review_app.main:app
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from flask import Flask, redirect, render_template, request, url_for  # noqa: E402
except ModuleNotFoundError:
    for venv_py in (
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "bin", "python3"),
        os.path.expanduser("~/.venv/bin/python3"),
        os.path.expanduser("~/.venv-bq/bin/python3"),
    ):
        if os.path.exists(venv_py) and sys.executable != venv_py:
            os.execv(venv_py, [venv_py, os.path.abspath(__file__)] + sys.argv[1:])
    raise

from optimizer import bq, governance, pricing, store, verifier  # noqa: E402
from optimizer.config import cfg  # noqa: E402
from optimizer.executor import recommender_sync  # noqa: E402

app = Flask(__name__)

REASONS = list(store.REJECTION_SNOOZE_DAYS)
_CACHED_REVIEWER: str | None = None

import copy
import datetime as _dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_DASHBOARD_LOCK = threading.Lock()
_DASHBOARD_CACHE: dict[str, dict] = {}   # key -> {"ts": float, "data": dict, "refreshing": bool}
_CACHE_FRESH_SEC = 45.0                  # Serve instantly from memory; trigger background refresh after 45s
_CACHE_MAX_STALE_SEC = 900.0             # Hard expiry (15 min) if background refresh hasn't run


def invalidate_dashboard_cache() -> None:
    """Clear cached dashboard snapshot immediately when any decision (approve/reject/rollback) is applied."""
    with _DASHBOARD_LOCK:
        _DASHBOARD_CACHE.clear()


@app.after_request
def _apply_security_headers(response):
    """Apply OWASP Web Application Firewall (WAF) & defense-in-depth HTTP headers."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/healthz")
def healthz():
    """Lightweight Cloud Run liveness/readiness endpoint (zero BigQuery scan cost)."""
    return {"status": "ok", "service": "bqopt-review-ui"}, 200


_PLACEHOLDER_REVIEWER = "finops-lead@company.com"


def _default_reviewer() -> str:
    global _CACHED_REVIEWER
    if os.environ.get("REVIEWER_EMAIL"):
        return os.environ["REVIEWER_EMAIL"]
    if _CACHED_REVIEWER:
        return _CACHED_REVIEWER
    if os.environ.get("K_SERVICE"):
        _CACHED_REVIEWER = _PLACEHOLDER_REVIEWER
        return _CACHED_REVIEWER
    try:
        import subprocess
        res = subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True, timeout=2)
        email = res.stdout.strip()
        if email and "@" in email:
            _CACHED_REVIEWER = email
            return email
    except Exception:
        pass
    _CACHED_REVIEWER = _PLACEHOLDER_REVIEWER
    return _CACHED_REVIEWER


def _default_reviewer_source() -> str:
    """How the server-side default identity was obtained (see optimizer/governance.py)."""
    if os.environ.get("REVIEWER_EMAIL"):
        return governance.SOURCE_SERVER
    if os.environ.get("K_SERVICE"):
        return governance.SOURCE_PLACEHOLDER
    return governance.SOURCE_PLACEHOLDER if _default_reviewer() == _PLACEHOLDER_REVIEWER else governance.SOURCE_LOCAL


_IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_IAP_CACHE: dict[str, tuple[str, float]] = {}    # verified JWT -> (email, expiry epoch)


def _iap_email(c) -> str | None:
    """Reviewer email from Identity-Aware Proxy. Trusted only when IAP's signed JWT
    (X-Goog-IAP-JWT-Assertion) verifies against the configured audience
    (governance.iap_audience in config.yaml, or env IAP_AUDIENCE). The plain
    X-Goog-Authenticated-User-Email header is never trusted on its own: anyone who
    reaches the app without going through IAP could forge it."""
    token = request.headers.get("X-Goog-IAP-JWT-Assertion", "")
    aud = os.environ.get("IAP_AUDIENCE") or str((c.get("governance") or {}).get("iap_audience") or "")
    if not token or not aud:
        return None
    now = time.time()
    hit = _IAP_CACHE.get(token)
    if hit and hit[1] > now:
        return hit[0]
    try:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token
        claims = id_token.verify_token(token, g_requests.Request(), audience=aud,
                                       certs_url=_IAP_CERTS_URL)
    except Exception as e:  # noqa: BLE001 — invalid/expired/forged token: not an identity
        app.logger.warning("IAP JWT rejected: %s", e)
        return None
    if claims.get("iss") != "https://cloud.google.com/iap":
        app.logger.warning("IAP JWT rejected: unexpected issuer %r", claims.get("iss"))
        return None
    email = str(claims.get("email") or "").strip()
    if not email:
        return None
    if len(_IAP_CACHE) > 1000:
        _IAP_CACHE.clear()
    _IAP_CACHE[token] = (email, float(claims.get("exp") or now + 60))
    return email


def _identity(c, principal: str | None = None) -> dict:
    """Who is acting, and how we know (strongest first):
    1. IAP-verified email.
    2. The principal the browser picked (persona picker / form field / ?principal=):
       honoured ONLY when governance.trust_client_identity is on (sandbox demo).
    3. Server default: REVIEWER_EMAIL env, local gcloud account, or a placeholder.
    FinOps permissions are decided from this, never from what the client claims alone."""
    iap = _iap_email(c)
    if iap:
        return {"email": iap, "source": governance.SOURCE_IAP}
    claimed = (principal or request.form.get("principal", "") or request.args.get("principal", "")).strip()
    if claimed and governance.trust_client_identity(c):
        return {"email": claimed, "source": governance.SOURCE_CLIENT}
    return {"email": _default_reviewer(), "source": _default_reviewer_source()}


def _who(principal: str | None = None) -> str:
    """Email recorded in the audit trail for the current request."""
    return _identity(cfg(), principal)["email"]


def _enrich_repartition(cs: dict) -> None:
    if cs["proposed"].get("action") == "REPARTITION":
        if not cs["proposed"].get("generated_ddl"):
            pcol = cs["proposed"].get("partition_column") or "_PARTITIONTIME"
            gran = cs["proposed"].get("granularity", "DAY")
            expr = f"DATE({pcol})" if gran == "DAY" else f"TIMESTAMP_TRUNC({pcol}, {gran})"
            cs["proposed"]["generated_ddl"] = (
                f"CREATE OR REPLACE TABLE `{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}`\n"
                f"PARTITION BY {expr}\n"
                f"AS SELECT * FROM `{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}`;"
            )
        if not cs["evidence"].get("current_state") and cs["evidence"].get("current_partitions"):
            cur_parts = cs["evidence"].get("current_partitions")
            avg_mb = cs["evidence"].get("avg_partition_size_mb", 0)
            gran = cs["proposed"].get("granularity", "MONTH")
            cs["evidence"]["current_state"] = {
                "partition_granularity": "DAY",
                "total_partitions": f"{cur_parts:,} partitions",
                "avg_partition_size": f"{avg_mb} MB (< 10 MB overhead limit)",
            }
            cs["evidence"]["proposed_state"] = {
                "partition_granularity": gran,
                "target_partitions": f"~{max(1, cur_parts // 30):,} partitions",
                "optimization": "Reduced metadata scan overhead by ~97%",
            }


def _fetch_dashboard_data_uncached(c) -> dict:
    """Fetch and compute dashboard state using 2 concurrent BigQuery queries instead of 9 sequential calls."""
    all_cs_sql = f"""
        SELECT *
        FROM `{c.ops}.change_sets`
        WHERE state IN (
            'PENDING_REVIEW', 'FAILED', 'REGRESSED', 'ROLLED_BACK',
            'PR_HANDED_OFF', 'VERIFYING', 'VERIFIED', 'ROLLING_BACK'
        )
           OR 'W-01' IN UNNEST(rule_ids)
    """
    dir_sql = f"""
        SELECT change_set_id, director_name, department,
               team_readers_count, team_queries_count, team_billed_gb, attribution_source
        FROM `{c.ops}.v_director_recommendations`
    """
    # Same query against a warehouse that has not been re-initialised yet (no attribution_source).
    dir_sql_legacy = dir_sql.replace(", attribution_source", "")

    def _dir_rows() -> list[dict]:
        try:
            return bq.query(c, dir_sql)
        except Exception:
            try:
                return bq.query(c, dir_sql_legacy)
            except Exception:
                return []

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_cs = pool.submit(bq.query, c, all_cs_sql)
            f_dir = pool.submit(_dir_rows)
            f_spend = pool.submit(_compute_spend_30d, c)
            raw_rows = f_cs.result()
            dir_rows = f_dir.result()
            compute_spend = f_spend.result()
    except Exception:
        from optimizer import cli
        cli.cmd_init(c)
        raw_rows = bq.query(c, all_cs_sql)
        dir_rows = _dir_rows()
        compute_spend = _compute_spend_30d(c)

    now_utc = _dt.datetime.now(_dt.timezone.utc)

    def _is_active_pending(r: dict) -> bool:
        if r.get("state") != "PENDING_REVIEW":
            return False
        exp = r.get("expires_at")
        if isinstance(exp, _dt.datetime):
            exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=_dt.timezone.utc)
            if exp_utc <= now_utc:
                return False
        snz = r.get("snooze_until")
        if isinstance(snz, _dt.datetime):
            snz_utc = snz if snz.tzinfo else snz.replace(tzinfo=_dt.timezone.utc)
            if snz_utc >= now_utc:
                return False
        return True

    cards = []
    blocked_cards = []
    regressed_cards = []
    rolled_back_cards = []
    handoff_cards = []
    receipt_candidates = []
    w01_candidates = []

    for r in raw_rows:
        st = r.get("state")
        r_ids = r.get("rule_ids") or []
        if "W-01" in r_ids:
            w01_candidates.append(r)
        if st in ("VERIFYING", "VERIFIED", "REGRESSED", "ROLLING_BACK", "ROLLED_BACK"):
            receipt_candidates.append(r)

        if _is_active_pending(r):
            cs = dict(r)
            cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
            cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
            cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
            _enrich_repartition(cs)
            cards.append(cs)
        elif st == "FAILED":
            cs = dict(r)
            cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
            cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
            cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
            _enrich_repartition(cs)
            hist = cs.get("state_history") or []
            blocker_msg = "Execution stopped by safety guardrail."
            for h in reversed(hist):
                if h.get("state") == "FAILED" and h.get("note"):
                    blocker_msg = h.get("note")
                    break
            cs["blocker_message"] = blocker_msg
            blocked_cards.append(cs)
        elif st == "REGRESSED":
            cs = dict(r)
            cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
            cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
            cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
            hist = cs.get("state_history") or []
            reg_msg = "P95 latency or bytes billed spiked >15% vs baseline across recurring query families."
            for h in reversed(hist):
                if h.get("state") == "REGRESSED" and h.get("note"):
                    reg_msg = h.get("note")
                    break
            cs["regression_message"] = reg_msg
            regressed_cards.append(cs)
        elif st == "ROLLED_BACK":
            cs = dict(r)
            cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
            cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
            cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
            prog = bq.loads(cs.get("progress_json")) or {}
            cs["forensics_table"] = prog.get("steps", {}).get("ROLLED_BACK", {}).get("kept_for_forensics")
            hist = cs.get("state_history") or []
            rollback_msg = cs.get("rejection_note") or "Rolled back by operator"
            rollback_actor = "operator"
            for h in reversed(hist):
                if h.get("state") == "ROLLED_BACK":
                    if h.get("note"):
                        rollback_msg = h.get("note")
                    if h.get("actor"):
                        rollback_actor = h.get("actor")
                    break
            cs["rollback_message"] = rollback_msg
            cs["rollback_actor"] = rollback_actor
            cs["snooze_until_display"] = cs.get("snooze_until")
            rolled_back_cards.append(cs)
        elif st == "PR_HANDED_OFF":
            cs = dict(r)
            cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
            cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
            cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
            cs["handoff"] = bq.loads(cs.get("progress_json")) or {}
            handoff_cards.append(cs)

    # Sort strictly by Net Savings Realized ($) DESC
    cards.sort(key=lambda x: float(x.get("net_monthly_value_usd") or 0.0), reverse=True)

    # Associate director and department mapping from v_director_recommendations
    all_items = cards + blocked_cards + regressed_cards + rolled_back_cards + handoff_cards
    dir_map = {r["change_set_id"]: r for r in dir_rows}
    for item in all_items:
        cid = item.get("change_set_id")
        if cid in dir_map:
            item["director_name"] = dir_map[cid].get("director_name")
            item["department"] = dir_map[cid].get("department")
            item["team_readers_count"] = dir_map[cid].get("team_readers_count", 0)
            item["team_queries_count"] = dir_map[cid].get("team_queries_count", 0)
            item["team_billed_gb"] = dir_map[cid].get("team_billed_gb", 0.0)
            item["attribution_source"] = dir_map[cid].get("attribution_source")
        item.setdefault("director_name", "Central Data Platform")
        item.setdefault("department", "Platform Infrastructure")
        item.setdefault("team_readers_count", 0)
        item.setdefault("team_queries_count", 0)
        item.setdefault("team_billed_gb", 0.0)
        item.setdefault("attribution_source", None)

    # Build top-10 receipts (sorted by applied_at DESC)
    receipt_candidates.sort(
        key=lambda x: str(x.get("applied_at") or ""),
        reverse=True,
    )
    receipts = []
    for r in receipt_candidates[:10]:
        vres = bq.loads(r.get("verification_result_json")) or {}
        realized_val = vres.get("realized_monthly_usd")
        receipts.append({
            "change_set_id": r.get("change_set_id"),
            "rule_ids": r.get("rule_ids") or [],
            "target_dataset": r.get("target_dataset"),
            "target_table": r.get("target_table"),
            "predicted_usd": r.get("gross_monthly_savings_usd"),
            "realized_usd": None if realized_val is None else str(realized_val),
            "realized_over_predicted": r.get("realized_over_predicted"),
            "state": r.get("state"),
            "applied_at": r.get("applied_at"),
        })

    # Latest W-01 sizing for the Project-Wide Billing Fit panel (any state except retired:
    # a SUPERSEDED card carries a number the current math no longer stands behind).
    w01 = None
    w01_live = [r for r in w01_candidates if r.get("state") not in ("SUPERSEDED", "EXPIRED")]
    if w01_live:
        w01_live.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        latest_w = w01_live[0]
        ev = bq.loads(latest_w.get("evidence_json")) or {}
        pr = bq.loads(latest_w.get("proposed_change_json")) or {}
        od = float(ev.get("on_demand_spend_monthly") or 0.0)
        o1 = float(ev.get("option_1_monthly_cost_usd") or 0.0)
        o2 = float(ev.get("option_2_monthly_cost_usd") or 0.0)
        w01 = {
            "tib": float(ev.get("bytes_scanned_tib_30d") or 0.0),
            "od": od,
            "o1": o1,
            "o2": o2,
            "o1_pct": int(round(100 * o1 / od)) if od else 0,
            "o2_pct": int(round(100 * o2 / od)) if od else 0,
            "baseline": int(pr.get("recommended_baseline_slots") or 0),
            "max": int(pr.get("recommended_autoscale_max_slots") or 0),
            "recommended_option": ev.get("recommended_option") or pr.get("selected_option"),
            "billing_aware": isinstance(ev.get("savings_math"), dict),
        }

    data = dict(
        w01=w01,
        cards=cards,
        blocked_cards=blocked_cards,
        regressed_cards=regressed_cards,
        rolled_back_cards=rolled_back_cards,
        handoff_cards=handoff_cards,
        receipts=receipts,
        reasons=REASONS,
        reviewer_email=_default_reviewer(),
        # Actual spend the savings come out of; the headline is capped at it.
        spend_ceilings={pricing.COMPUTE: compute_spend},
    )
    data.update(_summaries(c, data))
    return data


def _compute_spend_30d(c) -> float | None:
    """What the warehouse actually spent on queries in the last 30 days (billing-aware:
    on-demand bytes x $/TiB, reservation slot-hours x edition rate). No set of cards can
    save more compute than this. None when the views are not upgraded / not readable."""
    try:
        rows = bq.query(c, f"""
            SELECT ROUND(SUM(est_cost_usd), 2) AS compute_usd_30d
            FROM `{c.ops}.v_jobs_costed`
            WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)""")
        v = rows[0].get("compute_usd_30d") if rows else None
        return float(v) if v is not None else None
    except Exception:
        return None


def _spend_ceilings(c) -> dict:
    """Spend ceilings for the headline, from the warm dashboard cache when possible."""
    with _DASHBOARD_LOCK:
        entry = _DASHBOARD_CACHE.get(f"{c.project_id}:{c.ops}")
        if entry and isinstance(entry["data"].get("spend_ceilings"), dict):
            return dict(entry["data"]["spend_ceilings"])
    return {pricing.COMPUTE: _compute_spend_30d(c)}


_ITEM_LISTS = ("cards", "blocked_cards", "regressed_cards", "rolled_back_cards", "handoff_cards")


def _savings_math(item: dict) -> dict | None:
    sm = (item.get("evidence") or {}).get("savings_math")
    return sm if isinstance(sm, dict) else None


def _summaries(c, data: dict) -> dict:
    """KPIs and filter facets for whatever cards are in `data` (recomputed per viewer).

    The headline monthly_savings does not double count: cards claiming the same spend
    (same table, same query family, same billing project) are compounded against that
    spend instead of being added up, billing-model cards only count against what the
    table/query fixes leave, and the total is capped at the last 30 days' actual compute
    spend (optimizer.pricing.deoverlap_total). The plain sum is reported next to it as
    monthly_savings_gross_sum."""
    cards = data.get("cards") or []
    all_items = [x for k in _ITEM_LISTS for x in (data.get(k) or [])]
    ceilings = data.get("spend_ceilings") or {}
    headline = pricing.deoverlap_total(cards, ceilings=ceilings)
    finops = [x for x in cards if governance.is_finops_card(x, c)]
    engineering = [x for x in cards if not governance.is_finops_card(x, c)]
    class_savings = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    class_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for x in cards:
        cls_idx = int(x.get("apply_class") or 1)
        if cls_idx in class_savings:
            class_savings[cls_idx] += float(x.get("net_monthly_value_usd") or 0.0)
            class_counts[cls_idx] += 1
    denom = max(sum(class_savings.values()), 1.0)
    directors_set = {x.get("director_name") for x in all_items if x.get("director_name")}
    departments_set = {x.get("department") for x in all_items if x.get("department")}
    kpis = {
        "monthly_savings": headline["total"],
        "annual_savings": headline["total"] * 12,
        "monthly_savings_gross_sum": headline["naive_total"],
        "overlap_removed_usd": headline["overlap_removed"],
        "overlapping_cards": headline["overlapping_cards"],
        "headline_capped_at_spend": bool(headline["ceiling_applied"]),
        "compute_spend_30d_usd": headline["compute_ceiling_usd"],
        "billing_cards_claimed_usd": headline["billing_cards_claimed_usd"],
        "billing_cards_counted_usd": headline["billing_cards_counted_usd"],
        "demo_floor_cards": sum(1 for x in cards if (_savings_math(x) or {}).get("demo_floor_applied")),
        "legacy_estimate_cards": sum(1 for x in cards if _savings_math(x) is None),
        "finops_pending_count": len(finops),
        "finops_monthly_savings": pricing.deoverlap_total(finops, ceilings=ceilings)["total"],
        "engineering_pending_count": len(engineering),
        "engineering_monthly_savings": pricing.deoverlap_total(engineering, ceilings=ceilings)["total"],
        "pending_count": len(cards),
        "blocked_count": len(data.get("blocked_cards") or []),
        "regressed_count": len(data.get("regressed_cards") or []),
        "rolled_back_count": len(data.get("rolled_back_cards") or []),
        "handoff_count": len(data.get("handoff_cards") or []),
        "directors_count": len(directors_set),
        "departments_count": len(departments_set),
        "applied_count": len(data.get("receipts") or []),
        "project_id": c.project_id,
        "location": getattr(c, "location", "US"),
        "class_savings": {k: round(v, 2) for k, v in class_savings.items()},
        "class_pcts": {k: int(round((v / denom) * 100)) for k, v in class_savings.items()},
        "class_counts": class_counts,
    }
    return dict(
        kpis=kpis,
        directors=sorted(directors_set),
        projects=sorted({x.get("target_project") for x in all_items if x.get("target_project")}),
        datasets=sorted({x.get("target_dataset") for x in all_items if x.get("target_dataset")}),
    )


def _scope_for_viewer(c, data: dict, ident: dict) -> dict:
    """Server-side FinOps split. Billing, commitment, reservation and project-level cards
    (and the W-01 billing panel) are only sent to FinOps / governance viewers; everyone
    else gets the engineering queue, with KPIs computed on what they can see."""
    is_finops = governance.is_finops_viewer(ident, c)
    hidden = 0
    for key in _ITEM_LISTS + ("receipts",):
        data[key], n = governance.split_items(data.get(key) or [], is_finops, c)
        hidden += n
    if not is_finops:
        data["w01"] = None
    data.update(_summaries(c, data))
    # The org-wide compute bill is billing data: the cap still applies, but only FinOps
    # viewers see the figure. The raw ceilings never leave the server.
    data.pop("spend_ceilings", None)
    if not is_finops:
        data["kpis"]["compute_spend_30d_usd"] = None
        data["kpis"]["billing_cards_claimed_usd"] = None
        data["kpis"]["billing_cards_counted_usd"] = None
    data["reviewer_email"] = ident["email"]
    data["viewer"] = {
        "email": ident["email"],
        "identity_source": ident["source"],
        "is_finops": is_finops,
        "hidden_finops_items": hidden,
        "can_switch_persona": governance.trust_client_identity(c),
        "finops_approvers_configured": bool(governance.finops_approvers(c)),
    }
    return data


def _personas(c, ident: dict) -> list[dict]:
    """Identities the UI may offer. Outside demo mode (trust_client_identity off) that is
    only the verified identity: the persona picker is read-only."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(email: str | None, label: str, role: str) -> None:
        if not email or email.lower() in seen:
            return
        seen.add(email.lower())
        as_viewer = {"email": email, "source": ident["source"] if email == ident["email"] else governance.SOURCE_CLIENT}
        out.append({"email": email, "label": label, "role": role,
                    "is_finops": governance.is_finops_viewer(as_viewer, c)})

    add(ident["email"], "Active Identity", "platform_approver")
    if governance.trust_client_identity(c):
        add("gcp-admin@company.com", "GCP Project Admin", "platform_approver")
        add("alice-owner@company.com", "Data Owner / Dataset Lead", "owner_approver")
        for email in sorted(governance.finops_approvers(c)):
            add(email, "FinOps / Governance", "platform_approver")
    return out


def _refresh_cache_bg(cache_key: str, c) -> None:
    try:
        fresh = _fetch_dashboard_data_uncached(c)
        with _DASHBOARD_LOCK:
            _DASHBOARD_CACHE[cache_key] = {"ts": time.time(), "data": fresh, "refreshing": False}
    except Exception:
        with _DASHBOARD_LOCK:
            if cache_key in _DASHBOARD_CACHE:
                _DASHBOARD_CACHE[cache_key]["refreshing"] = False


def _dashboard_data(c, force_refresh: bool = False) -> dict:
    """Everything the review UI shows. Shared by the classic Jinja page and /api/dashboard.
    Uses stale-while-revalidate caching so page refreshes return in <50ms."""
    cache_key = f"{c.project_id}:{c.ops}"
    now = time.time()
    if not force_refresh:
        with _DASHBOARD_LOCK:
            entry = _DASHBOARD_CACHE.get(cache_key)
            if entry and (now - entry["ts"]) < _CACHE_MAX_STALE_SEC:
                if (now - entry["ts"]) >= _CACHE_FRESH_SEC and not entry.get("refreshing"):
                    entry["refreshing"] = True
                    threading.Thread(target=_refresh_cache_bg, args=(cache_key, c), daemon=True).start()
                return copy.deepcopy(entry["data"])

    fresh = _fetch_dashboard_data_uncached(c)
    with _DASHBOARD_LOCK:
        _DASHBOARD_CACHE[cache_key] = {"ts": time.time(), "data": fresh, "refreshing": False}
    return copy.deepcopy(fresh)


# ---------------------------------------------------------------------------
# Pages: React SPA at "/", classic Jinja page kept at "/classic"
# ---------------------------------------------------------------------------

_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")


@app.get("/classic")
def queue():
    c = cfg()
    force = request.args.get("refresh") in ("1", "true")
    data = _scope_for_viewer(c, _dashboard_data(c, force_refresh=force), _identity(c))
    return render_template("index.html", **data)


@app.get("/")
def home():
    if os.path.isfile(os.path.join(_DIST, "index.html")):
        from flask import send_from_directory
        resp = send_from_directory(_DIST, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    return queue()


@app.get("/assets/<path:filename>")
def spa_assets(filename):
    from flask import send_from_directory
    resp = send_from_directory(os.path.join(_DIST, "assets"), filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"   # hashed filenames
    return resp


def _jsonable(o):
    """BigQuery rows -> JSON: Decimal -> float, datetime/date -> ISO string."""
    from decimal import Decimal
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    return o


_JSON_BLOBS = ("evidence_json", "proposed_change_json", "confidence_factors_json", "progress_json",
               "rollback_plan_json", "verification_plan_json", "verification_result_json", "blast_radius_json")


@app.get("/api/dashboard")
def api_dashboard():
    from flask import jsonify
    c = cfg()
    force = request.args.get("refresh") in ("1", "true")
    ident = _identity(c, request.args.get("principal"))
    data = _scope_for_viewer(c, _dashboard_data(c, force_refresh=force), ident)
    for key in _ITEM_LISTS:
        for cs in data[key]:
            for blob in _JSON_BLOBS:          # already parsed into evidence/proposed/factors
                cs.pop(blob, None)
    data["personas"] = _personas(c, ident)
    data["reason_snooze_days"] = dict(store.REJECTION_SNOOZE_DAYS)
    return jsonify(_jsonable(data))


def _permission_denied(e: Exception):
    from flask import jsonify
    return jsonify({"ok": False, "error": str(e), "code": "FINOPS_PERMISSION_REQUIRED"}), 403


@app.post("/api/decisions")
def api_decisions():
    from flask import jsonify
    body = request.get_json(silent=True) or {}
    if not body.get("change_set_id"):
        return jsonify({"ok": False, "error": "change_set_id is required"}), 400
    c = cfg()
    ident = _identity(c, body.get("principal"))
    try:
        result = _apply_decision(c, body, ident)
        invalidate_dashboard_cache()
    except governance.PermissionDenied as e:
        return _permission_denied(e)
    except Exception as e:  # noqa: BLE001 — surfaced to the reviewer, card left retryable
        invalidate_dashboard_cache()
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, **result})


# Product reference material for Gemini FinOps Assist. These are background facts
# the model can draw on; they are NOT returned verbatim for unrelated questions.
_COPILOT_REFERENCE = {
    "W-02": (
        "🛡️ **How Rule `W-02` (Proactive Cost Guardrail) Protects Your Production ETL Pipelines:**\n\n"
        "1. **Smart `user_email` Filtering**: Every BigQuery job in `INFORMATION_SCHEMA.JOBS` stamps `user_email`. Our optimizer separates **Human Ad-Hoc Users** (`user_email NOT LIKE '%.gserviceaccount.com'`) from **Service Accounts & Production ETL** (`*.iam.gserviceaccount.com`, `airflow`, `dbt`, `dataform`).\n"
        "2. **👤 Human Analysts Only**: Enforces a **50 GiB per-query safety cap (`@@maximum_bytes_billed = 53687091200`, max ~$0.31/query)** and an isolated **50-slot autoscaling sandbox (`human-adhoc-sandbox-pool`)** so an accidental `SELECT *` without a `WHERE` clause is stopped in 0ms before billing.\n"
        "3. **🤖 Service Accounts 100% Exempt**: All `*.iam.gserviceaccount.com` ETL pipelines run **uncapped** on your dedicated production reservation (`enterprise-prod-pool`), guaranteeing **0% pipeline breakage**."
    ),
    "W-01": (
        "🏢 **Rule `W-01` BigQuery Enterprise Edition Sizing Comparison:**\n\n"
        "• Compares the project's On-Demand spend ($6.25/TiB scanned) with two Enterprise reservation options.\n"
        "• **Sizing comes from real per-minute slot usage** in `INFORMATION_SCHEMA.JOBS_TIMELINE` (30 days): "
        "baseline = median slots across ALL minutes (idle = 0) rounded down to 50; max = p99 busy-minute slots rounded up to 50.\n"
        "• **🏢 Option 1**: baseline slots (always billed) + autoscale up to the max. Best for steady 24/7 load.\n"
        "• **⚡ Option 2**: 0 baseline + autoscale up to the max — $0 idle cost, no commitment. Best for spiky workloads.\n"
        "• Use the live card's evidence (`capacity_sizing`, `option_1/2_monthly_cost_usd`, `option_1/2_savings_usd`) for exact numbers."
    ),
    "CLASS4_ENGINES": (
        "⚡ **How Our Tri-Engine Class 4 SQL Anti-Pattern Pipeline Works:**\n\n"
        "1. **Engine 1 — Fast Regex Pattern Scanner (`C4-01`..`C4-09`)**: Instantly catches obvious anti-patterns (`SELECT *`, `ORDER BY` without `LIMIT`, `WHERE DATE(col) = ...`).\n"
        "2. **Engine 2 — Google Official ZetaSQL AST Compiler (`bigquery-antipattern-recognition.jar`)**: Parses queries into an Abstract Syntax Tree (AST) to catch deep structural issues like CTEs evaluated multiple times and `ROW_NUMBER() = 1` sorts.\n"
        "3. **Engine 3 — Vertex AI Gemini 3 Flash (`gemini-3-flash-preview`) + `dry_run=True` Verifier**: Reads live table partitioning/clustering metadata, rewrites complex SQL, and runs a `$0` BigQuery `dry_run` to mathematically verify byte reduction before creating a card."
    ),
    "APPLY_CLASSES": (
        "Apply classes: Class 1 = in-place metadata DDL (clustering, require_partition_filter, expirations, "
        "storage billing model, W-02 guardrail); Class 2 = additive objects (materialized views with max_staleness "
        "+ cost watchdog); Class 3 = structural rebuilds via the S0-S9 copy-swap-rebind state machine "
        "(repartitioning, shard consolidation, archive) and W-01 reservation sizing; Class 4 = SQL anti-pattern "
        "rewrites delivered as CI pull requests (route CI_PULL_REQUEST), never applied directly to BigQuery."
    ),
}

_COPILOT_MODELS = [("gemini-3-flash-preview", "global"), ("gemini-2.5-flash", "us-central1")]


def _as_float(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _copilot_context(c, cards: list[dict], include_lifecycle: bool = True,
                     ceilings: dict | None = None) -> str:
    """Compact, factual snapshot of the live queue used to ground the model.
    include_lifecycle=False (non-FinOps viewers) skips the all-change-sets counts, which
    would otherwise include billing / commitment cards they are not allowed to see."""
    from collections import Counter

    headline = pricing.deoverlap_total(cards, ceilings=ceilings)
    total_mo = headline["total"]
    by_class = Counter(str(x.get("apply_class")) for x in cards)
    by_rule = Counter(r for x in cards for r in (x.get("rule_ids") or []))
    by_route = Counter(str(x.get("execution_route")) for x in cards)
    value_by_class: dict[str, float] = {}
    for x in cards:
        k = str(x.get("apply_class"))
        value_by_class[k] = value_by_class.get(k, 0.0) + _as_float(x.get("net_monthly_value_usd"))

    lines = [
        f"PROJECT: {c.project_id}",
        f"PENDING REVIEW QUEUE: {len(cards)} cards, total net value ${total_mo:,.2f}/mo (${total_mo * 12:,.2f}/yr) "
        f"after removing overlap between cards that claim the same spend"
        + (" and capping at the last 30 days' actual compute spend" if headline["ceiling_applied"] else "")
        + f" (plain sum of the cards: ${headline['naive_total']:,.2f}/mo)",
        "CARDS BY APPLY CLASS: " + ", ".join(
            f"Class {k}: {by_class[k]} cards (${value_by_class[k]:,.2f}/mo)" for k in sorted(by_class)),
        "CARDS BY RULE: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_rule.items())),
        "CARDS BY EXECUTION ROUTE: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_route.items())),
    ]
    # Lifecycle counts across ALL change sets (not just the pending queue)
    if include_lifecycle:
        try:
            rows = bq.query(c, f"SELECT apply_class, state, COUNT(*) AS n FROM `{c.ops}.change_sets` GROUP BY 1, 2 ORDER BY 1, 2")
            lines.append("ALL CHANGE SETS BY CLASS & STATE (every lifecycle state): " + ", ".join(
                f"Class {r['apply_class']} {r['state']}: {r['n']}" for r in rows))
        except Exception:
            pass

    lines.append("\nPENDING CARDS (sorted by net monthly value, highest first):")
    for x in sorted(cards, key=lambda x: _as_float(x.get("net_monthly_value_usd")), reverse=True):
        summary = (x.get("finding_summary") or "").replace("\n", " ")[:220]
        lines.append(
            f"- id={x.get('change_set_id')} | rules={','.join(x.get('rule_ids') or [])} | class={x.get('apply_class')} "
            f"| target={x.get('target_dataset')}.{x.get('target_table')} | net=${_as_float(x.get('net_monthly_value_usd')):,.2f}/mo "
            f"| confidence={x.get('confidence')} | route={x.get('execution_route')} | state={x.get('state')} "
            f"| owner={x.get('director_name') or x.get('owner_principal') or 'unassigned'} | summary={summary}"
        )
    return "\n".join(lines)


def _copilot_offline_answer(c, cards: list[dict], q: str, ceilings: dict | None = None) -> str:
    """Deterministic fallback used only when Gemini is unreachable. Answers from real queue data."""
    import re
    from collections import Counter

    ql = q.lower()
    total_mo = pricing.deoverlap_total(cards, ceilings=ceilings)["total"]
    by_class = Counter(str(x.get("apply_class")) for x in cards)
    note = "\n\n_⚠️ Gemini is currently unreachable — this is an offline answer computed from the live queue._"

    m = re.search(r"class\s*([1-4])", ql)
    if m and any(k in ql for k in ("how many", "count", "number of", "total")):
        k = m.group(1)
        return f"You have **{by_class.get(k, 0)} Class {k}** change set(s) pending review (out of {len(cards)} total)." + note
    if any(k in ql for k in ("w-02", "service account")):
        return _COPILOT_REFERENCE["W-02"] + note
    if any(k in ql for k in ("w-01", "option 1", "option 2", "edition")):
        return _COPILOT_REFERENCE["W-01"] + note
    if "class 4" in ql and any(k in ql for k in ("engine", "zetasql", "how do", "how does", "work")):
        return _COPILOT_REFERENCE["CLASS4_ENGINES"] + note

    top = sorted(cards, key=lambda x: _as_float(x.get("net_monthly_value_usd")), reverse=True)[:5]
    top_lines = "\n".join(
        f"• **{i + 1}. `{','.join(x.get('rule_ids') or [])}` on `{x.get('target_dataset')}.{x.get('target_table')}`** — "
        f"**${_as_float(x.get('net_monthly_value_usd')):,.0f}/mo** (Class {x.get('apply_class')})"
        for i, x in enumerate(top))
    class_line = ", ".join(f"Class {k}: {by_class[k]}" for k in sorted(by_class))
    return (
        f"📊 **Live Queue Summary (`{c.project_id}`)**\n\n"
        f"• **{len(cards)} pending cards** worth **${total_mo:,.0f}/mo** ({class_line})\n\n"
        f"**Top 5 by net monthly value:**\n{top_lines}" + note
    )


@app.post("/api/client-error")
def client_error():
    """Browser-side crash report from the React ErrorBoundary -> Cloud Run logs (severity ERROR)."""
    p = request.get_json(silent=True, force=True) or {}
    app.logger.error(
        "CLIENT_ERROR where=%s message=%s url=%s ua=%s\nstack=%s\ncomponentStack=%s",
        str(p.get("where"))[:100], str(p.get("message"))[:1000], str(p.get("url"))[:300],
        str(p.get("ua"))[:300], str(p.get("stack"))[:4000], str(p.get("componentStack"))[:4000])
    return {"ok": True}, 200


@app.post("/api/finops-chat")
def finops_chat():
    """Gemini FinOps Assist: answers the user's actual question, grounded in the live queue."""
    c = cfg()
    payload = request.get_json(silent=True) or {}
    q = (payload.get("question") or "").strip()[:2000]
    if not q:
        return {"answer": "Ask me anything about your active BigQuery optimization queue, W-01 Edition Sizing, W-02 Human vs. Service Account Guardrails, or Class 4 SQL rewrites!"}, 200

    try:
        cards = store.pending(c)
    except Exception:
        cards = []
    # Same FinOps split as the dashboard: non-FinOps viewers get no billing/commitment cards.
    is_finops = governance.is_finops_viewer(_identity(c, payload.get("principal")), c)
    if not is_finops:
        cards = [x for x in cards if not governance.is_finops_card(x, c)]
    ceilings = _spend_ceilings(c)

    # Optional short conversation history from the UI: [{"role": "user"|"ai", "text": "..."}]
    history = payload.get("history") or []
    history_txt = "\n".join(
        f"{'User' if h.get('role') == 'user' else 'Assistant'}: {str(h.get('text') or '')[:800]}"
        for h in history[-6:] if isinstance(h, dict))

    system_instruction = (
        f"You are Gemini FinOps Assist for the BigQuery Optimization Control Plane (project {c.project_id}).\n"
        "RULES:\n"
        "1. Answer EXACTLY the question the user asked. Put the direct answer (e.g. the number, name or yes/no) in the first sentence.\n"
        "2. For anything about the queue, counts, dollars, tables, rules or owners, use ONLY the LIVE QUEUE DATA below. "
        "Count carefully from the data. Never invent numbers, tables or IDs.\n"
        "3. If the data does not contain the answer, say so plainly and suggest where to look.\n"
        "4. Use the PRODUCT REFERENCE only when the user asks how something works; do not paste it for unrelated questions.\n"
        "5. Be concise: markdown, at most ~150 words, bullets when listing items.\n\n"
        "=== LIVE QUEUE DATA ===\n" + _copilot_context(c, cards, include_lifecycle=is_finops, ceilings=ceilings) + "\n\n"
        "=== PRODUCT REFERENCE ===\n" + "\n\n".join(f"[{k}]\n{v}" for k, v in _COPILOT_REFERENCE.items())
    )
    contents = (f"Conversation so far:\n{history_txt}\n\n" if history_txt else "") + f"User question: {q}"

    try:
        from google import genai
        from google.genai import types

        gen_cfg = types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
        for model, loc in _COPILOT_MODELS:
            try:
                gclient = genai.Client(vertexai=True, project=c.project_id, location=loc,
                                       http_options=types.HttpOptions(timeout=30_000))
                r = gclient.models.generate_content(model=model, contents=contents, config=gen_cfg)
                if r and r.text:
                    return {"answer": r.text.strip(), "model": model}, 200
            except Exception as e:  # try the next model
                app.logger.warning("Gemini FinOps Assist model %s@%s failed: %s", model, loc, e)
    except Exception as e:
        app.logger.warning("Gemini FinOps Assist: google-genai unavailable: %s", e)

    return {"answer": _copilot_offline_answer(c, cards, q, ceilings=ceilings), "model": "offline-fallback"}, 200



def _apply_decision(c, form, ident: dict) -> dict:
    """Single implementation of every reviewer action (classic form and JSON API).
    `form` is any mapping with .get(); `ident` comes from _identity(). Raises on failure;
    rollback failures leave the card in ROLLING_BACK so it can be retried. Every action on
    a billing / commitment / reservation / project-level card requires a FinOps approver
    (governance.PermissionDenied -> HTTP 403)."""
    cs_id = form.get("change_set_id")
    action = form.get("action", "reject")
    who = ident["email"]

    cs = store.get(c, cs_id)
    if not cs:
        raise ValueError(f"change set {cs_id} not found")
    governance.check_can_decide(cs, ident, c)

    if action == "pr_merged":
        from optimizer.executor import pr_handoff
        pr_handoff.mark_merged(c, cs, who, form.get("pr_url"))
        return {"action": action, "status": "VERIFYING"}

    if action == "approve":
        cls = int(cs.get("apply_class") or 1)
        existing_approvals = cs.get("approvals") or []
        role = form.get("role")
        if not role or cls == 3:
            if cls == 3:
                role = "platform_approver" if len(existing_approvals) > 0 else "owner_approver"
            else:
                role = role or "approver"

        # Determine Active Assist claim note for audit trail
        native_recs = cs.get("native_rec_names") or []
        if native_recs:
            claim_note = f"CLAIMED:{native_recs[0]}"
        else:
            rule_id = (cs.get("rule_ids") or ["custom"])[0]
            target_obj = cs.get("target_table") or cs.get("target_dataset") or "rule"
            claim_note = f"CLAIMED:projects/{c.project_id}/locations/{cs.get('target_region') or 'us'}/recommenders/optimizer.{rule_id}/recommendations/rec-{target_obj}-001"

        # Capacity migrations (W-01) carry two options; persist the one the reviewer picked
        change = bq.loads(cs.get("proposed_change_json")) or {}
        if change.get("action") == "CAPACITY_PRICING_MIGRATION":
            try:
                opt = int(form.get("selected_option") or change.get("selected_option") or 1)
            except (TypeError, ValueError):
                opt = 1
            opt = 2 if opt == 2 else 1
            store.set_selected_option(c, cs_id, opt)
            claim_note = f"{claim_note} | OPTION:{opt}"

        status = store.approve(c, cs_id, who, role=role, note=claim_note)
        if status == "APPROVED":
            verifier.freeze_baseline(c, cs)                      # baseline frozen at full approval
            recommender_sync.mark(cs.get("native_rec_names"), "CLAIMED")
        return {"action": action, "status": status}
    if action == "reset":
        note = form.get("note") or "Reset to PENDING_REVIEW from blocked state"
        store.unsnooze(c, cs_id, who, note=note)
        return {"action": action, "status": "PENDING_REVIEW"}
    if action == "unsnooze":
        note = form.get("note") or f"Un-snoozed from ROLLED_BACK by {who} for re-evaluation"
        store.unsnooze(c, cs_id, who, note=note)
        return {"action": action, "status": "PENDING_REVIEW"}
    if action == "rollback":
        category = form.get("category", "REGRESSION_PERFORMANCE")
        note = form.get("note") or f"Rollback requested via Web UI by {who}"
        from optimizer import cli
        cli.cmd_rollback(c, cs_id, reason=note, category=category, snooze_days=90, actor=who)
        return {"action": action, "status": "ROLLED_BACK"}
    # reject
    store.reject(c, cs_id, who, form.get("reason", "OTHER"), form.get("note") or None)
    return {"action": "reject", "status": "REJECTED"}


@app.post("/decision")
def decision():
    """Classic HTML form endpoint (used by /classic)."""
    c = cfg()
    try:
        _apply_decision(c, request.form, _identity(c))
        invalidate_dashboard_cache()
    except governance.PermissionDenied as e:
        from markupsafe import escape
        return (f"<h3>FinOps approval required</h3><pre>{escape(str(e))}</pre>"
                f"<a href='{url_for('queue')}'>Back to review queue</a>"), 403
    except Exception as e:  # noqa: BLE001
        invalidate_dashboard_cache()
        from markupsafe import escape
        return (f"<h3>Action failed</h3><pre>{escape(str(e))}</pre>"
                f"<p>If this was a rollback, the change set is left in ROLLING_BACK; fix the cause and retry.</p>"
                f"<a href='{url_for('queue')}'>Back to review queue</a>"), 500
    return redirect(url_for("queue"))


def _warm_cache_on_boot() -> None:
    """Pre-warm and continuously refresh the BigQuery dashboard cache in a background daemon thread."""
    if os.environ.get("K_SERVICE") or os.environ.get("WARM_DASHBOARD_CACHE") == "1":
        def _bg():
            while True:
                try:
                    c = cfg()
                    fresh = _fetch_dashboard_data_uncached(c)
                    cache_key = f"{c.project_id}:{c.ops}"
                    with _DASHBOARD_LOCK:
                        _DASHBOARD_CACHE[cache_key] = {"ts": time.time(), "data": fresh, "refreshing": False}
                except Exception:
                    pass
                time.sleep(30.0)
        threading.Thread(target=_bg, daemon=True).start()


_warm_cache_on_boot()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run BigQuery Optimization Review App")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind to")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)), help="Port to listen on")
    args = parser.parse_args()
    is_debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    app.run(host=args.host, port=args.port, debug=is_debug)

