"""Billing-aware pricing helpers. Pure logic: no GCP imports.

Why this module exists (customer review, Sept 29, 2026): a savings estimate
is only credible if it matches how each job is actually billed.

  * On-demand jobs pay per TiB scanned  -> bytes x $/TiB is real money.
  * Reservation (Editions) jobs pay for slot time -> their total_bytes_billed is
    informational only; their cost is slot-hours x the edition's slot rate.

Pricing every job at $/TiB (what the older rules did) overstates savings on
projects that run on slots, which is how a ~$15k/mo claim could check out at
roughly a tenth of that against the invoice.

Conventions
  * `c` may be a Config or a plain dict (unit tests pass dicts).
  * `prices` is the one-row optimizer_ops.v_config dict (values may be Decimal).
  * Every estimator returns the formula it used, so the UI can show its work.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

TIB = 1024 ** 4
GIB = 1024 ** 3
MS_PER_HOUR = 3_600_000.0

# US multi-region list prices; only used when v_config is missing a key.
DEFAULT_PRICES: dict[str, float] = {
    "on_demand_usd_per_tib": 6.25,
    "slot_hour_usd_standard": 0.04,
    "slot_hour_usd_enterprise": 0.06,
    "slot_hour_usd_enterprise_plus": 0.10,
    "slot_hour_usd_enterprise_1yr": 0.048,
    "p_log_active": 0.02,
    "p_log_lt": 0.01,
    "p_phy_active": 0.04,
    "p_phy_lt": 0.02,
}

# Older code paths used different spellings for the same v_config columns.
_ALIASES: dict[str, tuple[str, ...]] = {
    "p_log_active": ("storage_logical_active_usd_per_gib", "active_logical_gib_usd"),
    "p_log_lt": ("storage_logical_lt_usd_per_gib", "long_term_logical_gib_usd"),
    "p_phy_active": ("storage_physical_active_usd_per_gib", "active_physical_gib_usd"),
    "p_phy_lt": ("storage_physical_lt_usd_per_gib", "long_term_physical_gib_usd"),
}

ON_DEMAND = "ON_DEMAND"
RESERVATION = "RESERVATION"

# Which bill a card's savings come out of (see spend_kind / deoverlap_total).
COMPUTE = "compute"
STORAGE = "storage"
# Rules priced off the storage bill; used for cards whose savings_math has no spend_kind.
_STORAGE_RULES = frozenset({"C1-03", "C1-04", "C1-05", "C3-05"})


# ---------------------------------------------------------------------------
# Config / price lookups
# ---------------------------------------------------------------------------

def _cfg_get(c: Any, *path: str, default: Any = None) -> Any:
    cur: Any = c or {}
    for p in path:
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def rate(prices: Mapping | None, key: str, default: float | None = None) -> float:
    """Price lookup tolerant of legacy key spellings and Decimal values."""
    prices = prices or {}
    for k in (key, *_ALIASES.get(key, ())):
        v = prices.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    if default is not None:
        return float(default)
    return float(DEFAULT_PRICES.get(key, 0.0))


def edition_slot_rate(prices: Mapping | None, edition: str | None) -> float:
    """Pay-as-you-go slot-hour rate for a reservation edition (default ENTERPRISE)."""
    e = (edition or "ENTERPRISE").upper().replace("-", "_").replace(" ", "_")
    if e == "STANDARD":
        return rate(prices, "slot_hour_usd_standard")
    if e == "ENTERPRISE_PLUS":
        return rate(prices, "slot_hour_usd_enterprise_plus")
    return rate(prices, "slot_hour_usd_enterprise")


def realization(c: Any) -> float:
    """Share of a slot-time reduction that becomes cash (config
    pricing.reservation_savings_realization, 0..1, default 1.0).

    1.0 means every slot-hour saved is an autoscale slot-hour you stop paying
    for. Lower it when most capacity is committed baseline: freed baseline
    slots only create headroom until the reservation is downsized."""
    v = _cfg_get(c, "pricing", "reservation_savings_realization", default=1.0)
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 1.0


def demo_mode(c: Any) -> bool:
    """True only for the sandbox demo (config `demo_mode: true`). In demo mode
    rules may apply labelled synthetic minimums so a tiny dataset still shows
    every card type. Never enable this for a real customer."""
    return bool(_cfg_get(c, "demo_mode", default=False))


def demo_floor(c: Any, raw: float, floor: float) -> tuple[float, bool]:
    """(value, floor_applied). Floors apply ONLY in demo mode."""
    raw_f = float(raw or 0.0)
    if demo_mode(c) and raw_f < float(floor):
        return float(floor), True
    return raw_f, False


# ---------------------------------------------------------------------------
# Job / pool costs
# ---------------------------------------------------------------------------

def job_cost(bytes_billed: float | None, slot_ms: float | None, reservation_id: str | None,
             prices: Mapping | None, slot_rate: float | None = None) -> dict:
    """Cost of one job under the billing model it actually ran on."""
    if reservation_id:
        r = float(slot_rate) if slot_rate is not None else rate(prices, "slot_hour_usd_enterprise")
        return {"billing_mode": RESERVATION, "rate": r, "unit": "slot-hour",
                "usd": float(slot_ms or 0) / MS_PER_HOUR * r}
    r = rate(prices, "on_demand_usd_per_tib")
    return {"billing_mode": ON_DEMAND, "rate": r, "unit": "TiB",
            "usd": float(bytes_billed or 0) / TIB * r}


def billing_mix(od_usd: float | None, resv_usd: float | None) -> str:
    od, rv = float(od_usd or 0), float(resv_usd or 0)
    if od <= 0 and rv <= 0:
        return "NO_ATTRIBUTED_SPEND"
    if rv <= 0:
        return "100% on-demand"
    if od <= 0:
        return "100% reservation (slots)"
    return f"{od / (od + rv):.0%} on-demand / {rv / (od + rv):.0%} reservation"


def _pct(x: float) -> str:
    return f"{x:.0%}" if abs(x * 100 - round(x * 100)) < 1e-9 else f"{x:.1%}"


def scan_savings(od_usd: float | None, resv_usd: float | None, reduction: float,
                 c: Any = None) -> dict:
    """Savings when a change cuts the work done by a set of jobs by `reduction`.

    od_usd   : monthly cost of the affected ON-DEMAND jobs (bytes x $/TiB)
    resv_usd : monthly cost of the affected RESERVATION jobs (slot-hours x edition rate)

    On-demand: fewer bytes scanned is cash. Reservation: less slot time is cash
    only for autoscale slots, so that part is scaled by `realization(c)`."""
    red = max(0.0, min(1.0, float(reduction or 0)))
    od = max(0.0, float(od_usd or 0))
    rv = max(0.0, float(resv_usd or 0))
    real = realization(c)
    od_save = od * red
    rv_save = rv * red * real
    gross = od_save + rv_save
    parts = []
    if od > 0:
        parts.append(f"on-demand ${od:,.2f}/mo x {_pct(red)}")
    if rv > 0:
        parts.append(f"reservation ${rv:,.2f}/mo x {_pct(red)} x {_pct(real)} realization")
    formula = (" + ".join(parts) + f" = ${gross:,.2f}/mo") if parts else "no attributed spend = $0.00/mo"
    return {
        "gross": round(gross, 2),
        "on_demand_part_usd": round(od_save, 2),
        "reservation_part_usd": round(rv_save, 2),
        "reduction": round(red, 4),
        "realization": real,
        "attributed_monthly_usd": round(od + rv, 2),
        "on_demand_monthly_usd": round(od, 2),
        "reservation_monthly_usd": round(rv, 2),
        "billing_mix": billing_mix(od, rv),
        "formula": formula,
    }


def table_shares(sizes: Iterable[Any]) -> list[float]:
    """How a multi-table job's cost is split across the tables it reads:
    in proportion to table size when every size is known and > 0, otherwise an
    equal 1/N split. Mirrors the SQL in optimizer_ops.v_table_read_write_90d.
    Shares always sum to 1, so a join is never counted N times."""
    vals: list[float | None] = []
    for s in sizes:
        try:
            vals.append(float(s))
        except (TypeError, ValueError):
            vals.append(None)
    n = len(vals)
    if n == 0:
        return []
    if all(v is not None and v > 0 for v in vals):
        tot = sum(v for v in vals if v is not None)
        return [float(v) / tot for v in vals if v is not None]
    return [1.0 / n] * n


# ---------------------------------------------------------------------------
# Combining claims against the same spend
# ---------------------------------------------------------------------------

def combine_reductions(reductions: Iterable[float]) -> float:
    """Independent reductions compound: 1 - prod(1 - r).
    50% + 50% = 75% (not 100%); never more than 100%."""
    keep = 1.0
    for r in reductions:
        keep *= 1.0 - max(0.0, min(1.0, float(r or 0)))
    return 1.0 - keep


def combine_savings(savings: Iterable[float], spend: float | None = None) -> float:
    """Combine several savings claims against ONE pool of spend.

    Known spend: turn each claim into a reduction fraction, compound them and
    cap at the spend. Unknown spend: plain sum (callers flag that case)."""
    vals = [max(0.0, float(s or 0)) for s in savings]
    if spend is None or float(spend) <= 0:
        return round(sum(vals), 2)
    sp = float(spend)
    return round(sp * combine_reductions(min(v / sp, 1.0) for v in vals), 2)


def _evidence(item: Mapping) -> Mapping:
    """Parsed evidence of a card: the `evidence` dict, or the raw `evidence_json` column."""
    ev = item.get("evidence")
    if isinstance(ev, Mapping):
        return ev
    raw = item.get("evidence_json")
    if isinstance(raw, str) and raw:
        import json
        try:
            ev = json.loads(raw, strict=False)
        except ValueError:
            return {}
        return ev if isinstance(ev, Mapping) else {}
    return {}


def pool_of(item: Mapping) -> tuple[str | None, float | None]:
    """(pool_key, pool_spend_usd) recorded by the rules engine in
    evidence.savings_math; (None, None) when the card has no shared pool."""
    sm = _evidence(item).get("savings_math")
    if not isinstance(sm, Mapping):
        return None, None
    key = sm.get("pool_key")
    spend = sm.get("pool_spend_usd")
    try:
        spend_f = float(spend) if spend is not None else None
    except (TypeError, ValueError):
        spend_f = None
    return (str(key) if key else None), spend_f


def spend_kind(item: Mapping) -> str | None:
    """Which bill a card's savings come out of: COMPUTE (query cost) or STORAGE.
    Cards priced before savings_math existed are classified by rule id, so even they
    fall under the actual-spend cap. None only for an item with no rule and no math."""
    sm = _evidence(item).get("savings_math")
    if isinstance(sm, Mapping):
        k = sm.get("spend_kind")
        if k in (COMPUTE, STORAGE):
            return str(k)
        key = str(sm.get("pool_key") or "")
        if key.startswith("storage:"):
            return STORAGE
        if key.startswith(("table:", "query:", "billing:")):
            return COMPUTE
    rules = item.get("rule_ids")
    if rules is None:
        rules = [item["rule_id"]] if item.get("rule_id") else []
    elif isinstance(rules, str):
        rules = [rules]
    if rules and all(str(r) in _STORAGE_RULES for r in rules):
        return STORAGE
    # Every other rule estimates a cut in query (compute) cost.
    return COMPUTE if (isinstance(sm, Mapping) or rules) else None


def _effective_pool(item: Mapping) -> tuple[str | None, float | None]:
    """pool_of(), except that a query-family card whose query reads exactly one table
    joins that table's pool: a SQL rewrite and, say, clustering that table cut the
    same scans, so their savings compound instead of adding up."""
    key, spend = pool_of(item)
    if key and key.startswith("query:"):
        sm = _evidence(item).get("savings_math")
        tables = sm.get("tables") if isinstance(sm, Mapping) else None
        if isinstance(tables, (list, tuple)):
            distinct = sorted({str(t) for t in tables if t})
            if len(distinct) == 1:
                return f"table:{distinct[0]}", spend
    return key, spend


def _positive(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def deoverlap_total(items: Iterable[Mapping], value_key: str = "net_monthly_value_usd",
                    ceilings: Mapping[str, Any] | None = None) -> dict:
    """Headline total that does not double count, and cannot exceed the bill.

    1. Cards in the same pool (same table, same query family, same dataset's storage,
       same project's on-demand bill) compound against that pool's spend
       (50% + 50% = 75%) instead of being added up.
    2. A query-family card whose query reads exactly one table joins that table's pool.
    3. Billing-model cards (pool 'billing:<project>': Editions sizing, byte caps) only
       count against the part of the bill left after the table and query fixes.
    4. ceilings = {'compute': $/mo, 'storage': $/mo} of ACTUAL spend (e.g. the last 30
       days): each kind's total is capped at what is really spent on it.

    Still an upper bound (overlap between multi-table queries and their tables is not
    modelled), but with ceilings it can never claim more than the customer spends."""
    ceilings = ceilings or {}
    compute_ceiling = _positive(ceilings.get(COMPUTE))
    storage_ceiling = _positive(ceilings.get(STORAGE))
    naive = 0.0
    by_kind: dict[str | None, float] = {}
    fixes_by_project: dict[str, float] = {}
    pools: dict[str, dict[str, Any]] = {}
    for it in items:
        try:
            v = max(0.0, float(it.get(value_key) or 0.0))
        except (TypeError, ValueError):
            v = 0.0
        naive += v
        kind = spend_kind(it)
        project = str(it.get("target_project") or "")
        key, spend = _effective_pool(it)
        if not key:
            by_kind[kind] = by_kind.get(kind, 0.0) + v
            if kind == COMPUTE:
                fixes_by_project[project] = fixes_by_project.get(project, 0.0) + v
            continue
        p = pools.setdefault(key, {"values": [], "spend": None, "kind": kind, "project": project})
        p["values"].append(v)
        if spend is not None and spend > 0:
            p["spend"] = max(p["spend"] or 0.0, spend)

    overlapping = 0
    billing: list[dict[str, Any]] = []
    for key, p in pools.items():
        if len(p["values"]) > 1:
            overlapping += len(p["values"])
        combined = combine_savings(p["values"], p["spend"])
        if key.startswith("billing:"):
            billing.append({"project": p["project"] or key.split(":", 1)[1],
                            "savings": combined, "spend": p["spend"]})
            continue
        by_kind[p["kind"]] = by_kind.get(p["kind"], 0.0) + combined
        if p["kind"] == COMPUTE:
            fixes_by_project[p["project"]] = fixes_by_project.get(p["project"], 0.0) + combined

    # Billing-model changes act on whatever spend the table / query fixes leave behind.
    compute_fixes = by_kind.get(COMPUTE, 0.0)
    billing_claimed = sum(b["savings"] for b in billing)
    billing_counted = 0.0
    for b in billing:
        if compute_ceiling:
            left = max(0.0, 1.0 - compute_fixes / compute_ceiling)
        elif b["spend"]:
            left = max(0.0, 1.0 - fixes_by_project.get(b["project"], 0.0) / b["spend"])
        else:
            left = 1.0
        billing_counted += b["savings"] * left
    compute_total = compute_fixes + billing_counted

    capped: list[str] = []
    if compute_ceiling is not None and compute_total > compute_ceiling:
        compute_total = compute_ceiling
        capped.append(COMPUTE)
    if billing or COMPUTE in by_kind:
        by_kind[COMPUTE] = compute_total
    if storage_ceiling is not None and by_kind.get(STORAGE, 0.0) > storage_ceiling:
        by_kind[STORAGE] = storage_ceiling
        capped.append(STORAGE)

    total = round(sum(by_kind.values()), 2)
    return {
        "total": total,
        "naive_total": round(naive, 2),
        "overlap_removed": round(max(naive - total, 0.0), 2),
        "pools": len(pools),
        "overlapping_cards": overlapping,
        "compute_usd": round(by_kind.get(COMPUTE, 0.0), 2),
        "storage_usd": round(by_kind.get(STORAGE, 0.0), 2),
        "unclassified_usd": round(by_kind.get(None, 0.0), 2),
        "billing_cards_claimed_usd": round(billing_claimed, 2),
        "billing_cards_counted_usd": round(billing_counted, 2),
        "compute_ceiling_usd": round(compute_ceiling, 2) if compute_ceiling else None,
        "ceiling_applied": capped,
    }
