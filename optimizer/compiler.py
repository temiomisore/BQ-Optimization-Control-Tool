"""Plan compiler — pure logic (design doc §6.4).

Findings in → change sets out:
  * dedupe against open/snoozed change sets on (rule_id, target)
  * merge C1-01 clustering into a pending C3-01 rebuild on the same table
    (one rebuild, one approval — never two cards for one table surgery);
    their savings compound against the table's read spend instead of adding up
  * conflict: any new class 1/2 finding on a table with an open class-3
    change set is suppressed with a note (the rebuild supersedes it)
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from . import pricing

Finding = dict[str, Any]

OPEN_STATES = {"PENDING_REVIEW", "APPROVED", "SCHEDULED", "APPLYING", "APPLIED", "VERIFYING"}


def _tkey(f: Finding) -> tuple:
    return (f.get("target_project"), f.get("target_dataset"), f.get("target_table"))


def _merge_savings(c3: Finding, c1: Finding) -> tuple[float, dict, list[str]]:
    """Combined gross for partition + clustering on ONE table.

    Both claim a share of the same table's read spend, so (when that spend is known)
    the reductions compound — 50% + 50% = 75% — and can never exceed the spend.
    Only when the spend is unknown are they summed, and the card says so."""
    g3 = float(c3.get("gross_monthly_savings_usd") or 0)
    g1 = float(c1.get("gross_monthly_savings_usd") or 0)
    spends = [s for s in (pricing.pool_of(c3)[1], pricing.pool_of(c1)[1]) if s and s > 0]
    key = pricing.pool_of(c3)[0] or pricing.pool_of(c1)[0]
    floors = any(((f.get("evidence") or {}).get("savings_math") or {}).get("demo_floor_applied")
                 for f in (c3, c1))
    notes: list[str] = []
    if spends:
        spend = max(spends)
        gross = pricing.combine_savings([g3, g1], spend)
        formula = (f"partition ${g3:,.2f} + clustering ${g1:,.2f} compounded against the table's "
                   f"${spend:,.2f}/mo read spend: ${spend:,.2f} x (1 - (1 - {min(g3 / spend, 1):.0%}) "
                   f"x (1 - {min(g1 / spend, 1):.0%})) = ${gross:,.2f}/mo")
    else:
        spend = None
        gross = round(g3 + g1, 2)
        formula = (f"partition ${g3:,.2f} + clustering ${g1:,.2f} = ${gross:,.2f}/mo (summed: the table's "
                   f"read spend is unknown, so overlap could not be removed; treat as an upper bound)")
        notes.append("MERGED_SAVINGS_SUMMED_SPEND_UNKNOWN")
    math = {"method": "MERGED_PARTITION_AND_CLUSTER_COMPOUNDED" if spends else "MERGED_PARTITION_AND_CLUSTER_SUMMED",
            "formula": formula, "demo_floor_applied": floors, "spend_kind": pricing.COMPUTE,
            "components": [{"rule_id": "C3-01", "gross_usd": round(g3, 2)},
                           {"rule_id": "C1-01", "gross_usd": round(g1, 2)}]}
    if key:
        math["pool_key"] = key
    if spend:
        math["pool_spend_usd"] = round(spend, 2)
    return gross, math, notes


def _merge_pair(c3: Finding, c1: Finding, cfg: dict | None = None) -> Finding:
    """Fold a C1-01 clustering finding into the C3-01 rebuild on the same table
    (mutates and returns the C3-01 finding)."""
    merged = c3
    g3_old = float(merged.get("gross_monthly_savings_usd") or 0)
    gross_new, merged_math, merge_notes = _merge_savings(merged, c1)
    merged["rule_ids"] = ["C3-01", "C1-01"]
    merged["proposed_change"]["cluster_columns"] = (
        c1["proposed_change"].get("cluster_columns"))
    merged["gross_monthly_savings_usd"] = gross_new
    if "net_monthly_value_usd" in merged:
        # Already scored: keep net value and score consistent with the new gross
        # (same formula as scoring.score; C3-01 is class 3).
        net_new = round(float(merged.get("net_monthly_value_usd") or 0) + (gross_new - g3_old), 2)
        risk_w = float(((cfg or {}).get("risk_weights") or {}).get(3, 2.0))
        merged["net_monthly_value_usd"] = net_new
        merged["score"] = round(max(net_new, 0.0) * float(merged.get("confidence") or 0) / risk_w, 2)
    merged["finding_summary"] = ((merged.get("finding_summary") or "")
                                 + f" Clustering folded into the same rebuild "
                                   f"(combined ~${gross_new:,.2f}/mo).").strip()
    merged["native_rec_names"] = list(
        {*(merged.get("native_rec_names") or []), *(c1.get("native_rec_names") or [])})
    merged["risk_notes"] = list(dict.fromkeys(
        [*(merged.get("risk_notes") or []), *(c1.get("risk_notes") or []), *merge_notes]))
    merged["evidence"] = {"partition": merged.get("evidence"), "cluster": c1.get("evidence"),
                          "savings_math": merged_math}
    return merged


def pending_refreshes(findings: list[Finding], open_sets: Iterable[dict],
                      cfg: dict | None = None) -> list[tuple[str, Finding]]:
    """(change_set_id, finding) for open PENDING_REVIEW cards that nobody has approved yet
    and that this run detected again — so they are re-priced in place with the latest
    telemetry instead of keeping a stale number (compile() would otherwise just drop the
    duplicate finding). Partition + cluster findings are merged first, as compile() does.
    Works on copies: the findings passed in are not modified."""
    import copy
    by_target: dict[tuple, list[Finding]] = {}
    for f in findings:
        by_target.setdefault(_tkey(f), []).append(copy.deepcopy(f))
    candidates: list[Finding] = []
    for group in by_target.values():
        c3 = [f for f in group if f["rule_id"] == "C3-01"]
        c1c = [f for f in group if f["rule_id"] == "C1-01"]
        rest = [f for f in group if f["rule_id"] not in ("C3-01", "C1-01")]
        if c3 and c1c:
            candidates.append(_merge_pair(c3[0], c1c[0], cfg))
        else:
            candidates += c3 + c1c
        candidates += rest
    open_list = [cs for cs in open_sets
                 if cs.get("state") == "PENDING_REVIEW" and not cs.get("n_approvals")]
    out: list[tuple[str, Finding]] = []
    used: set[str] = set()
    for f in candidates:
        rids = set(f.get("rule_ids") or [f["rule_id"]])
        for cs in open_list:
            k = (cs.get("target_project"), cs.get("target_dataset"), cs.get("target_table"))
            if (cs["change_set_id"] not in used and k == _tkey(f)
                    and set(cs.get("rule_ids") or []) == rids):
                out.append((cs["change_set_id"], f))
                used.add(cs["change_set_id"])
                break
    return out


# Engines whose output varies run to run: a card they produced that is missing from one
# run is not evidence that the finding went away.
NON_DETERMINISTIC_SOURCES = ("GEMINI_AI_JUDGE", "GOOGLE_ANTIPATTERN_AST")


def stale_legacy_cards(findings: list[Finding], legacy_pending: Iterable[dict]) -> list[dict]:
    """Unapproved cards priced by the pre-billing-aware engine (no savings_math) that this
    run did not detect again. Their old number cannot be re-priced, so the caller retires
    them; if the finding still applies it is re-created with corrected math next run."""
    seen = {(r, _tkey(f)) for f in findings for r in (f.get("rule_ids") or [f["rule_id"]])}
    out: list[dict] = []
    for cs in legacy_pending:
        if str(cs.get("source") or "") in NON_DETERMINISTIC_SOURCES:
            continue
        k = (cs.get("target_project"), cs.get("target_dataset"), cs.get("target_table"))
        if not any((r, k) in seen for r in (cs.get("rule_ids") or [])):
            out.append(cs)
    return out


def legacy_to_reprice(findings: list[Finding], legacy_pending: Iterable[dict]) -> list[dict]:
    """The other half of stale_legacy_cards: legacy cards from the non-deterministic
    engines (Gemini judge, AST analyzer) that this run did not detect again. Not seeing
    them is no evidence they went away, so the caller re-prices them in place from the
    query family's current cost instead of retiring them."""
    seen = {(r, _tkey(f)) for f in findings for r in (f.get("rule_ids") or [f["rule_id"]])}
    out: list[dict] = []
    for cs in legacy_pending:
        if str(cs.get("source") or "") not in NON_DETERMINISTIC_SOURCES:
            continue
        k = (cs.get("target_project"), cs.get("target_dataset"), cs.get("target_table"))
        if not any((r, k) in seen for r in (cs.get("rule_ids") or [])):
            out.append(cs)
    return out


def compile(findings: list[Finding],
            open_sets: Iterable[dict], cfg: dict | None = None) -> tuple[list[Finding], list[str]]:
    """Returns (change_sets_to_insert, suppressed_notes)."""
    open_by_target: dict[tuple, set[str]] = {}
    open_rules: set[tuple] = set()
    open_class3: set[tuple] = set()
    for cs in open_sets:
        k = (cs.get("target_project"), cs.get("target_dataset"), cs.get("target_table"))
        open_by_target.setdefault(k, set()).update(cs.get("rule_ids") or [])
        for r in cs.get("rule_ids") or []:
            open_rules.add((r, k))
        if int(cs.get("apply_class") or 0) == 3 and cs.get("state") in OPEN_STATES:
            open_class3.add(k)

    notes: list[str] = []
    # 0. suppress internal optimizer backup/staging tables (__bqopt_*)
    valid_findings: list[Finding] = []
    for f in findings:
        tbl = str(f.get("target_table") or "")
        if "__bqopt_" in tbl:
            notes.append(f"SUPPRESSED_INTERNAL_TABLE:{f['rule_id']}:{tbl}")
            continue
        valid_findings.append(f)

    # 1. dedupe
    fresh: list[Finding] = []
    for f in valid_findings:
        if (f["rule_id"], _tkey(f)) in open_rules:
            continue
        fresh.append(f)

    # 2. merge partition + cluster on the same table into one class-3 set
    by_target: dict[tuple, list[Finding]] = {}
    for f in fresh:
        by_target.setdefault(_tkey(f), []).append(f)

    out: list[Finding] = []
    for key, group in by_target.items():
        c3 = [f for f in group if f["rule_id"] == "C3-01"]
        c1c = [f for f in group if f["rule_id"] == "C1-01"]
        rest = [f for f in group if f not in c3 and f not in c1c]
        if c3 and c1c:
            out.append(_merge_pair(c3[0], c1c[0], cfg))
            group = rest
        else:
            group = c3 + c1c + rest

        # 3. conflict suppression: open class-3 on this table swallows new class 1/2
        for f in group:
            if key in open_class3 and int(f["apply_class"]) in (1, 2):
                notes.append(f"SUPERSEDED_BY_OPEN_REBUILD:{f['rule_id']}:{key}")
                continue
            out.append(f)

    # finalize
    for f in out:
        f.setdefault("rule_ids", [f["rule_id"]])
        f["change_set_id"] = str(uuid.uuid4())
    return out, notes
