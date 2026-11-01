"""FinOps / governance split. Pure logic: no GCP or Flask imports.

Customer review (Sept 29, 2026): billing, commitment, reservation and
project-level controls are FinOps decisions. Analysts and business IT should
see and approve only query/table optimizations; the FinOps team and central
governance see and approve the rest. The check has to live on the server, not
just in the UI, so this module is used by:

  * review_app (what each viewer can see, and 403 on decisions), and
  * the executor (refuses to apply a FinOps card that a FinOps approver
    didn't approve: defense in depth).

Config (config.yaml):

  governance:
    finops_approvers: [finops.director@example.com, finops-team@example.com]
    engineering_approvers: [data-platform-lead@example.com]
    finops_rule_ids: [W-01, C1-07, C1-05, W-02, C1-06]
    trust_client_identity: false   # true ONLY for the sandbox demo persona picker

Env BQOPT_FINOPS_APPROVERS (comma-separated) adds approvers without editing
the file. With no approvers configured nobody can act on FinOps cards: fail
closed.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

FINOPS = "FINOPS"
ENGINEERING = "ENGINEERING"

# W-01 reservations/commitments, C1-07 legacy id for W-01, C1-05 dataset storage
# billing model, W-02 project-wide guardrail + reservation, C1-06 ALTER PROJECT.
DEFAULT_FINOPS_RULE_IDS = ("W-01", "C1-07", "C1-05", "W-02", "C1-06")
FINOPS_TARGET_DATASETS = ("PROJECT_WIDE_BILLING", "HUMAN_ADHOC_GOVERNANCE")
FINOPS_ACTIONS = ("CAPACITY_PRICING_MIGRATION", "SET_STORAGE_BILLING_MODEL",
                  "APPLY_HUMAN_COST_GUARDRAIL", "ENABLE_ADAPTIVE_OPTIMIZATION")

# Identity sources, strongest first.
SOURCE_IAP = "IAP"                          # verified by Identity-Aware Proxy
SOURCE_CLIENT = "CLIENT_SELECTED"           # persona picker (demo only)
SOURCE_SERVER = "SERVER_CONFIG"             # REVIEWER_EMAIL env on the server
SOURCE_LOCAL = "LOCAL_GCLOUD"               # local run: operator's gcloud account
SOURCE_PLACEHOLDER = "PLACEHOLDER"          # nobody identified (never grants FinOps)


class PermissionDenied(Exception):
    """Raised when a viewer may not act on a change set."""


def _gov(c: Any) -> Mapping:
    g = (c or {}).get("governance") if isinstance(c, Mapping) else None
    return g if isinstance(g, Mapping) else {}


def finops_rule_ids(c: Any) -> set[str]:
    ids = _gov(c).get("finops_rule_ids")
    if not ids:
        return set(DEFAULT_FINOPS_RULE_IDS)
    return {str(x).strip().upper() for x in ids if str(x).strip()}


def finops_approvers(c: Any) -> set[str]:
    out = {str(x).strip().lower() for x in (_gov(c).get("finops_approvers") or []) if str(x).strip()}
    env = os.environ.get("BQOPT_FINOPS_APPROVERS", "")
    out |= {x.strip().lower() for x in env.split(",") if x.strip()}
    return out


def settings_admins(c: Any) -> set[str]:
    out = {str(x).strip().lower() for x in (_gov(c).get("settings_admins") or []) if str(x).strip()}
    env = os.environ.get("BQOPT_SETTINGS_ADMINS", "")
    out |= {x.strip().lower() for x in env.split(",") if x.strip()}
    return out


def engineering_approvers(c: Any) -> set[str]:
    out = {str(x).strip().lower() for x in (_gov(c).get("engineering_approvers") or []) if str(x).strip()}
    env = os.environ.get("BQOPT_ENGINEERING_APPROVERS", "")
    out |= {x.strip().lower() for x in env.split(",") if x.strip()}
    return out


def allow_demo_mode_in_env() -> bool:
    """Cloud Run production guard: ignore demo_mode / trust_client_identity when running on
    Cloud Run (K_SERVICE or CLOUD_RUN_JOB set) unless BQOPT_ALLOW_DEMO_MODE=true is set."""
    if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"):
        return os.environ.get("BQOPT_ALLOW_DEMO_MODE", "").strip().lower() in ("1", "true", "yes")
    return True


def trust_client_identity(c: Any) -> bool:
    """Demo-only: honour the persona picker's principal. Production deployments
    keep this False and put the app behind IAP."""
    return bool(_gov(c).get("trust_client_identity", False)) and allow_demo_mode_in_env()


def _change(cs: Mapping) -> Mapping:
    for k in ("proposed", "proposed_change"):
        v = cs.get(k)
        if isinstance(v, Mapping):
            return v
    raw = cs.get("proposed_change_json")
    if isinstance(raw, str) and raw:
        try:
            import json
            v = json.loads(raw, strict=False)
            return v if isinstance(v, Mapping) else {}
        except ValueError:
            return {}
    return {}


def is_finops_card(cs: Mapping, c: Any = None) -> bool:
    """Billing, commitment, reservation or project-level control?"""
    rule_ids = cs.get("rule_ids") or ([cs["rule_id"]] if cs.get("rule_id") else [])
    if any(str(r).upper() in finops_rule_ids(c) for r in rule_ids):
        return True
    ds = cs.get("target_dataset")
    if ds in FINOPS_TARGET_DATASETS:
        return True
    if not ds and not cs.get("target_table") and cs.get("target_project"):
        return True                      # project-level change (no dataset, no table)
    return str(_change(cs).get("action") or "").upper() in FINOPS_ACTIONS


def scope(cs: Mapping, c: Any = None) -> str:
    return FINOPS if is_finops_card(cs, c) else ENGINEERING


def is_trusted_identity(identity: Mapping | None, c: Any) -> bool:
    """Return True only when `identity` represents a verified, non-service-account human caller."""
    identity = identity or {}
    email = str(identity.get("email") or "").strip().lower()
    if not email or "@" not in email or email.endswith(".gserviceaccount.com"):
        return False
    src = identity.get("source")
    demo = trust_client_identity(c)
    if src in (SOURCE_PLACEHOLDER, SOURCE_CLIENT, None) and not demo:
        return False
    if os.environ.get("K_SERVICE") and not demo and src != SOURCE_IAP:
        return False
    return True


def requires_two_person_approval(cs: Mapping) -> bool:
    try:
        if int(cs.get("apply_class") or 0) == 3:
            return True
    except (TypeError, ValueError):
        pass
    return "REQUIRES_TWO_PERSON_APPROVAL" in (cs.get("risk_notes") or [])


def is_finops_viewer(identity: Mapping | None, c: Any) -> bool:
    identity = identity or {}
    email = str(identity.get("email") or "").strip().lower()
    if not email or email not in finops_approvers(c):
        return False
    return is_trusted_identity(identity, c)


def _human_owner(raw: Any) -> str | None:
    s = str(raw or "").strip().lower()
    if not s or "@" not in s or s.endswith(".gserviceaccount.com") or s == "data-engineering@company.com":
        return None
    return s


def check_can_decide(
    cs: Mapping,
    identity: Mapping | None,
    c: Any,
    *,
    editors: Iterable[str] | None = None,
    action: str = "approve",
) -> None:
    """Enforce server-side RBAC + two-person distinct-principal rules on FinOps and Engineering cards."""
    identity = identity or {}
    who = str(identity.get("email") or "").strip() or "unknown identity"
    email = who.lower()

    if is_finops_card(cs, c):
        if not is_finops_viewer(identity, c):
            if not finops_approvers(c):
                raise PermissionDenied(
                    "FinOps approval required, and no FinOps approvers are configured "
                    "(set governance.finops_approvers in config.yaml or BQOPT_FINOPS_APPROVERS).")
            raise PermissionDenied(
                f"{who} is not a FinOps approver. Billing, commitment, reservation and "
                f"project-level changes can only be approved by the FinOps / governance team.")
        if action == "approve" and requires_two_person_approval(cs):
            existing = {
                str((a or {}).get("principal") or "").strip().lower()
                for a in (cs.get("approvals") or [])
                if str((a or {}).get("principal") or "").strip()
            }
            if email in existing:
                raise PermissionDenied(
                    "Two-person approval requires two distinct FinOps approvers; "
                    f"{who} has already approved this change set."
                )
        return

    # Engineering cards (Classes 1-4): fail closed for untrusted/placeholder/SA identities
    # and for corporate domain viewers who are not an authorized admin, approver, editor, or card owner.
    if not is_trusted_identity(identity, c):
        raise PermissionDenied(
            "Verified reviewer identity required to act on optimization cards."
        )

    admins = settings_admins(c)
    eng_approvers = engineering_approvers(c)
    fin_approvers = finops_approvers(c)
    eds = {str(e).strip().lower() for e in (editors or []) if str(e).strip()}
    card_owner = _human_owner(cs.get("owner_principal"))
    iac_owner = (
        _human_owner(c.get("iac_codeowner"))
        if isinstance(c, Mapping) and cs.get("execution_route") == "CI_PULL_REQUEST"
        else None
    )

    platform_tier = admins | eng_approvers | fin_approvers
    if action == "rollback":
        allowed = platform_tier | ({card_owner} if card_owner else set())
        if email not in allowed:
            raise PermissionDenied(
                f"{who} is not authorized to roll back this change set. "
                "Only Settings Admins, Engineering/FinOps Approvers, or the table owner may trigger rollbacks."
            )
        return

    allowed = platform_tier | eds | ({card_owner} if card_owner else set()) | ({iac_owner} if iac_owner else set())
    if email not in allowed:
        raise PermissionDenied(
            f"{who} has read-only viewer access and cannot modify Engineering optimization cards. "
            "Ask a Settings Admin to add you as an editor/approver or route to the card owner."
        )

    if action == "approve" and requires_two_person_approval(cs):
        existing = {
            str((a or {}).get("principal") or "").strip().lower()
            for a in (cs.get("approvals") or [])
            if str((a or {}).get("principal") or "").strip()
        }
        if email in existing:
            raise PermissionDenied(
                "Two-person approval requires two distinct principals; "
                f"{who} has already recorded the first approval."
            )
        if existing and platform_tier and email not in platform_tier:
            raise PermissionDenied(
                "Class 3 second sign-off (platform_approver) must come from a Settings Admin, "
                "Engineering Approver, or FinOps Approver."
            )


def executor_block_reason(cs: Mapping, c: Any) -> str | None:
    """Defense in depth for the executor: FinOps cards require FinOps approvers, and
    Class 3 two-person change sets require at least two distinct human principals."""
    principals = [
        str((a or {}).get("principal") or "").strip().lower()
        for a in (cs.get("approvals") or [])
        if str((a or {}).get("principal") or "").strip()
    ]
    if is_finops_card(cs, c):
        approvers = finops_approvers(c)
        if not approvers:
            return "FinOps card blocked: no governance.finops_approvers configured"
        if not principals:
            return "FinOps card blocked: no recorded approvals"
        outsiders = sorted({p for p in principals if p not in approvers})
        if outsiders:
            return ("FinOps card blocked: approved by non-FinOps principal(s) "
                    + ", ".join(outsiders) + "; a FinOps approver must re-approve")
    if requires_two_person_approval(cs):
        distinct = {p for p in principals if "@" in p and not p.endswith(".gserviceaccount.com")}
        if len(distinct) < 2:
            return (
                "Two-person approval blocked: Class 3 changes require 2 distinct human principals "
                f"(found {len(distinct)})"
            )
    return None


def split_items(items: Iterable[dict], viewer_is_finops: bool, c: Any) -> tuple[list[dict], int]:
    """Tag every item with governance_scope; drop FinOps items for non-FinOps
    viewers. Returns (visible_items, hidden_count)."""
    visible: list[dict] = []
    hidden = 0
    for it in items:
        it["governance_scope"] = scope(it, c)
        if it["governance_scope"] == FINOPS and not viewer_is_finops:
            hidden += 1
            continue
        visible.append(it)
    return visible, hidden
