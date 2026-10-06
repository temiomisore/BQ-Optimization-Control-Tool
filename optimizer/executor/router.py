"""Executor routing + global guardrails (design doc §9.1, §8).

v1 ownership detection is deliberately conservative: everything routes
DIRECT_GUARDED unless config lists the dataset as IaC-managed. Wiring real
detection (terraform state, dbt manifest.json, Dataform repos) is the marked
TODO — the interface is already what the rest of the system expects.
"""
from __future__ import annotations

import datetime as dt

from .. import attribution, store
from ..config import Config


class Blocked(Exception):
    """Raised when a guardrail refuses an apply; message lands in state_history."""


def route(c: Config, target_dataset: str | None, finding: dict | None = None) -> tuple[str, str]:
    """Resolves execution route: CI_PULL_REQUEST for managed/code assets, DIRECT_GUARDED otherwise."""
    managed = set(c.get("iac_managed_datasets", []) or [])
    if target_dataset in managed:
        return "CI_PULL_REQUEST", "IAC_CODEOWNERS"
    if finding and (int(finding.get("apply_class", 1)) == 4 or
                    finding.get("proposed_change", {}).get("target_repo_pr")):
        return "CI_PULL_REQUEST", "IAC_CODEOWNERS"
    return "DIRECT_GUARDED", "UNMANAGED"


def accountable_owner(principal: str | None, sa_owners: dict | None) -> dict | None:
    """Who answers for a principal's spend: a service account (PowerBI, ETL ...) listed in
    the SA owner table maps to its human owner; a human is accountable for themself.
    Returns None for a service account nobody has claimed yet."""
    if not principal:
        return None
    owner = attribution.resolve_sa_owner(principal, sa_owners)
    if owner:
        return {"principal": str(principal), "owner_email": owner["owner_email"],
                "director_name": owner.get("director_name"),
                "director_email": owner.get("director_email"),
                "application": owner.get("application"),
                "source": "SERVICE_ACCOUNT_OWNER"}
    if attribution.is_service_account(principal):
        return None
    return {"principal": str(principal), "owner_email": str(principal), "source": "PRINCIPAL"}


def resolve_owner(c: Config, finding: dict, sa_owners: dict | None = None) -> tuple[str, str]:
    """Resolves table owner following design doc §8:
    1. Table owner label -> 2. IaC CODEOWNERS -> 3. Top writer principal -> 4. Dataset admin.
    A top writer that is a service account resolves to its accountable human owner through
    the service-account owner table (sa_owners, see optimizer/attribution.py); an unmapped
    service account is never treated as an owner.
    Returns (owner_principal, owner_source)."""
    # 1. Table label
    labels = finding.get("evidence", {}).get("labels") or finding.get("proposed_change", {}).get("labels") or {}
    if isinstance(labels, dict):
        for k in ("owner", "data_owner", "team", "contact"):
            if k in labels and labels[k]:
                return str(labels[k]), "LABEL"

    # 2. IaC CODEOWNERS (if managed by IaC or Class 4)
    route_name, _ = route(c, finding.get("target_dataset"), finding)
    if route_name == "CI_PULL_REQUEST":
        codeowner = c.get("iac_codeowner") or "data-engineering@company.com"
        return codeowner, "IAC_CODEOWNERS"

    # 3. Top writer principal from telemetry if available
    top_writer = (finding.get("evidence") or {}).get("top_writer_email")
    if top_writer:
        acct = accountable_owner(top_writer, sa_owners)
        if acct and acct["source"] == "SERVICE_ACCOUNT_OWNER":
            finding.setdefault("evidence", {})["service_account_owner"] = acct
            return acct["owner_email"], "SERVICE_ACCOUNT_OWNER"
        if acct:
            return str(top_writer), "TOP_WRITER"
        notes = finding.setdefault("risk_notes", [])
        if "TOP_WRITER_IS_UNMAPPED_SERVICE_ACCOUNT" not in notes:
            notes.append("TOP_WRITER_IS_UNMAPPED_SERVICE_ACCOUNT")

    # 4. Fallback: Dataset admin / project default
    default_owner = c.get("default_owner") or f"data-platform-admin@{c['project_id']}.iam.gserviceaccount.com"
    return default_owner, "DATASET_ADMIN"


def check_window(c: Config) -> None:
    lo, hi = c["executor"]["change_window_utc"]
    hour = dt.datetime.now(dt.timezone.utc).hour
    if not (lo <= hour < hi):
        raise Blocked(f"outside change window {lo:02d}:00-{hi:02d}:00 UTC (now {hour:02d}:xx)")


def check_rate_limit(c: Config, cs: dict) -> None:
    if int(cs["apply_class"]) != 3:
        return
    days = int(c["executor"]["max_structural_changes_per_table_per_days"])
    key = (cs["target_project"], cs["target_dataset"], cs["target_table"])
    if store.recent_structural_change(c, key, days):
        raise Blocked(f"structural change on this table within the last {days} days")
