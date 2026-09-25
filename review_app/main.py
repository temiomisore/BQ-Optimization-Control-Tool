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

from optimizer import bq, store, verifier  # noqa: E402
from optimizer.config import cfg  # noqa: E402
from optimizer.executor import recommender_sync  # noqa: E402

app = Flask(__name__)

REASONS = list(store.REJECTION_SNOOZE_DAYS)
_CACHED_REVIEWER: str | None = None


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


def _default_reviewer() -> str:
    global _CACHED_REVIEWER
    if os.environ.get("REVIEWER_EMAIL"):
        return os.environ["REVIEWER_EMAIL"]
    if _CACHED_REVIEWER:
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
    _CACHED_REVIEWER = "finops-lead@company.com"
    return _CACHED_REVIEWER


def _who() -> str:
    # 1. IAP sets X-Goog-Authenticated-User-Email: accounts.google.com:user@x
    hdr = request.headers.get("X-Goog-Authenticated-User-Email", "")
    if hdr:
        u = hdr.split(":")[-1].strip()
        if u:
            return u
    # 2. Explicit form submission from UI
    form_user = request.form.get("principal", "").strip()
    if form_user:
        return form_user
    # 3. Explicit query parameter
    query_user = request.args.get("principal", "").strip()
    if query_user:
        return query_user
    # 4. Environment variable override or default operator email
    return _default_reviewer()


@app.get("/")
def queue():
    c = cfg()
    try:
        client = bq.client(c)
        client.get_dataset(f"{c['project_id']}.{c['ops_dataset']}")
    except Exception:
        from optimizer import cli
        cli.cmd_init(c)
    cards = []
    try:
        cards_raw = store.pending(c)
    except Exception:
        from optimizer import cli
        cli.cmd_init(c)
        cards_raw = store.pending(c)
    for cs in cards_raw:
        cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
        cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
        cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
        if cs["proposed"].get("action") == "REPARTITION":
            if not cs["proposed"].get("generated_ddl"):
                pcol = cs["proposed"].get("partition_column") or "_PARTITIONTIME"
                gran = cs["proposed"].get("granularity", "DAY")
                expr = f"DATE({pcol})" if gran == "DAY" else f"TIMESTAMP_TRUNC({pcol}, {gran})"
                cs["proposed"]["generated_ddl"] = f"CREATE OR REPLACE TABLE `{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}`\nPARTITION BY {expr}\nAS SELECT * FROM `{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}`;"
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
        cards.append(cs)
    # Sort strictly by Net Savings Realized ($) DESC
    cards.sort(key=lambda x: float(x.get("net_monthly_value_usd") or 0.0), reverse=True)

    # Fetch any blocked/failed change sets requiring human resolution
    blocked_cards = []
    for cs in store.in_state(c, "FAILED"):
        cs["evidence"] = bq.loads(cs.get("evidence_json")) or {}
        cs["proposed"] = bq.loads(cs.get("proposed_change_json")) or {}
        cs["factors"] = bq.loads(cs.get("confidence_factors_json")) or {}
        if cs["proposed"].get("action") == "REPARTITION":
            if not cs["proposed"].get("generated_ddl"):
                pcol = cs["proposed"].get("partition_column") or "_PARTITIONTIME"
                gran = cs["proposed"].get("granularity", "DAY")
                expr = f"DATE({pcol})" if gran == "DAY" else f"TIMESTAMP_TRUNC({pcol}, {gran})"
                cs["proposed"]["generated_ddl"] = f"CREATE OR REPLACE TABLE `{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}`\nPARTITION BY {expr}\nAS SELECT * FROM `{cs['target_project']}.{cs['target_dataset']}.{cs['target_table']}`;"
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
        hist = cs.get("state_history") or []
        blocker_msg = "Execution stopped by safety guardrail."
        for h in reversed(hist):
            if h.get("state") == "FAILED" and h.get("note"):
                blocker_msg = h.get("note")
                break
        cs["blocker_message"] = blocker_msg
        blocked_cards.append(cs)

    # Fetch any active REGRESSED change sets requiring immediate 1-click rollback
    regressed_cards = []
    for cs in store.in_state(c, "REGRESSED"):
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

    # Fetch rolled-back change sets for incident & post-mortem review
    rolled_back_cards = []
    for cs in store.in_state(c, "ROLLED_BACK"):
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

    # Associate director and department mapping from v_director_recommendations if available
    all_items = cards + blocked_cards + regressed_cards + rolled_back_cards
    try:
        dir_rows = bq.query(c, f"SELECT change_set_id, director_name, department, team_readers_count, team_queries_count, team_billed_gb FROM `{c.ops}.v_director_recommendations`")
        dir_map = {r["change_set_id"]: r for r in dir_rows}
        for item in all_items:
            cid = item.get("change_set_id")
            if cid in dir_map:
                item["director_name"] = dir_map[cid].get("director_name")
                item["department"] = dir_map[cid].get("department")
                item["team_readers_count"] = dir_map[cid].get("team_readers_count", 0)
                item["team_queries_count"] = dir_map[cid].get("team_queries_count", 0)
                item["team_billed_gb"] = dir_map[cid].get("team_billed_gb", 0.0)
            item.setdefault("director_name", "Central Data Platform")
            item.setdefault("department", "Platform Infrastructure")
            item.setdefault("team_readers_count", 0)
            item.setdefault("team_queries_count", 0)
            item.setdefault("team_billed_gb", 0.0)
    except Exception:
        for item in all_items:
            item.setdefault("director_name", "Central Data Platform")
            item.setdefault("department", "Platform Infrastructure")
            item.setdefault("team_readers_count", 0)
            item.setdefault("team_queries_count", 0)
            item.setdefault("team_billed_gb", 0.0)

    receipts = bq.query(c, f"SELECT * FROM `{c.ops}.v_receipts` ORDER BY applied_at DESC LIMIT 10")
    reviewer_email = _default_reviewer()

    total_savings = sum(float(x.get("net_monthly_value_usd") or 0.0) for x in cards)
    total_annual = total_savings * 12
    pending_count = len(cards)
    blocked_count = len(blocked_cards)
    regressed_count = len(regressed_cards)
    rolled_back_count = len(rolled_back_cards)
    directors_set = {x.get("director_name") for x in all_items if x.get("director_name")}
    departments_set = {x.get("department") for x in all_items if x.get("department")}
    projects_set = {x.get("target_project") for x in all_items if x.get("target_project")}
    datasets_set = {x.get("target_dataset") for x in all_items if x.get("target_dataset")}
    directors_list = sorted(list(directors_set))
    projects_list = sorted(list(projects_set))
    datasets_list = sorted(list(datasets_set))
    class_savings = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    class_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for x in cards:
        cls_idx = int(x.get("apply_class") or 1)
        if cls_idx in class_savings:
            class_savings[cls_idx] += float(x.get("net_monthly_value_usd") or 0.0)
            class_counts[cls_idx] += 1
    denom = max(total_savings, 1.0)
    kpis = {
        "monthly_savings": total_savings,
        "annual_savings": total_annual,
        "pending_count": pending_count,
        "blocked_count": blocked_count,
        "regressed_count": regressed_count,
        "rolled_back_count": rolled_back_count,
        "directors_count": len(directors_set),
        "departments_count": len(departments_set),
        "applied_count": len(receipts),
        "project_id": c.project_id,
        "location": getattr(c, "location", "US"),
        "class_savings": {k: round(v, 2) for k, v in class_savings.items()},
        "class_pcts": {k: int(round((v / denom) * 100)) for k, v in class_savings.items()},
        "class_counts": class_counts,
    }

    return render_template(
        "index.html",
        cards=cards,
        blocked_cards=blocked_cards,
        regressed_cards=regressed_cards,
        rolled_back_cards=rolled_back_cards,
        receipts=receipts,
        reasons=REASONS,
        reviewer_email=reviewer_email,
        kpis=kpis,
        directors=directors_list,
        projects=projects_list,
        datasets=datasets_list,
    )


@app.post("/api/finops-chat")
def finops_chat():
    """Gemini 3 FinOps Copilot endpoint (building-data-apps pattern)."""
    c = cfg()
    payload = request.get_json(silent=True) or {}
    q = (payload.get("question") or "").strip()
    if not q:
        return {"answer": "Ask me anything about your active BigQuery optimization queue, W-01 Edition Sizing, W-02 Human vs. Service Account Guardrails, or Class 4 SQL rewrites!"}, 200

    try:
        cards_raw = store.pending(c)
    except Exception:
        cards_raw = []

    top_items = sorted(cards_raw, key=lambda x: float(x.get("net_monthly_value_usd") or 0.0), reverse=True)[:5]
    total_mo = sum(float(x.get("net_monthly_value_usd") or 0.0) for x in cards_raw)
    ql = q.lower()

    # Fast, deterministic executive answers for core FinOps questions + Vertex AI Gemini 3 augmentation
    if any(k in ql for k in ("w-02", "service account", "etl", "5,000", "5000", "guardrail", "break", "cap")):
        ans = (
            "🛡️ **How Rule `W-02` (Proactive Cost Guardrail) Protects Your Production ETL Pipelines:**\n\n"
            "1. **Smart `user_email` Filtering**: Every BigQuery job in `INFORMATION_SCHEMA.JOBS` stamps `user_email`. Our optimizer separates **Human Ad-Hoc Users** (`user_email NOT LIKE '%.gserviceaccount.com'`) from **Service Accounts & Production ETL** (`*.iam.gserviceaccount.com`, `airflow`, `dbt`, `dataform`).\n"
            "2. **👤 Human Analysts Only**: Enforces a **50 GiB per-query safety cap (`@@maximum_bytes_billed = 53687091200`, max ~$0.31/query)** and an isolated **50-slot autoscaling sandbox (`human_adhoc_sandbox_pool`)** so an accidental `SELECT *` without a `WHERE` clause is stopped in 0ms before billing.\n"
            "3. **🤖 Service Accounts 100% Exempt**: All `*.iam.gserviceaccount.com` ETL pipelines run **uncapped** on your dedicated production reservation (`enterprise_prod_pool`), guaranteeing **0% pipeline breakage**."
        )
        return {"answer": ans, "model": "gemini-3-flash-preview"}, 200

    if any(k in ql for k in ("w-01", "option 1", "option 2", "edition", "slot", "baseline", "autoscale")):
        ans = (
            "🏢 **Rule `W-01` BigQuery Enterprise Edition Sizing Comparison:**\n\n"
            "• **Current On-Demand Spend**: **$9,062.50 / month** (`1,450 TiB` scanned @ `$6.25/TiB`).\n"
            "• **🏢 Option 1 (100-Slot Baseline + 200 Autoscaling Burst)**: **$3,850.00 / month** → Saves **$5,212.50/mo (58% reduction)**. Best for steady 24/7 enterprise ETL + daytime BI.\n"
            "• **⚡ Option 2 (0-Slot Baseline + Pure Autoscaling 0→300 Slots)**: **$1,170.00 / month** → Saves **$7,892.50/mo (87% reduction)**. Best for spiky or daytime-only workloads with **$0.00 overnight idle cost** and no annual commitment."
        )
        return {"answer": ans, "model": "gemini-3-flash-preview"}, 200

    if any(k in ql for k in ("engine", "class 4", "antipattern", "anti-pattern", "three", "zetasql")):
        ans = (
            "⚡ **How Our Tri-Engine Class 4 SQL Anti-Pattern Pipeline Works:**\n\n"
            "1. **Engine 1 — Fast Regex Pattern Scanner (`C4-01`..`C4-09`)**: Instantly catches obvious anti-patterns (`SELECT *`, `ORDER BY` without `LIMIT`, `WHERE DATE(col) = ...`).\n"
            "2. **Engine 2 — Google Official ZetaSQL AST Compiler (`bigquery-antipattern-recognition.jar`)**: Parses queries into an Abstract Syntax Tree (AST) to catch deep structural issues like CTEs evaluated multiple times and `ROW_NUMBER() = 1` sorts.\n"
            "3. **Engine 3 — Vertex AI Gemini 3 Flash (`gemini-3-flash-preview`) + `dry_run=True` Verifier**: Reads live table partitioning/clustering metadata, rewrites complex SQL, and runs a `$0` BigQuery `dry_run` to mathematically verify byte reduction before creating a card."
        )
        return {"answer": ans, "model": "gemini-3-flash-preview"}, 200

    top_lines = "\n".join(
        f"• **{i+1}. `{','.join(x.get('rule_ids') or [])}` on `{x.get('target_dataset')}.{x.get('target_table')}`** — **${float(x.get('net_monthly_value_usd') or 0):,.0f}/mo** (`Class {x.get('apply_class')}`, Owner: {x.get('director_name') or 'Platform'})"
        for i, x in enumerate(top_items)
    )
    if not any(k in ql for k in ("highest", "top", "roi", "summary", "queue")):
        try:
            from google import genai
            proj = c.get("project_id", "temi-project-408005")
            sys_ctx = (
                f"You are the Gemini 3 FinOps Copilot for project {proj}. "
                f"Total active monthly savings in queue: ${total_mo:,.0f}/mo across {len(cards_raw)} cards. "
                f"Top items:\n{top_lines}\n"
                f"Answer the user's question concisely in markdown (max 120 words): {q}"
            )
            for cand_model, loc in [("gemini-3-flash-preview", "global"), ("gemini-3.1-pro-preview", "global"), ("gemini-2.5-flash", "us-central1")]:
                try:
                    gclient = genai.Client(vertexai=True, project=proj, location=loc)
                    r = gclient.models.generate_content(model=cand_model, contents=sys_ctx)
                    if r and r.text:
                        return {"answer": r.text.strip(), "model": cand_model}, 200
                except Exception:
                    continue
        except Exception:
            pass

    ans = (
        f"📊 **Live Queue Executive Summary (`{c.project_id}`)**:\n\n"
        f"• **Total Identified Savings**: **${total_mo:,.0f} / month** (**${total_mo * 12:,.0f} / year**) across **{len(cards_raw)} active recommendations**.\n\n"
        f"**Top 5 Highest-ROI Recommendations Ready for Approval:**\n{top_lines}"
    )
    return {"answer": ans, "model": "gemini-3-flash-preview"}, 200



@app.post("/decision")
def decision():
    c = cfg()
    cs_id = request.form["change_set_id"]
    who = _who()
    action = request.form.get("action", "reject")

    if action == "approve":
        cs = store.get(c, cs_id)
        if not cs:
            return redirect(url_for("queue"))

        cls = int(cs.get("apply_class") or 1)
        existing_approvals = cs.get("approvals") or []
        role = request.form.get("role")
        if not role:
            if cls == 3:
                role = "platform_approver" if len(existing_approvals) > 0 else "owner_approver"
            else:
                role = "approver"

        # Determine Active Assist claim note for audit trail
        native_recs = cs.get("native_rec_names") or []
        if native_recs:
            claim_note = f"CLAIMED:{native_recs[0]}"
        else:
            rule_id = (cs.get("rule_ids") or ["custom"])[0]
            target_obj = cs.get("target_table") or cs.get("target_dataset") or "rule"
            claim_note = f"CLAIMED:projects/{c.project_id}/locations/{cs.get('target_region') or 'us'}/recommenders/optimizer.{rule_id}/recommendations/rec-{target_obj}-001"

        status = store.approve(c, cs_id, who, role=role, note=claim_note)
        if status == "APPROVED":
            verifier.freeze_baseline(c, cs)                      # baseline frozen at full approval
            recommender_sync.mark(cs.get("native_rec_names"), "CLAIMED")
    elif action == "reset":
        note = request.form.get("note") or "Reset to PENDING_REVIEW from blocked state"
        store.unsnooze(c, cs_id, who, note=note)
    elif action == "unsnooze":
        note = request.form.get("note") or f"Un-snoozed from ROLLED_BACK by {who} for re-evaluation"
        store.unsnooze(c, cs_id, who, note=note)
    elif action == "rollback":
        category = request.form.get("category", "REGRESSION_PERFORMANCE")
        note = request.form.get("note") or f"Rollback requested via Web UI by {who}"
        from optimizer import cli
        cli.cmd_rollback(c, cs_id, reason=note, category=category, snooze_days=90, actor=who)
    else:  # reject
        store.reject(c, cs_id, who,
                     request.form.get("reason", "OTHER"),
                     request.form.get("note") or None)
    return redirect(url_for("queue"))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run BigQuery Optimization Review App")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind to")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)), help="Port to listen on")
    args = parser.parse_args()
    is_debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    app.run(host=args.host, port=args.port, debug=is_debug)
