"""Settings catalog: every setting the tool reads, in one list, in plain English.

Powers the review UI's Settings page (`GET /api/settings`) and its "copy config.yaml" export.
tests/test_settings.py keeps it complete: every default in optimizer/config.py and every
BQOPT_* environment variable must be listed here, so this is the single list of parameters.

Buckets (Phase 1 shows all of them read-only):
  startup   needed before the tool can read anything; set when deploying
  everyday  regions, scope, ownership tables, thresholds; safe to tune
  safety    who can approve and what the tool may change on its own; deploy-time on purpose
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any

import yaml

from .config import _DEFAULTS, cfg

BUCKETS = [
    {"id": "startup", "title": "Startup",
     "summary": "Set once when the tool is deployed. The tool needs these to find everything else."},
    {"id": "everyday", "title": "Everyday settings",
     "summary": "Regions, scope, ownership tables and thresholds. Safe to tune."},
    {"id": "safety", "title": "Safety switches",
     "summary": "Who can approve and what the tool may change on its own. Deliberately kept out of the UI."},
]

_UNSET = object()


def _s(key: str, label: str, bucket: str, group: str, desc: str, *, env: str | None = None,
       default: Any = _UNSET, secret: bool = False) -> dict:
    return {"key": key, "label": label, "bucket": bucket, "group": group, "desc": desc,
            "env": env, "default": default, "secret": secret}


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
       'run. Or a fixed list, e.g. ["us", "us-east4"].', env="BQOPT_REGIONS"),
    _s("asset_scope", "Discovery scope", "everyday", "Regions",
       'Where "auto" looks: organizations/ID (recommended), folders/ID or projects/ID. '
       "Not set = this project only.", env="BQOPT_ASSET_SCOPE"),
    _s("regions_include", "Always collect", "everyday", "Regions",
       "Regions to collect even if no datasets are found there."),
    _s("regions_exclude", "Never collect", "everyday", "Regions", "Regions to skip."),
    _s("region_alert_days", "New-region alert (days)", "everyday", "Regions",
       "How long a newly found region stays flagged in the UI."),

    _s("jobs_view", "Query history scope", "everyday", "Query history",
       "JOBS = this project only. JOBS_BY_ORGANIZATION = every project in the organization "
       "(needs an org-level role; BigQuery leaves query text out of that view).", env="BQOPT_JOBS_VIEW"),
    _s("recs_view", "Google recommendations view", "everyday", "Query history",
       "RECOMMENDATIONS (this project) or RECOMMENDATIONS_BY_ORGANIZATION.", default="RECOMMENDATIONS"),
    _s("initial_backfill_days", "First run reads (days)", "everyday", "Query history",
       "How far back the first collection reads. BigQuery keeps 180 days of job history.", default=180),
    _s("incremental_lookback_days", "Daily overlap (days)", "everyday", "Query history",
       "Each daily run re-reads this many days so late-finishing jobs aren't missed.", default=3),
    _s("remote_fetch_chunk_hours", "Remote fetch window (hours)", "everyday", "Query history",
       "Size of each fetch for non-home regions. Bigger = fewer queries, larger loads.", default=24),
    _s("target_dataset", "Only this dataset", "everyday", "Query history",
       "Optional: collect and analyze a single dataset. Not set = every dataset.",
       env="BQOPT_TARGET_DATASET", default=None),

    _s("reservation_admin_project", "Reservation admin project", "everyday", "Ownership & billing",
       "Project that owns your slot reservations. Reservation jobs are priced at their edition's "
       "slot rate (Enterprise list price if not set).", env="BQOPT_RESERVATION_ADMIN_PROJECT"),
    _s("employee_hierarchy_table", "HR hierarchy table", "everyday", "Ownership & billing",
       "Employee -> manager -> director table, for card owners and spend by director. "
       "Not set = optimizer_ops.employee_hierarchy.", env="BQOPT_EMPLOYEE_HIERARCHY_TABLE"),
    _s("employee_hierarchy_columns", "HR table column names", "everyday", "Ownership & billing",
       "Your column names, when they differ from ours (e.g. user_email: email)."),
    _s("service_account_owner_table", "Service-account owner table", "everyday", "Ownership & billing",
       "Maps service accounts (Power BI, ETL ...) to the person accountable for their spend.",
       env="BQOPT_SERVICE_ACCOUNT_OWNER_TABLE"),
    _s("service_account_owner_columns", "Owner table column names", "everyday", "Ownership & billing",
       "Your column names, when they differ from ours (service_account is required)."),
    _s("billing_export_table", "Billing export table", "everyday", "Ownership & billing",
       "Optional: your Cloud Billing export table.", env="BQOPT_BILLING_EXPORT_TABLE"),
    _s("default_owner", "Fallback owner", "everyday", "Ownership & billing",
       "Owner used when a card's owner can't be found. Not set = data-platform-admin@<project>.",
       default=None),
    _s("iac_managed_datasets", "Datasets managed in code", "everyday", "Ownership & billing",
       "Datasets owned by Terraform, dbt etc. Changes to them become pull requests, never direct changes.",
       default=[]),
    _s("iac_codeowner", "Reviewer for those pull requests", "everyday", "Ownership & billing",
       "Who reviews pull requests for datasets managed in code.", default=None),

    _s("min_card_net_monthly_usd", "Minimum card value ($/month)", "everyday", "Savings math",
       "Fixes worth less than this per month, after running costs, never become cards. "
       "0 = never queue a fix that loses money."),
    _s("pricing.reservation_savings_realization", "Reservation savings that become cash", "everyday",
       "Savings math", "Share (0-1) of slot-time savings that turns into cash. 1.0 = autoscale slots "
       "you stop paying for; lower it when most capacity is committed baseline."),
    _s("pricing.w01_min_on_demand_tib_30d", "Editions card threshold (TiB / 30 days)", "everyday",
       "Savings math", "Only suggest moving a project to slot billing (W-01) when it scans at least "
       "this much on-demand in 30 days.", default=100.0),
    _s("amortization_months", "Spread one-time costs over (months)", "everyday", "Savings math",
       "One-time costs of a fix (e.g. a rebuild) are divided over this many months in its net value."),
    _s("native_cap_scan_reduction", "Cap on Google's own estimates", "everyday", "Savings math",
       "Google recommender savings are capped at this share of the table's real spend.", default=0.68),
    _s("risk_weights", "Risk weight by class", "everyday", "Savings math",
       "A card's ranking score is divided by its class's weight (riskier classes rank lower)."),
    _s("approval_ttl_days", "Approvals expire after (days)", "everyday", "Savings math",
       "An approved change not applied within this many days needs approval again."),
    _s("evidence_stale_days", "Evidence expires after (days)", "everyday", "Savings math",
       "Cards whose evidence is older than this are expired."),
    _s("max_bytes_billed_per_query", "Byte cap on the tool's own queries", "everyday", "Savings math",
       "Optional cost guardrail for every query the tool runs. Not set = no cap.", default=None),

    _s("verify_window_days", "Verify savings after (days)", "everyday", "Verification",
       "How long after a change the tool measures real savings and writes the receipt."),
    _s("regression_pct", "Regression threshold (%)", "everyday", "Verification",
       "A query family counts as regressed when it scans this much more than before."),
    _s("min_family_execs", "Minimum runs per query family", "everyday", "Verification",
       "Query families with fewer runs are ignored when checking for regressions."),
    _s("min_regressed_families", "Regressed families to flag a change", "everyday", "Verification",
       "How many regressed query families mark a change as REGRESSED."),

    _s("ai_judge.enabled", "Gemini SQL judge", "everyday", "Detection engines",
       "Use Gemini to review expensive SQL for anti-patterns (each finding is validated with a dry run).",
       default=True),
    _s("ai_judge.model", "Gemini model", "everyday", "Detection engines",
       "Model used by the SQL judge.", default="gemini-3-flash-preview"),
    _s("antipattern_tool.enabled", "Google anti-pattern analyzer", "everyday", "Detection engines",
       "Run Google's open-source BigQuery anti-pattern recognizer on expensive queries.", default=True),
    _s("antipattern_tool.top_expensive_queries_limit", "Queries analyzed per run", "everyday",
       "Detection engines", "How many of the most expensive queries the SQL engines look at.", default=100),
    _s("antipattern_tool.expensive_query_cost_usd", "Expensive query threshold ($)", "everyday",
       "Detection engines", "Only queries costing at least this much are analyzed.", default=5.0),

    # ------------------------------------------------------------------- safety
    _s("governance.finops_approvers", "FinOps approvers", "safety", "Who can approve",
       "People who can see and approve billing, commitment and reservation cards. Empty = nobody.",
       env="BQOPT_FINOPS_APPROVERS"),
    _s("governance.finops_rule_ids", "FinOps-only rules", "safety", "Who can approve",
       "Rules whose cards only FinOps approvers can see and approve."),
    _s("governance.trust_client_identity", "Trust the identity picker (demo only)", "safety",
       "Who can approve", "Lets anyone choose who they are in the header. Keep off in production and "
       "put the app behind Identity-Aware Proxy."),
    _s("governance.iap_audience", "IAP audience", "safety", "Who can approve",
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
    _s("demo_mode", "Demo mode (sandbox only)", "safety", "Other",
       "Shows labelled synthetic minimum savings so a tiny demo has every card type. "
       "Never turn on for a real customer."),
    _s("slack_webhook", "Slack webhook for alerts", "safety", "Other",
       "Where regression alerts are posted. Prefer the SLACK_WEBHOOK_URL environment variable "
       "(kept in Secret Manager).", env="SLACK_WEBHOOK_URL", secret=True),
]

# Environment variables optimizer/config.py reads, mapped to the setting they override.
ENV_VARS = {s["env"]: s["key"] for s in SETTINGS if s["env"]}
ENV_VARS.update({"GCP_PROJECT": "project_id"})


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


def _source(s: dict, raw: dict) -> str:
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
        if key == "governance.finops_approvers" and in_yaml:
            return f"config.yaml + env {env}"     # governance.py adds the env list to the file's
        return f"env {env}"
    return "config.yaml" if in_yaml else "default"


def _effective(s: dict, c: dict) -> Any:
    key = s["key"]
    if key == "governance.finops_approvers":
        from .governance import finops_approvers
        return sorted(finops_approvers(c))
    if key == "slack_webhook":
        return os.environ.get("SLACK_WEBHOOK_URL") or c.get("slack_webhook_url") or c.get("slack_webhook")
    v = _dig(c, key)
    if v is _UNSET:
        v = None if s["default"] is _UNSET else s["default"]
    return v


def explain(c: dict | None = None, path: str | None = None) -> dict:
    """Every setting with its current value, default and where the value came from."""
    path = path or os.environ.get("BQOPT_CONFIG", "config.yaml")
    raw: dict = {}
    found = os.path.exists(path)
    if found:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    c = c if c is not None else cfg(path)
    items = []
    for s in SETTINGS:
        value = _effective(s, c)
        default = _dig(_DEFAULTS, s["key"])
        if default is _UNSET:
            default = None if s["default"] is _UNSET else s["default"]
        if s["secret"]:
            value = "set (hidden)" if value else None
        items.append({"key": s["key"], "label": s["label"], "bucket": s["bucket"], "group": s["group"],
                      "desc": s["desc"], "env": s["env"], "secret": s["secret"],
                      "value": _jsonable(value), "default": _jsonable(default),
                      "source": _source(s, raw)})
    return {"config_file": os.path.basename(path), "config_file_found": found,
            "buckets": BUCKETS, "settings": items}


def _dump(key: str, value: Any, indent: str = "") -> list[str]:
    text = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return [indent + line for line in text.rstrip("\n").split("\n")]


def to_yaml(explained: dict) -> str:
    """A commented config.yaml with the values the tool is using now (secrets left out)."""
    today = dt.date.today().isoformat()
    lines = [f"# bq-optimizer config.yaml, exported from the Settings page on {today}.",
             "# Values are what the tool is using now. Secrets are not exported.", ""]
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
