"""Plan compiler — pure logic (design doc §6.4).

Findings in → change sets out:
  * dedupe against open/snoozed change sets on (rule_id, target)
  * merge C1-01 clustering into a pending C3-01 rebuild on the same table
    (one rebuild, one approval — never two cards for one table surgery)
  * conflict: any new class 1/2 finding on a table with an open class-3
    change set is suppressed with a note (the rebuild supersedes it)
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

Finding = dict[str, Any]

OPEN_STATES = {"PENDING_REVIEW", "APPROVED", "SCHEDULED", "APPLYING", "APPLIED", "VERIFYING"}


def _tkey(f: Finding) -> tuple:
    return (f.get("target_project"), f.get("target_dataset"), f.get("target_table"))


def compile(findings: list[Finding],
            open_sets: Iterable[dict]) -> tuple[list[Finding], list[str]]:
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
            merged = c3[0]
            merged["rule_ids"] = ["C3-01", "C1-01"]
            merged["proposed_change"]["cluster_columns"] = (
                c1c[0]["proposed_change"].get("cluster_columns"))
            merged["gross_monthly_savings_usd"] = (
                float(merged.get("gross_monthly_savings_usd") or 0)
                + float(c1c[0].get("gross_monthly_savings_usd") or 0))
            merged["finding_summary"] = ((merged.get("finding_summary") or "")
                                         + " Clustering folded into the same rebuild.").strip()
            merged["native_rec_names"] = list(
                {*(merged.get("native_rec_names") or []), *(c1c[0].get("native_rec_names") or [])})
            merged["evidence"] = {"partition": merged.get("evidence"), "cluster": c1c[0].get("evidence")}
            out.append(merged)
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
