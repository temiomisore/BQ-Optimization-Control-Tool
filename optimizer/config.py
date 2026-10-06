"""Configuration loader.

Reads config.yaml from the repo root (override path with BQOPT_CONFIG).
Every component gets its settings through cfg() so there is exactly one
source of truth for projects, thresholds, and feature flags.
"""
from __future__ import annotations

import os
from typing import Any

import yaml

_DEFAULTS: dict[str, Any] = {
    "ops_dataset": "optimizer_ops",
    "regions": ["us"],
    "location": "US",
    "jobs_view": "JOBS",
    "reservation_admin_project": None,
    "employee_hierarchy_table": None,
    # Map the customer's column names when they differ from ours (see optimizer/attribution.py).
    "employee_hierarchy_columns": {},
    # Service account -> accountable human owner (PowerBI, ETL ...): rolls SA spend up to a Director.
    "service_account_owner_table": None,
    "service_account_owner_columns": {},
    "billing_export_table": None,
    # SANDBOX ONLY. True lets rules apply labelled synthetic minimum values so a tiny
    # demo dataset still shows every card type. Never enable for a real customer.
    "demo_mode": False,
    "pricing": {
        # Share of a reservation slot-time reduction that turns into cash (0..1). 1.0 = autoscale
        # slots you stop paying for; lower it when most capacity is committed baseline.
        "reservation_savings_realization": 1.0,
    },
    "governance": {
        # Who may see and approve billing / commitment / reservation / project-level cards.
        # Empty = nobody (fail closed). Env BQOPT_FINOPS_APPROVERS adds comma-separated emails.
        "finops_approvers": [],
        "finops_rule_ids": ["W-01", "C1-07", "C1-05", "W-02", "C1-06"],
        # DEMO ONLY: trust the identity picked in the UI persona picker. Production: false + IAP.
        "trust_client_identity": False,
        # IAP signed-header audience; when set, the review app trusts only IAP-verified emails.
        # Cloud Run + IAP: "/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME"
        # Load balancer:   "/projects/PROJECT_NUMBER/global/backendServices/BACKEND_SERVICE_ID"
        "iap_audience": None,
    },
    "executor": {
        "enable_class1": True,
        "enable_class2": False,
        "enable_class3": False,
        "enable_reservation_changes": False,   # W-01: real CREATE RESERVATION + ASSIGNMENT (changes project billing)
        "change_window_utc": [3, 9],
        "backup_hold_days": 7,
        "max_structural_changes_per_table_per_days": 7,
    },
    "approval_ttl_days": 14,
    "evidence_stale_days": 45,
    "verify_window_days": 14,
    "regression_pct": 15.0,
    "min_family_execs": 20,
    "min_regressed_families": 3,
    "amortization_months": 12,
    "risk_weights": {1: 1.0, 2: 1.3, 3: 2.0, 4: 1.5},
    "slack_webhook": None,
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Config(dict):
    """dict with attribute access: cfg().project_id, cfg().executor['enable_class3']."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as e:  # pragma: no cover
            raise AttributeError(name) from e

    @property
    def ops(self) -> str:
        """Fully qualified ops dataset: project.optimizer_ops"""
        return f"{self['project_id']}.{self['ops_dataset']}"


def cfg(path: str | None = None, **overrides: Any) -> Config:
    path = path or os.environ.get("BQOPT_CONFIG", "config.yaml")
    user = {}
    if os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
    merged = _merge(_DEFAULTS, user)
    # Apply environment variable overrides if present
    if os.environ.get("BQOPT_JOBS_VIEW"):
        merged["jobs_view"] = os.environ["BQOPT_JOBS_VIEW"]
    if os.environ.get("BQOPT_RESERVATION_ADMIN_PROJECT"):
        merged["reservation_admin_project"] = os.environ["BQOPT_RESERVATION_ADMIN_PROJECT"]
    if "BQOPT_TARGET_DATASET" in os.environ:
        merged["target_dataset"] = os.environ["BQOPT_TARGET_DATASET"] or None
    if os.environ.get("BQOPT_LOOKBACK_DAYS"):
        try:
            merged["lookback_days"] = int(os.environ["BQOPT_LOOKBACK_DAYS"])
        except ValueError:
            pass
    if os.environ.get("BQOPT_LOCATION"):
        merged["location"] = os.environ["BQOPT_LOCATION"]
    if os.environ.get("BQOPT_EMPLOYEE_HIERARCHY_TABLE"):
        merged["employee_hierarchy_table"] = os.environ["BQOPT_EMPLOYEE_HIERARCHY_TABLE"]
    if os.environ.get("BQOPT_SERVICE_ACCOUNT_OWNER_TABLE"):
        merged["service_account_owner_table"] = os.environ["BQOPT_SERVICE_ACCOUNT_OWNER_TABLE"]
    if os.environ.get("BQOPT_BILLING_EXPORT_TABLE"):
        merged["billing_export_table"] = os.environ["BQOPT_BILLING_EXPORT_TABLE"]

    # Apply explicit keyword overrides
    for k, v in overrides.items():
        if v is not None:
            merged[k] = v
        elif k in ("target_dataset", "reservation_admin_project", "employee_hierarchy_table",
                   "service_account_owner_table", "billing_export_table"):
            merged[k] = None
    env_proj = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if env_proj:
        merged["project_id"] = env_proj
    elif "project_id" not in merged or not merged["project_id"] or merged["project_id"] == "my-analytics-project":
        try:
            import subprocess
            res = subprocess.run(["gcloud", "config", "get-value", "project"], capture_output=True, text=True, timeout=2)
            p = res.stdout.strip()
            if p and not p.startswith("Your active") and " " not in p:
                merged["project_id"] = p
        except Exception:
            pass
    if "project_id" not in merged or not merged["project_id"]:
        raise SystemExit("config.yaml (or --project / GOOGLE_CLOUD_PROJECT) must set project_id")
    # yaml reads risk_weights keys as ints already; normalise anyway
    merged["risk_weights"] = {int(k): float(v) for k, v in merged["risk_weights"].items()}
    return Config(merged)
