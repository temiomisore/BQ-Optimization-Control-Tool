"""Tests for the Sept 29, 2026 customer review fixes (no GCP access needed):

  #3 billing-aware savings math   -> optimizer.pricing, rules mappers, compiler, verifier
  #1 FinOps view + permission     -> optimizer.governance, review_app 403s, executor guard
  #2 service-account owner table  -> optimizer.attribution, executor.router
"""
import copy
import json
import os
import unittest
from unittest import mock

from optimizer import attribution, compiler, governance, pricing, rules, verifier
from optimizer.executor import router

TIB = 1024 ** 4
DEMO = {"demo_mode": True}
REAL = {"demo_mode": False}


def card(rule="C1-01", target=("p", "d", "t"), net=100.0, pool=None, spend=None, **kw):
    ev = {}
    if pool:
        ev["savings_math"] = {"pool_key": pool, "pool_spend_usd": spend}
    c = {"rule_ids": [rule], "target_project": target[0], "target_dataset": target[1],
         "target_table": target[2], "net_monthly_value_usd": net, "evidence": ev}
    c.update(kw)
    return c


# ---------------------------------------------------------------------------
# Fix #3: savings math
# ---------------------------------------------------------------------------

class TestPricing(unittest.TestCase):
    def test_reservation_job_priced_by_slot_time_not_bytes(self):
        # 10 TiB scanned in 1 slot-hour: on-demand pays $62.50, a reservation pays $0.06.
        od = pricing.job_cost(10 * TIB, 3_600_000, None, {})
        rv = pricing.job_cost(10 * TIB, 3_600_000, "projects/x/reservations/r", {})
        self.assertAlmostEqual(od["usd"], 62.5)
        self.assertAlmostEqual(rv["usd"], 0.06)
        self.assertEqual(rv["billing_mode"], pricing.RESERVATION)

    def test_scan_savings_on_demand(self):
        sv = pricing.scan_savings(100.0, 0.0, 0.5, REAL)
        self.assertEqual(sv["gross"], 50.0)
        self.assertEqual(sv["billing_mix"], "100% on-demand")
        self.assertIn("on-demand $100.00/mo x 50%", sv["formula"])

    def test_scan_savings_reservation_scaled_by_realization(self):
        c = {"pricing": {"reservation_savings_realization": 0.5}}
        sv = pricing.scan_savings(0.0, 100.0, 0.5, c)
        self.assertEqual(sv["gross"], 25.0)
        self.assertEqual(sv["reservation_part_usd"], 25.0)

    def test_combine_savings_never_exceeds_spend(self):
        self.assertEqual(pricing.combine_savings([50, 50], 100), 75.0)    # 50% + 50% = 75%
        self.assertEqual(pricing.combine_savings([90, 90], 100), 99.0)
        self.assertEqual(pricing.combine_savings([50, 50], None), 100.0)  # unknown spend: summed

    def test_table_shares_split_a_join_once(self):
        self.assertEqual(pricing.table_shares([100, 300]), [0.25, 0.75])
        self.assertEqual(pricing.table_shares([None, 5]), [0.5, 0.5])
        self.assertAlmostEqual(sum(pricing.table_shares([1, 2, 3])), 1.0)

    def test_demo_floor_only_in_demo_mode(self):
        self.assertEqual(pricing.demo_floor(REAL, 3.0, 95.0), (3.0, False))
        self.assertEqual(pricing.demo_floor(DEMO, 3.0, 95.0), (95.0, True))
        self.assertEqual(pricing.demo_floor(DEMO, 300.0, 95.0), (300.0, False))

    def test_deoverlap_total_compounds_cards_on_the_same_spend(self):
        items = [card(net=60, pool="table:p.d.t", spend=100),
                 card(rule="C2-01", net=60, pool="table:p.d.t", spend=100),
                 card(rule="C1-03", target=("p", "d", "u"), net=10)]
        out = pricing.deoverlap_total(items)
        self.assertEqual(out["naive_total"], 130.0)
        self.assertEqual(out["total"], 94.0)          # 100 x (1 - 0.4 x 0.4) + 10
        self.assertEqual(out["overlap_removed"], 36.0)
        self.assertEqual(out["overlapping_cards"], 2)

    def test_pool_of_reads_raw_evidence_json(self):
        raw = {"evidence_json": json.dumps({"savings_math": {"pool_key": "query:abc", "pool_spend_usd": 12}})}
        self.assertEqual(pricing.pool_of(raw), ("query:abc", 12.0))
        self.assertEqual(pricing.pool_of({"evidence_json": "not json"}), (None, None))

    def test_billing_cards_count_only_against_what_the_fixes_leave(self):
        # $1,000/mo on-demand bill. A table fix saves $600; Editions claims $800 (80%).
        # Editions can only cut what is left: 80% x $400 = $320 -> $920, not $1,400.
        items = [card(net=600, pool="table:p.d.t", spend=700),
                 card(rule="W-01", target=("p", "PROJECT_WIDE_BILLING", "ALL"), net=800,
                      pool="billing:p", spend=1000)]
        out = pricing.deoverlap_total(items)
        self.assertEqual(out["total"], 920.0)
        self.assertEqual(out["billing_cards_claimed_usd"], 800.0)
        self.assertEqual(out["billing_cards_counted_usd"], 320.0)

    def test_headline_never_exceeds_actual_spend(self):
        items = [card(net=600, pool="table:p.d.t", spend=700),
                 card(rule="C4-01", target=("p", "queries", "h2"), net=500, pool="query:h2", spend=550),
                 card(rule="W-01", target=("p", "PROJECT_WIDE_BILLING", "ALL"), net=800,
                      pool="billing:p", spend=1000)]
        out = pricing.deoverlap_total(items, ceilings={"compute": 1000})
        self.assertEqual(out["total"], 1000.0)        # fixes alone ($1,100) exceed the $1,000 bill
        self.assertEqual(out["billing_cards_counted_usd"], 0.0)
        self.assertEqual(out["ceiling_applied"], ["compute"])
        # Storage savings come out of a different bill: not capped by compute spend.
        items.append(card(rule="C1-05", target=("p", "d", None), net=50, pool="storage:p.d", spend=200))
        out = pricing.deoverlap_total(items, ceilings={"compute": 1000})
        self.assertEqual((out["total"], out["storage_usd"]), (1050.0, 50.0))

    def test_single_table_query_card_joins_that_tables_pool(self):
        q = card(rule="C4-01", target=("p", "queries", "h1"), net=50)
        q["evidence"]["savings_math"] = {"pool_key": "query:h1", "pool_spend_usd": 100, "tables": ["p.d.t"]}
        t = card(net=50, pool="table:p.d.t", spend=100)
        self.assertEqual(pricing.deoverlap_total([q, t])["total"], 75.0)    # same scans: 50% + 50% = 75%
        multi = dict(q, evidence={"savings_math": dict(q["evidence"]["savings_math"], tables=["p.d.t", "p.d.u"])})
        self.assertEqual(pricing.deoverlap_total([multi, t])["total"], 100.0)

    def test_spend_kind(self):
        self.assertEqual(pricing.spend_kind(card(pool="storage:p.d", spend=1)), pricing.STORAGE)
        self.assertEqual(pricing.spend_kind(card(pool="query:h", spend=1)), pricing.COMPUTE)
        self.assertEqual(pricing.spend_kind({"rule_ids": ["C1-04"], "evidence": {}}), pricing.STORAGE)
        self.assertEqual(pricing.spend_kind({"rule_ids": ["C1-01"], "evidence": {}}), pricing.COMPUTE)  # legacy
        self.assertIsNone(pricing.spend_kind({"evidence": {}}))

    def test_legacy_cards_are_capped_too(self):
        legacy = [{"rule_ids": ["W-01"], "target_project": "p", "net_monthly_value_usd": 8716.5, "evidence": {}},
                  {"rule_ids": ["C1-04"], "target_project": "p", "net_monthly_value_usd": 5.0, "evidence": {}}]
        out = pricing.deoverlap_total(legacy, ceilings={"compute": 2000})
        self.assertEqual(out["total"], 2005.0)        # compute capped at the bill; storage separate


class TestBillingAwareMappers(unittest.TestCase):
    def setUp(self):
        self.prices = {"on_demand_usd_per_tib": 6.25, "slot_hour_usd_enterprise": 0.06}

    def _rollup_row(self, od, rv):
        return {"project_id": "p", "dataset_id": "d", "table_id": "t", "agg_queries_30d": 40,
                "bytes_billed_30d": 50 * TIB, "od_cost_usd_30d": od, "resv_cost_usd_30d": rv}

    def test_mv_rollup_uses_attributed_cost_not_bytes(self):
        # 50 TiB billed would be $312.50 at $/TiB, but only $40 of on-demand cost is
        # attributed to this table: savings = $40 x 75% = $30. No floor in real mode.
        f = rules._map_mv_rollup(self._rollup_row(40.0, 0.0), self.prices, REAL)
        self.assertEqual(f["gross_monthly_savings_usd"], 30.0)
        sm = f["evidence"]["savings_math"]
        self.assertFalse(sm["demo_floor_applied"])
        self.assertEqual(sm["pool_key"], "table:p.d.t")
        self.assertNotIn("DEMO_SYNTHETIC_FLOOR_APPLIED", f["risk_notes"])

    def test_mv_rollup_on_reservation_respects_realization(self):
        c = {"demo_mode": False, "pricing": {"reservation_savings_realization": 0.5}}
        f = rules._map_mv_rollup(self._rollup_row(0.0, 100.0), self.prices, c)
        self.assertEqual(f["gross_monthly_savings_usd"], 37.5)       # 100 x 75% x 50%
        self.assertEqual(f["savings_basis"], "SLOT_EDITIONS")

    def test_demo_floor_is_labelled(self):
        f = rules._map_mv_rollup(self._rollup_row(4.0, 0.0), self.prices, DEMO)
        self.assertEqual(f["gross_monthly_savings_usd"], 95.0)
        self.assertTrue(f["evidence"]["savings_math"]["demo_floor_applied"])
        self.assertEqual(f["evidence"]["savings_math"]["measured_gross_usd"], 3.0)
        self.assertIn("DEMO_SYNTHETIC_FLOOR_APPLIED", f["risk_notes"])

    def test_editions_fit_skipped_when_reservations_cost_more(self):
        # $6.25/mo of on-demand scanning that burns 1,000 slot-hours: Editions can't win.
        row = {"project_id": "p", "od_bytes_billed_30d": 1 * TIB, "od_jobs_30d": 50,
               "od_slot_ms_30d": 1000 * 3_600_000}
        self.assertIsNone(rules._map_editions_fit(row, self.prices, REAL))

    def test_editions_fit_skips_projects_with_no_on_demand_jobs(self):
        row = {"project_id": "p", "od_bytes_billed_30d": 0, "od_jobs_30d": 0, "od_slot_ms_30d": 0}
        self.assertIsNone(rules._map_editions_fit(row, self.prices, REAL))

    def test_reprice_legacy_query_card(self):
        legacy = {"change_set_id": "x", "apply_class": 4, "gross_monthly_savings_usd": 1434.0,
                  "recurring_monthly_cost_usd": 0, "one_time_apply_cost_usd": 0, "confidence": 0.5,
                  "evidence_json": json.dumps({"savings_ratio_heuristic": 0.4, "query_hash": "h1"})}
        fam = {"query_hash": "h1", "est_od_usd": 28.0, "est_resv_usd": 0.0, "billing_project": "bp",
               "sample_user_email": "etl@p.iam.gserviceaccount.com"}
        c = {"project_id": "p", "amortization_months": 12, "risk_weights": {4: 1.5}}
        r = rules.reprice_query_card(legacy, fam, self.prices, c)
        self.assertEqual(r["gross"], 12.0)                 # $28 / 28d -> $30/mo x 40%
        self.assertEqual(r["net"], 12.0)
        self.assertEqual(r["score"], 4.0)                  # 12 x 0.5 / 1.5
        self.assertEqual(r["old_gross"], 1434.0)
        self.assertEqual(r["evidence"]["savings_math"]["pool_key"], "query:h1")
        self.assertEqual(r["evidence"]["savings_math"]["repriced_from_usd"], 1434.0)
        self.assertIsNone(rules.reprice_query_card(legacy, None, self.prices, c))   # query gone
        no_ratio = dict(legacy, evidence_json=json.dumps({"query_hash": "h1"}))
        self.assertIsNone(rules.reprice_query_card(no_ratio, fam, self.prices, c))

    def test_query_cards_record_the_tables_they_read(self):
        row = {"query_hash": "h", "est_od_usd": 28.0, "est_resv_usd": 0.0, "billing_project": "bp",
               "sample_referenced_tables": [{"project_id": "p", "dataset_id": "d", "table_id": "t"},
                                            {"project_id": "p", "dataset_id": "d", "table_id": "t"}]}
        m = rules._c4_money(row, self.prices, {"project_id": "p"}, 0.5)
        self.assertEqual(m["math"]["tables"], ["p.d.t"])
        self.assertEqual(m["math"]["spend_kind"], pricing.COMPUTE)

    def test_editions_fit_prices_autoscale_in_50_slot_steps(self):
        # 200 TiB on-demand ($1,250/mo) used 400 slot-hours, spread over 6,000 busy minutes:
        # autoscale bills >= 50 slots for each of them = 5,000 slot-hours, not 400 x 1.25.
        row = {"project_id": "p", "od_bytes_billed_30d": 200 * TIB, "od_jobs_30d": 900,
               "od_slot_ms_30d": 400 * 3_600_000}
        profile = {"p50_all": 0, "p95_all": 10, "p99_busy": 30, "peak": 60, "busy_minutes": 6000,
                   "total_minutes": 43200, "slot_hours": 400, "autoscale_slot_hours": 5000,
                   "autoscale_slot_hours_above_baseline": 5000}
        with mock.patch.object(rules, "_slot_profile", return_value=profile):
            f = rules._map_editions_fit(row, self.prices, REAL)
        self.assertEqual(f["evidence"]["option_2_monthly_cost_usd"], 300.0)   # 5,000 x $0.06
        self.assertEqual(f["gross_monthly_savings_usd"], 950.0)
        sm = f["evidence"]["savings_math"]
        self.assertIn("50-slot steps", sm["formula"])
        self.assertTrue(sm["slot_telemetry_plausible"])
        self.assertEqual(f["confidence_hint"], 0.85)

    def test_editions_fit_flags_slot_telemetry_that_cannot_cover_the_bytes(self):
        row = {"project_id": "p", "od_bytes_billed_30d": 300 * TIB, "od_jobs_30d": 100,
               "od_slot_ms_30d": 30 * 3_600_000}            # 0.1 slot-hour per TiB: not physical
        with mock.patch.object(rules, "_slot_profile", return_value=None):
            f = rules._map_editions_fit(row, self.prices, REAL)
        self.assertIn("SLOT_TELEMETRY_IMPLAUSIBLE_FOR_BYTES_SCANNED", f["risk_notes"])
        self.assertIn("AUTOSCALE_COST_NOT_CHECKED_AGAINST_JOBS_TIMELINE", f["risk_notes"])
        self.assertEqual(f["confidence_hint"], 0.4)
        self.assertIn("WARNING", f["evidence"]["savings_math"]["formula"])


class TestCompilerRepricing(unittest.TestCase):
    def _f(self, rule, gross, pool_spend=None, cls=1):
        f = {"rule_id": rule, "apply_class": cls, "source": "CUSTOM_RULE", "target_project": "p",
             "target_dataset": "d", "target_table": "t", "gross_monthly_savings_usd": gross,
             "proposed_change": {}, "evidence": {}}
        if pool_spend is not None:
            f["evidence"]["savings_math"] = {"pool_key": "table:p.d.t", "pool_spend_usd": pool_spend}
        return f

    def test_partition_plus_cluster_compounds_against_known_spend(self):
        out, _ = compiler.compile([self._f("C3-01", 60.0, 100.0, cls=3), self._f("C1-01", 60.0, 100.0)], [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["gross_monthly_savings_usd"], 84.0)     # not 120 (> spend)
        self.assertEqual(out[0]["evidence"]["savings_math"]["pool_spend_usd"], 100.0)

    def test_partition_plus_cluster_summed_and_flagged_when_spend_unknown(self):
        out, _ = compiler.compile([self._f("C3-01", 60.0, cls=3), self._f("C1-01", 60.0)], [])
        self.assertEqual(out[0]["gross_monthly_savings_usd"], 120.0)
        self.assertIn("MERGED_SAVINGS_SUMMED_SPEND_UNKNOWN", out[0]["risk_notes"])

    def test_pending_refreshes_only_unapproved_exact_matches(self):
        f = self._f("C2-01", 30.0, 40.0, cls=2)
        base = {"target_project": "p", "target_dataset": "d", "target_table": "t", "rule_ids": ["C2-01"]}
        open_sets = [dict(base, change_set_id="a", state="PENDING_REVIEW", n_approvals=0),
                     dict(base, change_set_id="b", state="PENDING_REVIEW", n_approvals=1),
                     dict(base, change_set_id="c", state="APPROVED", n_approvals=1),
                     dict(base, change_set_id="d", state="PENDING_REVIEW", n_approvals=0,
                          rule_ids=["C1-01"])]
        out = compiler.pending_refreshes([f], open_sets)
        self.assertEqual([cid for cid, _ in out], ["a"])
        self.assertIsNot(out[0][1], f)                      # works on a copy

    def test_legacy_cards_retired_or_repriced(self):
        found = [self._f("C2-01", 30.0, 40.0, cls=2)]
        legacy = [
            {"change_set_id": "kept", "rule_ids": ["C2-01"], "source": "CUSTOM_RULE",
             "target_project": "p", "target_dataset": "d", "target_table": "t"},
            {"change_set_id": "stale", "rule_ids": ["C2-02"], "source": "CUSTOM_RULE",
             "target_project": "p", "target_dataset": "d", "target_table": "t"},
            {"change_set_id": "ai", "rule_ids": ["C4-AI-SCHEMA-PRUNING"], "source": "GEMINI_AI_JUDGE",
             "target_project": "p", "target_dataset": "queries", "target_table": "h1"},
        ]
        self.assertEqual([x["change_set_id"] for x in compiler.stale_legacy_cards(found, legacy)], ["stale"])
        self.assertEqual([x["change_set_id"] for x in compiler.legacy_to_reprice(found, legacy)], ["ai"])


class TestVerifierBillingAware(unittest.TestCase):
    PRICES = {"on_demand_usd_per_tib": 6.25, "slot_hour_usd_enterprise": 0.06}

    def test_per_mode_realized_savings(self):
        base = {"h": {"execs": 20, "bytes_per_exec": TIB, "slot_ms_per_exec": 3_600_000, "p95_ms": 100,
                      "od_execs": 10, "od_bytes_per_exec": TIB,
                      "resv_execs": 10, "resv_slot_ms_per_exec": 3_600_000, "resv_slot_rate_usd": 0.06}}
        post = {"h": {"execs": 20, "bytes_per_exec": TIB / 2, "slot_ms_per_exec": 1_800_000, "p95_ms": 90,
                      "od_execs": 10, "od_bytes_per_exec": TIB / 2,
                      "resv_execs": 10, "resv_slot_ms_per_exec": 1_800_000, "resv_slot_rate_usd": 0.06}}
        out = verifier.compare_families(base, post, win=30, min_execs=1, thresh=1.15,
                                        prices=self.PRICES, c=REAL)
        self.assertTrue(out["billing_aware"])
        self.assertAlmostEqual(out["od_usd"], 31.25)        # 0.5 TiB x 10 execs x $6.25
        self.assertAlmostEqual(out["resv_usd"], 0.3)         # 0.5 slot-h x 10 execs x $0.06
        self.assertEqual(out["regressed"], 0)

    def test_regression_detected_per_mode(self):
        base = {"h": {"execs": 10, "p95_ms": 100, "od_execs": 10, "od_bytes_per_exec": TIB,
                      "resv_execs": 0}}
        post = {"h": {"execs": 10, "p95_ms": 100, "od_execs": 10, "od_bytes_per_exec": 1.5 * TIB,
                      "resv_execs": 0}}
        out = verifier.compare_families(base, post, win=14, min_execs=1, thresh=1.15,
                                        prices=self.PRICES, c=REAL)
        self.assertEqual(out["regressed"], 1)

    def test_old_plans_fall_back_to_totals(self):
        base = {"h": {"execs": 10, "bytes_per_exec": TIB, "p95_ms": 100}}
        post = {"h": {"execs": 10, "bytes_per_exec": TIB / 2, "p95_ms": 100}}
        out = verifier.compare_families(base, post, win=30, min_execs=1, thresh=1.15,
                                        prices=self.PRICES, c=REAL)
        self.assertFalse(out["billing_aware"])
        self.assertAlmostEqual(out["bytes_delta"], 5 * TIB)


# ---------------------------------------------------------------------------
# Fix #1: FinOps view + server-side permission check
# ---------------------------------------------------------------------------

GOV = {"governance": {"finops_approvers": ["Shawn.Stark@example.com", "finops.lead@example.com"],
                      "trust_client_identity": False}}
GOV_DEMO = {"governance": dict(GOV["governance"], trust_client_identity=True)}
W01 = {"rule_ids": ["W-01"], "target_project": "p", "target_dataset": "PROJECT_WIDE_BILLING",
       "target_table": None}
TABLE_CARD = {"rule_ids": ["C1-01"], "target_project": "p", "target_dataset": "d", "target_table": "t"}


class TestGovernance(unittest.TestCase):
    def test_finops_card_classification(self):
        self.assertTrue(governance.is_finops_card(W01, GOV))
        self.assertTrue(governance.is_finops_card({"rule_ids": ["C1-05"], "target_dataset": "d"}, GOV))
        self.assertTrue(governance.is_finops_card({"rule_ids": ["X"], "target_project": "p"}, GOV))  # project-level
        self.assertTrue(governance.is_finops_card(
            {"rule_ids": ["X"], "target_dataset": "d", "target_table": "t",
             "proposed_change_json": json.dumps({"action": "SET_STORAGE_BILLING_MODEL"})}, GOV))
        self.assertFalse(governance.is_finops_card(TABLE_CARD, GOV))
        self.assertFalse(governance.is_finops_card(
            {"rule_ids": ["C4-01"], "target_dataset": "queries", "target_table": "h"}, GOV))

    def test_finops_viewer_requires_trusted_identity(self):
        iap = {"email": "shawn.stark@example.com", "source": governance.SOURCE_IAP}
        self.assertTrue(governance.is_finops_viewer(iap, GOV))         # case-insensitive match
        self.assertFalse(governance.is_finops_viewer(dict(iap, email="analyst@example.com"), GOV))
        client = {"email": "finops.lead@example.com", "source": governance.SOURCE_CLIENT}
        self.assertFalse(governance.is_finops_viewer(client, GOV))      # persona picker not trusted
        self.assertTrue(governance.is_finops_viewer(client, GOV_DEMO))  # ...except in demo mode
        ph = {"email": "finops.lead@example.com", "source": governance.SOURCE_PLACEHOLDER}
        self.assertFalse(governance.is_finops_viewer(ph, GOV))

    def test_check_can_decide(self):
        analyst = {"email": "analyst@example.com", "source": governance.SOURCE_IAP}
        governance.check_can_decide(TABLE_CARD, analyst, GOV)           # engineering card: fine
        with self.assertRaises(governance.PermissionDenied):
            governance.check_can_decide(W01, analyst, GOV)
        with self.assertRaisesRegex(governance.PermissionDenied, "no FinOps approvers"):
            governance.check_can_decide(W01, analyst, {})               # fail closed

    def test_env_adds_approvers(self):
        with mock.patch.dict(os.environ, {"BQOPT_FINOPS_APPROVERS": "gov@example.com, x@example.com"}):
            self.assertIn("gov@example.com", governance.finops_approvers({}))

    def test_executor_block_reason(self):
        ok = dict(W01, approvals=[{"principal": "finops.lead@example.com"}])
        self.assertIsNone(governance.executor_block_reason(ok, GOV))
        bad = dict(W01, approvals=[{"principal": "finops.lead@example.com"}, {"principal": "eng@example.com"}])
        self.assertIn("eng@example.com", governance.executor_block_reason(bad, GOV))
        self.assertIn("no recorded approvals", governance.executor_block_reason(W01, GOV))
        self.assertIsNone(governance.executor_block_reason(TABLE_CARD, GOV))

    def test_split_items(self):
        items = [dict(W01), dict(TABLE_CARD)]
        visible, hidden = governance.split_items(items, False, GOV)
        self.assertEqual(hidden, 1)
        self.assertEqual([x["governance_scope"] for x in visible], [governance.ENGINEERING])
        visible, hidden = governance.split_items([dict(W01), dict(TABLE_CARD)], True, GOV)
        self.assertEqual((len(visible), hidden), (2, 0))


class TestReviewAppPermissions(unittest.TestCase):
    """The FinOps split and the 403 are enforced by the server, not the UI."""

    @classmethod
    def setUpClass(cls):
        from optimizer.config import Config
        from review_app import main
        cls.main = main
        cls.cfg = Config({"project_id": "p", "ops_dataset": "optimizer_ops", "location": "US",
                          **GOV_DEMO})

    def _data(self):
        w01 = dict(W01, change_set_id="w", apply_class=3, net_monthly_value_usd=500.0, evidence={})
        tbl = dict(TABLE_CARD, change_set_id="t", apply_class=1, net_monthly_value_usd=40.0,
                   evidence={"savings_math": {"pool_key": "table:p.d.t", "pool_spend_usd": 80}})
        data = {"cards": [w01, tbl], "blocked_cards": [], "regressed_cards": [],
                "rolled_back_cards": [], "handoff_cards": [], "receipts": [],
                "w01": {"od": 1000.0}, "reasons": [], "reviewer_email": "x"}
        data.update(self.main._summaries(self.cfg, data))
        return data

    def test_dashboard_scoped_for_engineering_viewer(self):
        ident = {"email": "alice-owner@company.com", "source": governance.SOURCE_CLIENT}
        d = self.main._scope_for_viewer(self.cfg, self._data(), ident)
        self.assertEqual([x["change_set_id"] for x in d["cards"]], ["t"])
        self.assertIsNone(d["w01"])
        self.assertEqual(d["kpis"]["monthly_savings"], 40.0)
        self.assertEqual(d["kpis"]["finops_pending_count"], 0)
        self.assertFalse(d["viewer"]["is_finops"])
        self.assertEqual(d["viewer"]["hidden_finops_items"], 1)

    def test_dashboard_full_for_finops_viewer(self):
        ident = {"email": "finops.lead@example.com", "source": governance.SOURCE_CLIENT}
        d = self.main._scope_for_viewer(self.cfg, self._data(), ident)
        self.assertEqual(len(d["cards"]), 2)
        self.assertEqual(d["kpis"]["finops_monthly_savings"], 500.0)
        self.assertEqual(d["kpis"]["engineering_monthly_savings"], 40.0)
        self.assertTrue(d["viewer"]["is_finops"])

    def test_headline_capped_at_spend_and_spend_hidden_from_engineering(self):
        w01 = dict(W01, change_set_id="w", apply_class=3, net_monthly_value_usd=900.0,
                   evidence={"savings_math": {"pool_key": "billing:p", "pool_spend_usd": 1000}})
        tbl = dict(TABLE_CARD, change_set_id="t", apply_class=1, net_monthly_value_usd=700.0,
                   evidence={"savings_math": {"pool_key": "table:p.d.t", "pool_spend_usd": 800}})
        data = {"cards": [w01, tbl], "blocked_cards": [], "regressed_cards": [], "rolled_back_cards": [],
                "handoff_cards": [], "receipts": [], "w01": None, "reasons": [], "reviewer_email": "x",
                "spend_ceilings": {"compute": 1000.0}}
        fin = self.main._scope_for_viewer(self.cfg, copy.deepcopy(data),
                                          {"email": "finops.lead@example.com", "source": governance.SOURCE_CLIENT})
        # The table fix leaves $300 of the $1,000 bill; Editions' 90% only counts on that ($270).
        self.assertEqual(fin["kpis"]["monthly_savings"], 970.0)
        self.assertEqual(fin["kpis"]["compute_spend_30d_usd"], 1000.0)
        self.assertNotIn("spend_ceilings", fin)
        eng = self.main._scope_for_viewer(self.cfg, copy.deepcopy(data),
                                          {"email": "alice-owner@company.com", "source": governance.SOURCE_CLIENT})
        self.assertEqual(eng["kpis"]["monthly_savings"], 700.0)
        self.assertIsNone(eng["kpis"]["compute_spend_30d_usd"])
        self.assertNotIn("spend_ceilings", eng)

    def test_api_decision_403_for_non_finops(self):
        client = self.main.app.test_client()
        cs = dict(W01, change_set_id="w", apply_class=3, approvals=[], proposed_change_json="{}")
        with mock.patch.object(self.main, "cfg", return_value=self.cfg), \
                mock.patch.object(self.main.store, "get", return_value=cs), \
                mock.patch.object(self.main.store, "approve") as approve:
            r = client.post("/api/decisions", json={"change_set_id": "w", "action": "approve",
                                                    "principal": "alice-owner@company.com"})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.get_json()["code"], "FINOPS_PERMISSION_REQUIRED")
            approve.assert_not_called()

    def test_client_principal_ignored_without_demo_trust(self):
        from optimizer.config import Config
        prod = Config({"project_id": "p", "ops_dataset": "optimizer_ops", **GOV})
        with self.main.app.test_request_context("/api/dashboard?principal=finops.lead@example.com"), \
                mock.patch.dict(os.environ, {"REVIEWER_EMAIL": "analyst@example.com"}):
            ident = self.main._identity(prod, "finops.lead@example.com")
        self.assertEqual(ident, {"email": "analyst@example.com", "source": governance.SOURCE_SERVER})

    def test_forged_iap_header_not_trusted(self):
        from optimizer.config import Config
        prod = Config({"project_id": "p", "ops_dataset": "optimizer_ops", **GOV})
        hdrs = {"X-Goog-Authenticated-User-Email": "accounts.google.com:finops.lead@example.com"}
        with self.main.app.test_request_context("/", headers=hdrs), \
                mock.patch.dict(os.environ, {"REVIEWER_EMAIL": "analyst@example.com"}):
            ident = self.main._identity(prod)
        self.assertEqual(ident["email"], "analyst@example.com")


# ---------------------------------------------------------------------------
# Fix #2: service-account owner table
# ---------------------------------------------------------------------------

class TestAttribution(unittest.TestCase):
    BASE = {"project_id": "p", "ops_dataset": "optimizer_ops"}

    def test_no_tables_configured_keeps_defaults(self):
        self.assertEqual(attribution.source_view_sql(self.BASE), [])

    def test_sa_table_with_custom_column_names(self):
        c = dict(self.BASE, service_account_owner_table="gov-proj.iam.sa_owner_map",
                 service_account_owner_columns={"service_account": "sa_email", "owner": "owner_email",
                                                "director_name": "dir_name"})
        (stmt,) = attribution.source_view_sql(c)
        self.assertIn("CREATE OR REPLACE VIEW `p.optimizer_ops.v_service_account_owners_src`", stmt)
        self.assertIn("CAST(`sa_email` AS STRING) AS service_account_email", stmt)
        self.assertIn("CAST(`dir_name` AS STRING) AS director_name", stmt)
        self.assertIn("CAST(NULL AS STRING) AS application", stmt)
        self.assertIn("FROM `gov-proj.iam.sa_owner_map`", stmt)

    def test_config_is_validated(self):
        c = dict(self.BASE, service_account_owner_table="gov-proj.iam.sa_owner_map",
                 service_account_owner_columns={"service_account": "sa_email"})
        with self.assertRaisesRegex(attribution.AttributionConfigError, "owner is required"):
            attribution.source_view_sql(c)
        c["service_account_owner_columns"] = {"service_account": "sa_email; DROP", "owner": "o"}
        with self.assertRaises(attribution.AttributionConfigError):
            attribution.source_view_sql(c)
        c = dict(self.BASE, service_account_owner_table="not_qualified")
        with self.assertRaises(attribution.AttributionConfigError):
            attribution.source_view_sql(c)

    def test_sa_owner_map(self):
        m = attribution.sa_owner_map([
            {"email": "PowerBI@p.iam.gserviceaccount.com", "accountable_owner_email": "Jane@x.com",
             "director_name": "Dir A", "attribution_source": "SERVICE_ACCOUNT_OWNER"},
            {"email": "orphan@p.iam.gserviceaccount.com", "accountable_owner_email": None}])
        self.assertEqual(list(m), ["powerbi@p.iam.gserviceaccount.com"])
        self.assertEqual(m["powerbi@p.iam.gserviceaccount.com"]["owner_email"], "jane@x.com")


class TestOwnerRouting(unittest.TestCase):
    SA = "powerbi@p.iam.gserviceaccount.com"
    MAP = {SA: {"owner_email": "jane@x.com", "director_name": "Dir A"}}
    CFG = {"project_id": "p", "default_owner": "platform@x.com"}

    def test_accountable_owner(self):
        self.assertEqual(router.accountable_owner(self.SA, self.MAP)["owner_email"], "jane@x.com")
        self.assertIsNone(router.accountable_owner("etl@p.iam.gserviceaccount.com", self.MAP))
        self.assertEqual(router.accountable_owner("bob@x.com", self.MAP)["source"], "PRINCIPAL")

    def test_service_account_top_writer_routes_to_its_owner(self):
        f = {"apply_class": 1, "target_dataset": "d", "evidence": {"top_writer_email": self.SA}}
        self.assertEqual(router.resolve_owner(self.CFG, f, self.MAP), ("jane@x.com", "SERVICE_ACCOUNT_OWNER"))
        self.assertEqual(f["evidence"]["service_account_owner"]["director_name"], "Dir A")

    def test_unmapped_service_account_is_never_the_owner(self):
        f = {"apply_class": 1, "target_dataset": "d",
             "evidence": {"top_writer_email": "etl@p.iam.gserviceaccount.com"}}
        self.assertEqual(router.resolve_owner(self.CFG, f, self.MAP), ("platform@x.com", "DATASET_ADMIN"))
        self.assertIn("TOP_WRITER_IS_UNMAPPED_SERVICE_ACCOUNT", f["risk_notes"])


if __name__ == "__main__":
    unittest.main()
