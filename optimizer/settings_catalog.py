"""Settings catalog: every setting the tool reads, in one list, in plain English.

Powers the review UI's Settings page (`GET /api/settings`), its "Download config.yaml" export
and Phase 2 editing (`POST /api/settings`). tests/test_settings.py keeps it complete: every
default in optimizer/config.py and every BQOPT_* environment variable must be listed here.

Buckets:
  startup   needed before the tool can read anything; set when deploying (never editable in the UI)
  everyday  regions, scope, ownership tables, thresholds; editable in the UI by the right people
  safety    who can approve and what the tool may change on its own; deploy-time only on purpose

Who may edit an everyday setting (`edit`):
  editor   settings editors (added by a settings admin in the UI) and settings admins
  finops   editors / admins who are also FinOps approvers: these change the savings numbers
  admin    settings admins only (governance.settings_admins, set at deploy time)
  locked   nobody in the UI (startup + safety switches)
"""
from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any, Mapping

import yaml

from .config import _DEFAULTS, cfg

BUCKETS = [
    {"id": "startup", "title": "Startup",
     "summary": "Set once when the tool is deployed. The tool needs these to find everything else."},
    {"id": "everyday", "title": "Everyday settings",
     "summary": "Regions, scope, ownership tables and thresholds. Editable here by settings editors."},
    {"id": "safety", "title": "Safety switches",
     "summary": "Who can approve or edit, and what the tool may change on its own. Deliberately deploy-time only."},
]

APPLIES = {
    "now": "Right away",
    "owners": "Right away (owner lookups are rebuilt)",
    "collect": "At the next collector run (nightly)",
    "rules": "At the next rules run (nightly)",
    "verify": "At the next verification run",
    "deploy": "At the next deploy",
}

_UNSET = object()

# Canonical column schemas for kind="map" settings (kept in lockstep with optimizer/attribution.py).
# default_col is the source column assumed when a key is omitted; None means omitted = NULL.
HIERARCHY_FIELDS = [
    {"key": "user_email", "label": "Employee email", "default_col": "user_email", "required": True},
    {"key": "user_name", "label": "Employee name", "default_col": "user_name", "required": False},
    {"key": "department", "label": "Department", "default_col": "department", "required": False},
    {"key": "manager_email", "label": "Manager email", "default_col": "manager_email", "required": False},
    {"key": "director_email", "label": "Director email", "default_col": "director_email", "required": False},
    {"key": "director_name", "label": "Director name", "default_col": "director_name", "required": False},
    {"key": "cost_center", "label": "Cost center", "default_col": "cost_center", "required": False},
]

SA_OWNER_FIELDS = [
    {"key": "service_account", "label": "Service account email", "default_col": None, "required": True,
     "hint": "service_account_email"},
    {"key": "owner", "label": "Accountable owner email", "default_col": None, "required": True,
     "hint": "owner_email"},
    {"key": "owner_team", "label": "Owner team / dept", "default_col": None, "required": False,
     "hint": "owner_team"},
    {"key": "director_email", "label": "Director email", "default_col": None, "required": False,
     "hint": "director_email"},
    {"key": "director_name", "label": "Director name", "default_col": None, "required": False,
     "hint": "director_name"},
    {"key": "application", "label": "Application / workload", "default_col": None, "required": False,
     "hint": "application"},
]


def _s(key: str, label: str, bucket: str, group: str, desc: str, *, env: str | None = None,
       default: Any = _UNSET, secret: bool = False, edit: str = "locked", kind: str = "text",
       nullable: bool = False, min: float | None = None, max: float | None = None,
       choices: list[str] | None = None, presets: list[dict] | None = None,
       fields: list[dict] | None = None, table_key: str | None = None,
       applies: str = "deploy") -> dict:
    if bucket != "everyday":
        edit = "locked"
    return {"key": key, "label": label, "bucket": bucket, "group": group, "desc": desc, "env": env,
            "default": default, "secret": secret, "edit": edit, "kind": kind, "nullable": nullable,
            "min": min, "max": max, "choices": choices, "presets": presets, "fields": fields,
            "table_key": table_key, "applies": applies}


SETTINGS: list[dict] = [
    # ------------------------------------------------------------------ startup
    _s("project_id", "Project", "startup", "Where the tool runs",
       "Google Cloud project that holds the tool's dataset and runs its queries.",
       env="GOOGLE_CLOUD_PROJECT", default=None),
    _s("ops_dataset", "Tool dataset", "startup", "Where the tool runs",
       "Dataset where the tool keeps its history, recommendations and audit trail."),
    _s("location", "Home location", "startup", "Where the tool runs",
       "BigQuery location of the tool's dataset. Always collected directly; other regions are "
       "fetched where they live and merged here.", env="BQOPT_LOCATION"),

    # ----------------------------------------------------------------- everyday
    _s("regions", "Regions to collect", "everyday", "Regions",
       '"auto" finds every location that holds a BigQuery dataset (Cloud Asset Inventory) on each '
       'run. Or a fixed list, e.g. us, us-east4.', env="BQOPT_REGIONS",
       edit="editor", kind="regions_mode", applies="collect"),
    _s("asset_scope", "Discovery scope", "everyday", "Regions",
       'Where "auto" looks: organizations/ID (recommended), folders/ID or projects/ID. '
       "Not set = this project only.", env="BQOPT_ASSET_SCOPE",
       edit="editor", kind="scope", nullable=True, applies="collect"),
    _s("regions_include", "Always collect", "everyday", "Regions",
       "Regions to collect even if no datasets are found there.",
       edit="editor", kind="region_list", applies="collect"),
    _s("regions_exclude", "Never collect", "everyday", "Regions", "Regions to skip.",
       edit="editor", kind="region_list", applies="collect"),
    _s("region_alert_days", "New-region alert (days)", "everyday", "Regions",
       "How long a newly found region stays flagged in the UI.",
       edit="editor", kind="int", min=1, max=90, applies="now",
       presets=[{"value": 3, "label": "3 days"},
                {"value": 7, "label": "7 days (default)"},
                {"value": 14, "label": "14 days (2 weeks)"},
                {"value": 30, "label": "30 days (1 month)"},
                {"value": 60, "label": "60 days"},
                {"value": 90, "label": "90 days"}]),

    _s("jobs_view", "Query history scope", "everyday", "Query history",
       "JOBS = this project only. JOBS_BY_ORGANIZATION = every project in the organization "
       "(needs an org-level role; BigQuery leaves query text out of that view).", env="BQOPT_JOBS_VIEW",
       edit="editor", kind="enum", choices=["JOBS", "JOBS_BY_ORGANIZATION"], applies="collect",
       presets=[{"value": "JOBS", "label": "JOBS — This project only (keeps full SQL text · default)"},
                {"value": "JOBS_BY_ORGANIZATION",
                 "label": "JOBS_BY_ORGANIZATION — Every project in the org (omits SQL text)"}]),
    _s("recs_view", "Google recommendations view", "everyday", "Query history",
       "RECOMMENDATIONS (this project) or RECOMMENDATIONS_BY_ORGANIZATION.", default="RECOMMENDATIONS",
       edit="editor", kind="enum", choices=["RECOMMENDATIONS", "RECOMMENDATIONS_BY_ORGANIZATION"],
       applies="collect",
       presets=[{"value": "RECOMMENDATIONS", "label": "RECOMMENDATIONS — This project only (default)"},
                {"value": "RECOMMENDATIONS_BY_ORGANIZATION",
                 "label": "RECOMMENDATIONS_BY_ORGANIZATION — Every project in the org"}]),
    _s("initial_backfill_days", "First run reads (days)", "everyday", "Query history",
       "How far back the first collection reads. BigQuery keeps 180 days of job history.", default=180,
       edit="editor", kind="int", min=1, max=180, applies="collect",
       presets=[{"value": 30, "label": "30 days"},
                {"value": 60, "label": "60 days"},
                {"value": 90, "label": "90 days"},
                {"value": 180, "label": "180 days — BigQuery maximum (default)"}]),
    _s("incremental_lookback_days", "Daily overlap (days)", "everyday", "Query history",
       "Each daily run re-reads this many days so late-finishing jobs aren't missed.", default=3,
       edit="editor", kind="int", min=1, max=30, applies="collect",
       presets=[{"value": 1, "label": "1 day"},
                {"value": 2, "label": "2 days"},
                {"value": 3, "label": "3 days (default)"},
                {"value": 7, "label": "7 days (1 week)"},
                {"value": 14, "label": "14 days (2 weeks)"}]),
    _s("remote_fetch_chunk_hours", "Remote fetch window (hours)", "everyday", "Query history",
       "Size of each fetch for non-home regions. Bigger = fewer queries, larger loads.", default=24,
       edit="editor", kind="int", min=1, max=4380, applies="collect",
       presets=[{"value": 6, "label": "6 hours (high job volume)"},
                {"value": 24, "label": "24 hours — 1 day (default)"},
                {"value": 168, "label": "168 hours — 7 days"},
                {"value": 720, "label": "720 hours — 30 days (fewer queries)"}]),
    _s("target_dataset", "Only this dataset", "everyday", "Query history",
       "Optional: collect and analyze a single dataset. Not set = every dataset.",
       env="BQOPT_TARGET_DATASET", default=None,
       edit="editor", kind="dataset", nullable=True, applies="collect"),

    _s("reservation_admin_project", "Reservation admin project", "everyday", "Ownership & billing",
       "Project that owns your slot reservations. Reservation jobs are priced at their edition's "
       "slot rate (Enterprise list price if not set).", env="BQOPT_RESERVATION_ADMIN_PROJECT",
       edit="editor", kind="project", nullable=True, applies="collect"),
    _s("employee_hierarchy_table", "HR hierarchy table", "everyday", "Ownership & billing",
       "Employee -> manager -> director table (project.dataset.table), for card owners and spend by "
       "director. Not set = optimizer_ops.employee_hierarchy.", env="BQOPT_EMPLOYEE_HIERARCHY_TABLE",
       edit="editor", kind="table", nullable=True, applies="owners"),
    _s("employee_hierarchy_columns", "HR table column names", "everyday", "Ownership & billing",
       "Your column names, when they differ from ours: pick each column from your HR table.",
       edit="editor", kind="map", fields=HIERARCHY_FIELDS, table_key="employee_hierarchy_table",
       applies="owners"),
    _s("service_account_owner_table", "Service-account owner table", "everyday", "Ownership & billing",
       "Maps service accounts (Power BI, ETL ...) to the person accountable for their spend "
       "(project.dataset.table).", env="BQOPT_SERVICE_ACCOUNT_OWNER_TABLE",
       edit="editor", kind="table", nullable=True, applies="owners"),
    _s("service_account_owner_columns", "Owner table column names", "everyday", "Ownership & billing",
       "Your column names: service_account and owner are required.",
       edit="editor", kind="map", fields=SA_OWNER_FIELDS, table_key="service_account_owner_table",
       applies="owners"),
    _s("billing_export_table", "Billing export table", "everyday", "Ownership & billing",
       "Optional: your Cloud Billing export table (project.dataset.table).", env="BQOPT_BILLING_EXPORT_TABLE",
       edit="editor", kind="table", nullable=True, applies="rules"),
    _s("default_owner", "Fallback owner", "everyday", "Ownership & billing",
       "Owner used when a card's owner can't be found. Not set = data-platform-admin@<project>.",
       default=None, edit="editor", kind="email", nullable=True, applies="rules"),

    _s("min_card_net_monthly_usd", "Minimum card value ($/month)", "everyday", "Savings math",
       "Fixes worth less than this per month, after running costs, never become cards. "
       "0 = never queue a fix that loses money.",
       edit="finops", kind="float", min=0, max=100000, applies="rules",
       presets=[{"value": 0, "label": "$0 / month — hide money-losing fixes only (default)"},
                {"value": 10, "label": "$10 / month"},
                {"value": 25, "label": "$25 / month"},
                {"value": 50, "label": "$50 / month"},
                {"value": 100, "label": "$100 / month"},
                {"value": 250, "label": "$250 / month"},
                {"value": 500, "label": "$500 / month"}]),
    _s("pricing.reservation_savings_realization", "Reservation savings that become cash", "everyday",
       "Savings math", "Share (0-1) of slot-time savings that turns into cash. 1.0 = autoscale slots "
       "you stop paying for; lower it when most capacity is committed baseline.",
       edit="finops", kind="float", min=0, max=1, applies="rules",
       presets=[{"value": 1.0, "label": "1.0 (100%) — Autoscale slots you stop paying for (default)"},
                {"value": 0.75, "label": "0.75 (75%) — Mostly autoscale, small baseline"},
                {"value": 0.5, "label": "0.5 (50%) — Mix of baseline and autoscale"},
                {"value": 0.25, "label": "0.25 (25%) — Mostly committed baseline"},
                {"value": 0.0, "label": "0.0 (0%) — Fixed baseline only (no cash savings)"}]),
    _s("pricing.w01_min_on_demand_tib_30d", "Editions card threshold (TiB / 30 days)", "everyday",
       "Savings math", "Only suggest moving a project to slot billing (W-01) when it scans at least "
       "this much on-demand in 30 days.", default=100.0,
       edit="finops", kind="float", min=0, max=1000000, applies="rules",
       presets=[{"value": 10, "label": "10 TiB / 30 days (~$62/mo on-demand)"},
                {"value": 50, "label": "50 TiB / 30 days (~$312/mo on-demand)"},
                {"value": 100, "label": "100 TiB / 30 days (~$625/mo on-demand · default)"},
                {"value": 250, "label": "250 TiB / 30 days (~$1,560/mo on-demand)"},
                {"value": 500, "label": "500 TiB / 30 days (~$3,125/mo on-demand)"},
                {"value": 1000, "label": "1,000 TiB / 30 days (~$6,250/mo on-demand)"}]),
    _s("amortization_months", "Spread one-time costs over (months)", "everyday", "Savings math",
       "One-time costs of a fix (e.g. a rebuild) are divided over this many months in its net value.",
       edit="finops", kind="int", min=1, max=60, applies="rules",
       presets=[{"value": 3, "label": "3 months (1 quarter)"},
                {"value": 6, "label": "6 months"},
                {"value": 12, "label": "12 months — 1 year (default)"},
                {"value": 24, "label": "24 months (2 years)"},
                {"value": 36, "label": "36 months (3 years)"}]),
    _s("native_cap_scan_reduction", "Cap on Google's own estimates", "everyday", "Savings math",
       "Google recommender savings are capped at this share (0-1) of the table's real spend.", default=0.68,
       edit="finops", kind="float", min=0, max=1, applies="rules",
       presets=[{"value": 0.5, "label": "0.50 (50% max of table spend — conservative)"},
                {"value": 0.68, "label": "0.68 (68% max of table spend — default)"},
                {"value": 0.8, "label": "0.80 (80% max of table spend)"},
                {"value": 1.0, "label": "1.00 (100% — up to full table spend)"}]),
    _s("risk_weights", "Risk weight by class", "everyday", "Savings math",
       "A card's ranking score is divided by its class's weight (riskier classes rank lower). "
       "Higher weight = ranked lower.",
       edit="finops", kind="weights", applies="rules",
       presets=[{"value": {"1": 1.0, "2": 1.3, "3": 2.0, "4": 1.5},
                 "label": "Balanced (default: Class 1=1.0 · Class 2=1.3 · Class 3=2.0 · Class 4=1.5)"},
                {"value": {"1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0},
                 "label": "Equal weight across all classes (1.0 each)"},
                {"value": {"1": 1.0, "2": 1.5, "3": 3.0, "4": 2.0},
                 "label": "Strongly prefer low-risk Class 1 fixes (1.0 · 1.5 · 3.0 · 2.0)"}]),

    _s("evidence_stale_days", "Evidence expires after (days)", "everyday", "Cards & queries",
       "Cards whose evidence is older than this are expired.",
       edit="editor", kind="int", min=1, max=365, applies="rules",
       presets=[{"value": 14, "label": "14 days (2 weeks)"},
                {"value": 30, "label": "30 days (1 month)"},
                {"value": 45, "label": "45 days (default)"},
                {"value": 60, "label": "60 days"},
                {"value": 90, "label": "90 days"}]),
    _s("max_bytes_billed_per_query", "Byte cap on the tool's own queries", "everyday", "Cards & queries",
       "Optional cost guardrail for every query the tool runs. Not set = no cap. "
       "Keep it above about 10 GB so collection still works.", default=None,
       edit="editor", kind="int", nullable=True, min=10485760, max=10 ** 15, applies="now",
       presets=[{"value": 10737418240, "label": "10 GiB (~$0.06 max · minimum recommended)"},
                {"value": 53687091200, "label": "50 GiB (~$0.31 max per on-demand query)"},
                {"value": 107374182400, "label": "100 GiB (~$0.63 max per on-demand query)"},
                {"value": 549755813888, "label": "512 GiB (~$3.13 max per on-demand query)"},
                {"value": 1099511627776, "label": "1 TiB (~$6.25 max per on-demand query)"}]),

    _s("verify_window_days", "Verify savings after (days)", "everyday", "Verification",
       "How long after a change the tool measures real savings and writes the receipt.",
       edit="admin", kind="int", min=0, max=90, applies="verify",
       presets=[{"value": 0, "label": "0 days — verify right away (sandbox / demo)"},
                {"value": 7, "label": "7 days (1 week)"},
                {"value": 14, "label": "14 days — 2 weeks (default)"},
                {"value": 30, "label": "30 days (1 month)"}]),
    _s("regression_pct", "Regression threshold (%)", "everyday", "Verification",
       "A query family counts as regressed when it scans this much more than before.",
       edit="admin", kind="float", min=1, max=1000, applies="verify",
       presets=[{"value": 10, "label": "10% scan increase (strict)"},
                {"value": 15, "label": "15% scan increase (default)"},
                {"value": 25, "label": "25% scan increase"},
                {"value": 50, "label": "50% scan increase (relaxed)"}]),
    _s("min_family_execs", "Minimum runs per query family", "everyday", "Verification",
       "Query families with fewer runs are ignored when checking for regressions.",
       edit="admin", kind="int", min=1, max=100000, applies="verify",
       presets=[{"value": 5, "label": "5 runs"},
                {"value": 10, "label": "10 runs"},
                {"value": 20, "label": "20 runs (default)"},
                {"value": 50, "label": "50 runs"},
                {"value": 100, "label": "100 runs"}]),
    _s("min_regressed_families", "Regressed families to flag a change", "everyday", "Verification",
       "How many regressed query families mark a change as REGRESSED.",
       edit="admin", kind="int", min=1, max=1000, applies="verify",
       presets=[{"value": 1, "label": "1 query family (strictest)"},
                {"value": 2, "label": "2 query families"},
                {"value": 3, "label": "3 query families (default)"},
                {"value": 5, "label": "5 query families"}]),

    _s("ai_judge.enabled", "Gemini SQL judge", "everyday", "Detection engines",
       "Use Gemini to review expensive SQL for anti-patterns (each finding is validated with a dry run).",
       default=True, edit="editor", kind="bool", applies="rules"),
    _s("ai_judge.model", "Gemini model", "everyday", "Detection engines",
       "Model used by the SQL judge.", default="gemini-3-flash-preview",
       edit="editor", kind="model", applies="rules",
       presets=[{"value": "gemini-3-flash-preview", "label": "gemini-3-flash-preview — Fast & low cost (default)"},
                {"value": "gemini-3.1-pro-preview", "label": "gemini-3.1-pro-preview — Deepest SQL reasoning"},
                {"value": "gemini-2.5-flash", "label": "gemini-2.5-flash — GA fast model"},
                {"value": "gemini-2.5-pro", "label": "gemini-2.5-pro — GA pro model"}]),
    _s("antipattern_tool.enabled", "Google anti-pattern analyzer", "everyday", "Detection engines",
       "Run Google's open-source BigQuery anti-pattern recognizer on expensive queries.", default=True,
       edit="editor", kind="bool", applies="rules"),
    _s("antipattern_tool.top_expensive_queries_limit", "Queries analyzed per run", "everyday",
       "Detection engines", "How many of the most expensive queries the SQL engines look at.", default=100,
       edit="editor", kind="int", min=1, max=1000, applies="rules",
       presets=[{"value": 25, "label": "Top 25 queries"},
                {"value": 50, "label": "Top 50 queries"},
                {"value": 100, "label": "Top 100 queries (default)"},
                {"value": 250, "label": "Top 250 queries"},
                {"value": 500, "label": "Top 500 queries"}]),
    _s("antipattern_tool.expensive_query_cost_usd", "Expensive query threshold ($)", "everyday",
       "Detection engines", "Only queries costing at least this much are analyzed.", default=5.0,
       edit="editor", kind="float", min=0, max=100000, applies="rules",
       presets=[{"value": 0, "label": "$0 — analyze every query up to the limit"},
                {"value": 1, "label": "$1.00 per query"},
                {"value": 5, "label": "$5.00 per query (default)"},
                {"value": 10, "label": "$10.00 per query"},
                {"value": 25, "label": "$25.00 per query"},
                {"value": 100, "label": "$100.00 per query"}]),

    # ------------------------------------------------------------------- safety
    _s("governance.settings_admins", "Settings admins", "safety", "Who can approve and edit",
       "People who can change settings on this page and add or remove settings editors. "
       "Set at deploy time, so nobody can make themselves an admin from the UI.",
       env="BQOPT_SETTINGS_ADMINS"),
    _s("governance.finops_approvers", "FinOps approvers", "safety", "Who can approve and edit",
       "People who can see and approve billing, commitment and reservation cards, and change the "
       "savings-math settings. Empty = nobody.", env="BQOPT_FINOPS_APPROVERS"),
    _s("governance.finops_rule_ids", "FinOps-only rules", "safety", "Who can approve and edit",
       "Rules whose cards only FinOps approvers can see and approve."),
    _s("approval_ttl_days", "Approvals expire after (days)", "safety", "Who can approve and edit",
       "An approved change not applied within this many days needs approval again."),
    _s("governance.trust_client_identity", "Trust the identity picker (demo only)", "safety",
       "Who can approve and edit", "Lets anyone choose who they are in the header. Keep off in production "
       "and put the app behind Identity-Aware Proxy."),
    _s("governance.iap_audience", "IAP audience", "safety", "Who can approve and edit",
       "When set, the app only trusts identities verified by Identity-Aware Proxy."),
    _s("executor.enable_class1", "Apply Class 1 changes", "safety", "What the tool may change",
       "In-place settings such as clustering, partition filters and expirations."),
    _s("executor.enable_class2", "Apply Class 2 changes", "safety", "What the tool may change",
       "Materialized views (they add a running cost)."),
    _s("executor.enable_class3", "Apply Class 3 changes", "safety", "What the tool may change",
       "Table rebuilds (copy, swap, rebind permissions). Needs two approvals."),
    _s("executor.enable_reservation_changes", "Apply reservation changes (W-01)", "safety",
       "What the tool may change", "Creates real reservations and moves a project to slot billing."),
    _s("executor.change_window_utc", "Change window (UTC hours)", "safety", "What the tool may change",
       "Approved changes are only applied between these hours."),
    _s("executor.backup_hold_days", "Keep backups for (days)", "safety", "What the tool may change",
       "How long the zero-copy backup of a rebuilt table is kept for rollback."),
    _s("executor.max_structural_changes_per_table_per_days", "One rebuild per table every (days)",
       "safety", "What the tool may change", "Stops repeated rebuilds of the same table."),
    _s("iac_managed_datasets", "Datasets managed in code", "safety", "What the tool may change",
       "Datasets owned by Terraform, dbt etc. Changes to them become pull requests, never direct changes.",
       default=[]),
    _s("iac_codeowner", "Reviewer for those pull requests", "safety", "What the tool may change",
       "Who reviews pull requests for datasets managed in code.", default=None),
    _s("demo_mode", "Demo mode (sandbox only)", "safety", "Other",
       "Shows labelled synthetic minimum savings so a tiny demo has every card type. "
       "Never turn on for a real customer."),
    _s("slack_webhook", "Slack webhook for alerts", "safety", "Other",
       "Where regression alerts are posted. Prefer the SLACK_WEBHOOK_URL environment variable "
       "(kept in Secret Manager).", env="SLACK_WEBHOOK_URL", secret=True),
]

BY_KEY = {s["key"]: s for s in SETTINGS}
EDITABLE_KEYS = frozenset(s["key"] for s in SETTINGS if s["edit"] != "locked")
OWNER_KEYS = frozenset(s["key"] for s in SETTINGS if s["applies"] == "owners")

# Environment variables optimizer/config.py reads, mapped to the setting they override.
ENV_VARS = {s["env"]: s["key"] for s in SETTINGS if s["env"]}
ENV_VARS.update({"GCP_PROJECT": "project_id"})


# ----------------------------------------------------------------------------- access

def settings_admins(c: Mapping) -> set[str]:
    gov = c.get("governance") if isinstance(c.get("governance"), Mapping) else {}
    out = {str(x).strip().lower() for x in (gov.get("settings_admins") or []) if str(x).strip()}
    out |= {x.strip().lower() for x in os.environ.get("BQOPT_SETTINGS_ADMINS", "").split(",") if x.strip()}
    return out


def access_for(identity: Mapping | None, c: Mapping, editors: list[str] | None = None) -> dict:
    """What this person may do on the Settings page. Mirrors the FinOps identity rules: a
    client-picked identity counts only in demo mode, and an unidentified visitor never counts."""
    from . import governance
    identity = identity or {}
    email = str(identity.get("email") or "").strip().lower()
    src = identity.get("source")
    demo = governance.trust_client_identity(c)
    trusted = bool(email) and not (src in (governance.SOURCE_CLIENT, governance.SOURCE_PLACEHOLDER) and not demo)
    admins = settings_admins(c)
    eds = sorted({str(e).strip().lower() for e in (editors or []) if str(e).strip()})
    is_admin = trusted and email in admins
    is_editor = is_admin or (trusted and email in eds)
    return {"email": email, "source": src, "verified": src == governance.SOURCE_IAP,
            "demo_identity": demo and src != governance.SOURCE_IAP,
            "is_admin": is_admin, "is_editor": is_editor,
            "is_finops": governance.is_finops_viewer(identity, c),
            "admins": sorted(admins), "editors": eds}


def can_edit(s: Mapping, access: Mapping) -> tuple[bool, str | None]:
    """(allowed, reason when not allowed) for one setting."""
    if s["edit"] == "locked":
        return False, ("Startup setting: set when the tool is deployed." if s["bucket"] == "startup"
                       else "Safety switch: changed only at deploy time, on purpose.")
    if s.get("env") and os.environ.get(s["env"]):
        return False, f"Pinned by the deployment's {s['env']} environment variable."
    if s["edit"] == "admin" and not access.get("is_admin"):
        return False, "Only settings admins can change this."
    if s["edit"] == "finops" and not (access.get("is_editor") and access.get("is_finops")):
        return False, "Changes the savings numbers: only settings editors who are FinOps approvers."
    if s["edit"] == "editor" and not access.get("is_editor"):
        return False, "Only settings editors can change this."
    return True, None


# ----------------------------------------------------------------------------- validation

_SCOPE_RE = re.compile(r"^(organizations|folders|projects)/[A-Za-z0-9_.:\-]+$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_.:\-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_\-$]+$")
_PROJECT_RE = re.compile(r"^[a-z][-a-z0-9:.]{4,62}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,99}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = re.split(r"[,\s]+", v)
    return [str(x).strip() for x in v if str(x).strip()]


def _as_map(v: Any) -> dict:
    if isinstance(v, str):
        v = yaml.safe_load(v) if v.strip() else {}
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("Use one 'name: value' pair per line.")
    return v


def _regions(values: list[str]) -> list[str]:
    from .regions import normalize
    out: list[str] = []
    for v in values:
        n = normalize(v)
        if not n:
            raise ValueError(f"'{v}' isn't a BigQuery location (e.g. us, eu, us-east4).")
        if n not in out:
            out.append(n)
    return out


def validate(key: str, raw: Any, c: Mapping | None = None) -> Any:
    """Normalized value for an everyday setting, or ValueError with a plain-English reason."""
    s = BY_KEY.get(key)
    if s is None:
        raise ValueError(f"Unknown setting {key!r}.")
    if s["edit"] == "locked":
        raise ValueError("This setting can only be changed at deploy time.")
    kind = s["kind"]
    if kind in ("region_list",):
        vals = _regions(_as_list(raw))
        if key == "regions_exclude" and c is not None:
            from .regions import home_region
            if home_region(c) in vals:
                raise ValueError(f"You can't skip the home location ({home_region(c)}): the tool's own "
                                 "dataset lives there.")
        return vals
    if kind == "regions_mode":
        if isinstance(raw, str) and raw.strip().lower() == "auto":
            return "auto"
        vals = _regions(_as_list(raw))
        if not vals:
            raise ValueError('Use "auto" or list at least one region.')
        return vals
    if kind in ("map", "weights"):
        m = _as_map(raw)
        if kind == "weights":
            try:
                w = {int(k): float(v) for k, v in m.items()}
            except (TypeError, ValueError):
                raise ValueError("Use one 'class: weight' pair per line, e.g. 1: 1.0") from None
            if sorted(w) != [1, 2, 3, 4] or any(x <= 0 for x in w.values()):
                raise ValueError("Give a positive weight for each class 1, 2, 3 and 4.")
            return {str(k): v for k, v in sorted(w.items())}
        out = {}
        for k, v in m.items():
            if not _IDENT_RE.match(str(k)) or (v is not None and not _IDENT_RE.match(str(v))):
                raise ValueError(f"'{k}: {v}' isn't a valid column mapping (letters, digits, underscores).")
            out[str(k)] = None if v is None else str(v)
        return out
    if _blank(raw):
        if s["nullable"]:
            return None
        raise ValueError("A value is required.")
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        t = str(raw).strip().lower()
        if t in ("true", "on", "yes", "1"):
            return True
        if t in ("false", "off", "no", "0"):
            return False
        raise ValueError("Use On or Off.")
    if kind in ("int", "float"):
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise ValueError("Enter a number.") from None
        if kind == "int":
            if num != int(num):
                raise ValueError("Enter a whole number.")
            num = int(num)
        if s["min"] is not None and num < s["min"]:
            raise ValueError(f"Must be at least {s['min']:g}.")
        if s["max"] is not None and num > s["max"]:
            raise ValueError(f"Must be at most {s['max']:g}.")
        return num
    text = str(raw).strip()
    if kind == "enum":
        t = text.upper()
        if t not in (s["choices"] or []):
            raise ValueError("Choose one of: " + ", ".join(s["choices"] or []) + ".")
        return t
    checks = {"scope": (_SCOPE_RE, "Use organizations/ID, folders/ID or projects/ID."),
              "table": (_TABLE_RE, "Use the full name project.dataset.table."),
              "project": (_PROJECT_RE, "That doesn't look like a project ID."),
              "dataset": (_DATASET_RE, "That doesn't look like a dataset name."),
              "email": (_EMAIL_RE, "Enter an email address."),
              "model": (_MODEL_RE, "That doesn't look like a model name.")}
    if kind in checks:
        rx, msg = checks[kind]
        if not rx.match(text.strip("`")):
            raise ValueError(msg)
        return text.strip("`")
    return text


# ----------------------------------------------------------------------------- explain

def _dig(d: Any, dotted: str) -> Any:
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _UNSET
        cur = cur[part]
    return cur


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    return v


def _source(s: dict, raw: dict, saved: Mapping) -> str:
    key, env = s["key"], s["env"]
    in_yaml = _dig(raw, key) is not _UNSET
    if key == "project_id":
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "env GOOGLE_CLOUD_PROJECT"
        if os.environ.get("GCP_PROJECT"):
            return "env GCP_PROJECT"
        return "config.yaml" if in_yaml else "gcloud default project"
    if key == "slack_webhook":
        if os.environ.get("SLACK_WEBHOOK_URL"):
            return "env SLACK_WEBHOOK_URL"
        in_yaml = in_yaml or _dig(raw, "slack_webhook_url") is not _UNSET
        return "config.yaml" if in_yaml else "default"
    if env and os.environ.get(env):
        if key in ("governance.finops_approvers", "governance.settings_admins") and in_yaml:
            return f"config.yaml + env {env}"     # these env lists are added to the file's list
        return f"env {env}"
    if key in saved:
        return "saved in the app"
    return "config.yaml" if in_yaml else "default"


def _effective(s: dict, c: Mapping) -> Any:
    key = s["key"]
    if key == "governance.finops_approvers":
        from .governance import finops_approvers
        return sorted(finops_approvers(c))
    if key == "governance.settings_admins":
        return sorted(settings_admins(c))
    if key == "slack_webhook":
        return os.environ.get("SLACK_WEBHOOK_URL") or c.get("slack_webhook_url") or c.get("slack_webhook")
    v = _dig(c, key)
    if v is _UNSET:
        v = None if s["default"] is _UNSET else s["default"]
    return v


def file_value(key: str, path: str | None = None) -> Any:
    """Value a setting would have without anything saved in the app (config.yaml or default)."""
    c = cfg(path, ui=False)
    return _jsonable(_effective(BY_KEY[key], c))


def explain(c: Mapping | None = None, path: str | None = None, saved: Mapping | None = None,
            access: Mapping | None = None) -> dict:
    """Every setting with its current value, default, where the value came from and, when
    `access` is given, whether this person may edit it."""
    path = path or os.environ.get("BQOPT_CONFIG", "config.yaml")
    raw: dict = {}
    found = os.path.exists(path)
    if found:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    c = c if c is not None else cfg(path)
    saved = saved or {}
    items = []
    for s in SETTINGS:
        value = _effective(s, c)
        default = _dig(_DEFAULTS, s["key"])
        if default is _UNSET:
            default = None if s["default"] is _UNSET else s["default"]
        if s["secret"]:
            value = "set (hidden)" if value else None
        item = {k: s[k] for k in ("key", "label", "bucket", "group", "desc", "env", "secret", "edit",
                                  "kind", "nullable", "min", "max", "choices", "presets", "fields",
                                  "table_key", "applies")}
        item.update({"value": _jsonable(value), "default": _jsonable(default),
                     "source": _source(s, raw, saved), "applies_text": APPLIES.get(s["applies"], "")})
        if s["key"] in saved:
            sv = saved[s["key"]]
            item["saved"] = {"by": sv.get("changed_by"), "at": sv.get("changed_at"), "note": sv.get("note")}
        if access is not None:
            ok, why = can_edit(s, access)
            item["editable_by_you"], item["locked_reason"] = ok, why
        items.append(item)
    return {"config_file": os.path.basename(path), "config_file_found": found,
            "buckets": BUCKETS, "settings": items}


def _dump(key: str, value: Any, indent: str = "") -> list[str]:
    text = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return [indent + line for line in text.rstrip("\n").split("\n")]


def to_yaml(explained: dict) -> str:
    """A commented config.yaml with the values the tool is using now (secrets left out)."""
    today = dt.date.today().isoformat()
    lines = [f"# bq-optimizer config.yaml, exported from the Settings page on {today}.",
             "# Values are what the tool is using now, including any saved in the app.",
             "# Secrets are not exported.", ""]
    by_bucket = {b["id"]: [i for i in explained["settings"] if i["bucket"] == b["id"]] for b in BUCKETS}
    for b in BUCKETS:
        lines += [f"# ---- {b['title']}: {b['summary']}", ""]
        parents_done: set[str] = set()
        for item in by_bucket[b["id"]]:
            key = item["key"]
            if item["secret"]:
                lines += [f"# {item['label']}: {item['desc']}",
                          f"# {key}: <set the {item['env']} environment variable>", ""]
                continue
            if "." in key:
                parent = key.split(".", 1)[0]
                if parent in parents_done:
                    continue
                parents_done.add(parent)
                lines.append(f"{parent}:")
                for child in [i for i in by_bucket[b["id"]] if i["key"].startswith(parent + ".")]:
                    lines.append(f"  # {child['label']}: {child['desc']}")
                    lines += _dump(child["key"].split(".", 1)[1], child["value"], indent="  ")
                lines.append("")
                continue
            lines.append(f"# {item['label']}: {item['desc']}")
            value = item["value"]
            if key == "risk_weights" and isinstance(value, dict):
                value = {int(k): v for k, v in value.items()}
            lines += _dump(key, value)
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
