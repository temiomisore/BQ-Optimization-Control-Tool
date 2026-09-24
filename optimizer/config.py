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
    "billing_export_table": None,
    "executor": {
        "enable_class1": True,
        "enable_class2": False,
        "enable_class3": False,
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
    if os.environ.get("BQOPT_BILLING_EXPORT_TABLE"):
        merged["billing_export_table"] = os.environ["BQOPT_BILLING_EXPORT_TABLE"]

    # Apply explicit keyword overrides
    for k, v in overrides.items():
        if v is not None:
            merged[k] = v
        elif k in ("target_dataset", "reservation_admin_project", "employee_hierarchy_table", "billing_export_table"):
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
