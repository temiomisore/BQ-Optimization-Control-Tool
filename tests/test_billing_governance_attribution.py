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

GOV = {"governance": {"finops_approvers": ["FinOps.Director@example.com", "finops.lead@example.com"],
                      "trust_client_identity": False}}
GOV_DEMO = {"governance": dict(GOV["governance"], trust_client_identity=True)}
W01 = {"rule_ids": ["W-01"], "target_project": "p", "target_dataset": "PROJECT_WIDE_BILLING",
       "target_table": None}
TABLE_CARD = {"rule_ids": ["C1-01"], "target_project": "p", "target_dataset": "d", "target_table": "t",
              "owner_principal": "analyst@example.com"}


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
        iap = {"email": "finops.director@example.com", "source": governance.SOURCE_IAP}
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


class TestReservationRightsizingAnd180dCapacity(unittest.TestCase):
    PRICES = dict(pricing.DEFAULT_PRICES)

    def test_180d_capacity_window_default_on_real_config(self):
        from optimizer import config as cfg_mod
        real_cfg = cfg_mod.Config({
            **cfg_mod._DEFAULTS,
            "project_id": "acme-analytics-prod",
            "ops_dataset": "optimizer_ops",
        })
        self.assertEqual(rules._capacity_sizing_days(real_cfg), 180)
        sql_w01 = rules._sql_editions_fit(real_cfg)
        self.assertIn("INTERVAL 180 DAY", sql_w01)
        self.assertIn("30.0 / active_days", sql_w01)
        sql_w03 = rules._sql_reservation_rightsizing(real_cfg)
        self.assertIn("INTERVAL 180 DAY", sql_w03)
        self.assertIn("CAPACITY_COMMITMENT", sql_w03)

    def test_w01_multi_region_target_table_and_slot_estimator_link(self):
        cfg_180 = {
            "demo_mode": False,
            "location": "US",
            "pricing": {"capacity_sizing_days": 180},
        }
        prof = {
            "p50_all": 120.0, "p95_all": 260.0, "p50_busy": 185.0, "p99_busy": 340.0, "peak": 400.0,
            "busy_minutes": 100000.0, "total_minutes": 259200.0, "active_days": 180.0,
            "slot_hours": 90000.0, "autoscale_slot_hours": 115000.0,
            "autoscale_slot_hours_above_baseline": 28000.0,
            "source_label": "JOBS_TIMELINE_180D",
        }
        row = {
            "project_id": "acme-analytics-prod",
            "region": "us-east4",
            "active_days": 180.0,
            "od_bytes_billed_30d": 2000 * TIB,
            "od_slot_ms_30d": 90000 * 3_600_000,
            "od_jobs_30d": 1500,
            "resv_jobs_30d": 0,
        }
        with mock.patch.object(rules, "_slot_profile", return_value=prof) as m_prof:
            f = rules._map_editions_fit(row, self.PRICES, cfg_180)
        self.assertIsNotNone(f)
        m_prof.assert_called_once_with(cfg_180, "acme-analytics-prod", "us-east4", days=180)
        self.assertEqual(f["target_table"], "ALL_DATASETS_AND_TABLES_US_EAST4")
        self.assertEqual(f["observation_days"], 180)
        self.assertEqual(f["evidence"]["capacity_sizing"]["p50_slots_busy_minutes"], 185.0)
        self.assertEqual(f["evidence"]["capacity_sizing"]["recommended_baseline_slots"], 100)
        self.assertEqual(f["evidence"]["capacity_sizing"]["recommended_max_slots"], 350)
        self.assertEqual(
            f["evidence"]["slot_estimator_url"],
            "https://console.cloud.google.com/bigquery/admin/reservations;region=us-east4"
            "/slot-estimator;source=EDITION_UNSPECIFIED;resourceType=PROJECT;resource=acme-analytics-prod"
            "?project=acme-analytics-prod",
        )

    def test_w03_overprovisioned_baseline_rightsizing_and_cud_lockin_warning(self):
        cfg_180 = {"demo_mode": False, "location": "US", "pricing": {"capacity_sizing_days": 180}}
        prof = {
            "p50_all": 110.0, "p95_all": 220.0, "p99_busy": 290.0, "peak": 350.0,
            "busy_minutes": 120000.0, "total_minutes": 259200.0, "active_days": 180.0,
            "slot_hours": 80000.0, "autoscale_slot_hours": 105000.0,
            "autoscale_slot_hours_above_baseline": 12000.0,
            "current_burst_slot_hours": 0.0,
            "source_label": "JOBS_TIMELINE_180D",
        }
        row = {
            "admin_project": "acme-slot-admin",
            "region": "us-east4",
            "reservation_name": "prod-edw-pool",
            "edition": "ENTERPRISE",
            "baseline_slots": 500,
            "autoscale_max_slots": 500,
            "edition_total_baseline_slots": 500,
            "committed_slots": 500,
            "active_days": 180.0,
            "jobs_window": 24000,
            "jobs_30d": 4000,
            "slot_ms_30d": 80000 * 3_600_000,
            "bytes_billed_30d": 3000 * TIB,
        }
        with mock.patch.object(rules, "_slot_profile", return_value=prof):
            f = rules._map_reservation_rightsizing(row, self.PRICES, cfg_180)
        self.assertIsNotNone(f)
        self.assertEqual(f["rule_id"], "W-03")
        self.assertEqual(f["apply_class"], 3)
        self.assertEqual(f["target_dataset"], "RESERVATION_CAPACITY")
        self.assertEqual(f["target_table"], "prod-edw-pool")
        self.assertEqual(f["proposed_change"]["action"], "RESIZE_RESERVATION")
        self.assertEqual(f["proposed_change"]["recommended_baseline_slots"], 100)
        self.assertEqual(f["proposed_change"]["recommended_autoscale_max_slots"], 300)
        self.assertEqual(f["evidence"]["diagnosis_kind"], "OVERPROVISIONED_BASELINE")
        self.assertIn("COMMITTED_SLOTS_CONTINUE_UNTIL_EXPIRY", f["risk_notes"])
        self.assertIn("ALTER RESERVATION `acme-slot-admin.region-us-east4.prod-edw-pool`",
                      f["proposed_change"]["generated_ddl"])

    def test_w03_underprovisioned_baseline_and_uncovered_payg_slots(self):
        cfg_180 = {"demo_mode": False, "location": "US", "pricing": {"capacity_sizing_days": 180}}
        prof = {
            "p50_all": 260.0, "p95_all": 340.0, "p99_busy": 390.0, "peak": 450.0,
            "busy_minutes": 240000.0, "total_minutes": 259200.0, "active_days": 180.0,
            "slot_hours": 190000.0, "autoscale_slot_hours": 225000.0,
            "autoscale_slot_hours_above_baseline": 15000.0,
            "current_burst_slot_hours": 185000.0,
            "source_label": "JOBS_TIMELINE_180D",
        }
        row = {
            "admin_project": "acme-slot-admin",
            "region": "us",
            "reservation_name": "etl-heavy-pool",
            "edition": "ENTERPRISE",
            "baseline_slots": 100,
            "autoscale_max_slots": 500,
            "edition_total_baseline_slots": 100,
            "committed_slots": 0,  # 100 baseline slots are uncovered PAYG!
            "active_days": 180.0,
            "jobs_window": 90000,
            "jobs_30d": 15000,
            "slot_ms_30d": 190000 * 3_600_000,
            "bytes_billed_30d": 4500 * TIB,
        }
        with mock.patch.object(rules, "_slot_profile", return_value=prof):
            f = rules._map_reservation_rightsizing(row, self.PRICES, cfg_180)
        self.assertIsNotNone(f)
        self.assertEqual(f["evidence"]["diagnosis_kind"], "UNDERPROVISIONED_BASELINE_HEAVY_AUTOSCALE")
        self.assertEqual(f["proposed_change"]["recommended_baseline_slots"], 250)
        self.assertEqual(f["proposed_change"]["recommended_autoscale_max_slots"], 400)
        self.assertEqual(f["proposed_change"]["selected_option"], 1)
        self.assertIn("UNCOVERED_BASELINE_PAYG_SLOTS", f["risk_notes"])
        self.assertIn("CUD_PURCHASE_MANUAL_IN_CONSOLE", f["risk_notes"])

    def test_w03_tiny_reservation_recommends_returning_to_on_demand(self):
        cfg_180 = {"demo_mode": False, "location": "US", "pricing": {"capacity_sizing_days": 180}}
        prof = {
            "p50_all": 0.0, "p95_all": 0.0, "p99_busy": 80.0, "peak": 90.0,
            "busy_minutes": 3000.0, "total_minutes": 259200.0, "active_days": 180.0,
            "slot_hours": 400.0, "autoscale_slot_hours": 500.0,
            "autoscale_slot_hours_above_baseline": 500.0,
            "current_burst_slot_hours": 0.0,
            "source_label": "JOBS_TIMELINE_180D",
        }
        row = {
            "admin_project": "acme-slot-admin",
            "region": "us",
            "reservation_name": "dev-tiny-pool",
            "edition": "ENTERPRISE",
            "baseline_slots": 100,
            "autoscale_max_slots": 100,
            "edition_total_baseline_slots": 100,
            "committed_slots": 0,
            "assignment_ids": ["assign-dev"],
            "active_days": 180.0,
            "jobs_window": 600,
            "jobs_30d": 100,
            "slot_ms_30d": 400 * 3_600_000,
            "bytes_billed_30d": 2 * TIB,  # $12.50/mo on On-Demand vs $30.00/mo pure autoscale vs $4,380/mo baseline!
        }
        with mock.patch.object(rules, "_slot_profile", return_value=prof):
            f = rules._map_reservation_rightsizing(row, self.PRICES, cfg_180)
        self.assertIsNotNone(f)
        self.assertEqual(f["evidence"]["option_2_mode"], "ON_DEMAND")
        self.assertEqual(f["proposed_change"]["selected_option"], 2)
        self.assertEqual(f["savings_basis"], "BYTES_ON_DEMAND")
        self.assertIn("DROP ASSIGNMENT IF EXISTS `acme-slot-admin.region-us.dev-tiny-pool.assign-dev`",
                      f["proposed_change"]["generated_ddl_option2"])

    def test_w03_standard_edition_never_uses_cud_discount_and_caps_at_1600(self):
        self.assertAlmostEqual(pricing.edition_commit_rate(self.PRICES, "STANDARD"), 0.04)
        self.assertAlmostEqual(pricing.edition_slot_rate(self.PRICES, "STANDARD"), 0.04)
        cfg_180 = {"demo_mode": False, "location": "US", "pricing": {"capacity_sizing_days": 180}}
        prof = {
            "p50_all": 0.0, "p95_all": 200.0, "p99_busy": 1900.0, "peak": 2100.0,
            "busy_minutes": 20000.0, "total_minutes": 259200.0, "active_days": 180.0,
            "slot_hours": 2000.0, "autoscale_slot_hours": 2600.0,
            "autoscale_slot_hours_above_baseline": 2600.0,
            "current_burst_slot_hours": 0.0,
        }
        row = {
            "admin_project": "acme-slot-admin",
            "region": "us",
            "reservation_name": "std-pool",
            "edition": "STANDARD",
            "baseline_slots": 400,
            "autoscale_max_slots": 0,
            "active_days": 180.0,
            "jobs_window": 1200,
            "jobs_30d": 200,
            "slot_ms_30d": 2000 * 3_600_000,
            "bytes_billed_30d": 500 * TIB,
        }
        with mock.patch.object(rules, "_slot_profile", return_value=prof):
            f = rules._map_reservation_rightsizing(row, self.PRICES, cfg_180)
        self.assertIsNotNone(f)
        self.assertEqual(f["proposed_change"]["recommended_autoscale_max_slots"], 1600)
        self.assertNotIn("UNCOVERED_BASELINE_PAYG_SLOTS", f["risk_notes"])
        self.assertNotIn("CUD_PURCHASE_MANUAL_IN_CONSOLE", f["risk_notes"])

    def test_governance_deoverlap_and_class3_executor_resize_and_rollback(self):
        from optimizer.executor import class3
        from optimizer import collector_driver, cli as cli_mod
        from optimizer import config as cfg_mod

        w03_card = {
            "change_set_id": "cs-w03-1",
            "rule_ids": ["W-03"],
            "apply_class": 3,
            "target_project": "acme-slot-admin",
            "target_dataset": "RESERVATION_CAPACITY",
            "target_table": "dev-tiny-pool",
            "target_region": "us-east4",
            "net_monthly_value_usd": 1200.0,
            "evidence": {
                "savings_math": {
                    "pool_key": "reservation:acme-slot-admin:us-east4:dev-tiny-pool",
                    "pool_spend_usd": 1500.0,
                }
            },
        }
        self.assertTrue(governance.is_finops_card(w03_card, GOV))
        self.assertTrue(cli_mod._in_scope(w03_card, "analytics_mart"))
        deov = pricing.deoverlap_total([w03_card], ceilings={"compute": 1000.0})
        self.assertEqual(deov["total"], 1000.0)
        self.assertIn("compute", deov["ceiling_applied"])

        # Executor apply + rollback for Option 2 ON_DEMAND return
        exec_cfg = cfg_mod.Config({
            **cfg_mod._DEFAULTS,
            "project_id": "acme-slot-admin",
            "ops_dataset": "optimizer_ops",
            "executor": {**cfg_mod._DEFAULTS["executor"], "enable_class3": True, "enable_reservation_changes": True},
        })
        change = {
            "action": "RESIZE_RESERVATION",
            "reservation_name": "dev-tiny-pool",
            "edition": "ENTERPRISE",
            "recommended_baseline_slots": 0,
            "recommended_autoscale_max_slots": 100,
            "option_2_mode": "ON_DEMAND",
            "selected_option": 2,
        }
        cs_row = dict(w03_card, proposed_change_json=json.dumps(change), progress_json=None)
        executed_sql: list[tuple[str, str | None]] = []

        def fake_query(_c, sql, params=None, location=None):
            if "INFORMATION_SCHEMA.RESERVATIONS" in sql:
                return [{"reservation_name": "dev-tiny-pool", "edition": "ENTERPRISE",
                         "slot_capacity": 100, "autoscale_max_slots": 50}]
            if "INFORMATION_SCHEMA.ASSIGNMENTS" in sql:
                return [{"assignment_id": "assign-dev", "assignee_type": "PROJECT", "assignee_id": "acme-dev-proj"}]
            return []

        def fake_execute(_c, sql, params=None, location=None):
            executed_sql.append((sql, location))
            return 1

        with mock.patch("optimizer.bq.query", side_effect=fake_query), \
             mock.patch("optimizer.bq.execute", side_effect=fake_execute):
            plan = class3.run(exec_cfg, cs_row)
            self.assertTrue(plan["return_to_on_demand"])
            self.assertEqual(plan["prior_baseline_slots"], 100)
            self.assertEqual(plan["prior_autoscale_max_slots"], 50)
            ddl_calls = [s for s, _ in executed_sql if not s.strip().startswith("UPDATE")]
            self.assertTrue(any("DROP ASSIGNMENT IF EXISTS `acme-slot-admin.region-us-east4.dev-tiny-pool.assign-dev`" in s for s in ddl_calls))
            self.assertTrue(any("ALTER RESERVATION `acme-slot-admin.region-us-east4.dev-tiny-pool`" in s and "slot_capacity = 0" in s for s in ddl_calls))

            executed_sql.clear()
            cs_row["rollback_plan_json"] = json.dumps(plan)
            class3.rollback(exec_cfg, cs_row)
            rb_ddls = [s for s, _ in executed_sql if not s.strip().startswith("UPDATE")]
            # Must restore slot capacity BEFORE recreating QUERY assignment
            self.assertIn("ALTER RESERVATION `acme-slot-admin.region-us-east4.dev-tiny-pool`", rb_ddls[0])
            self.assertIn("slot_capacity = 100, autoscale_max_slots = 50", rb_ddls[0])
            self.assertIn("CREATE ASSIGNMENT `acme-slot-admin.region-us-east4.dev-tiny-pool.assign-dev`", rb_ddls[1])
            self.assertIn("assignee = 'projects/acme-dev-proj'", rb_ddls[1])

        # Collector driver reservation auto-discovery when reservation_admin_project is None
        disc_queries: list[str] = []

        def fake_disc_query(_c, sql, params=None, location=None):
            disc_queries.append(sql)
            if "FROM `acme-slot-admin.optimizer_ops.jobs_events`" in sql:
                return [{"admin_project": "central-slots-prod", "res_region": "us-east4"}]
            if "FROM `acme-slot-admin.optimizer_ops.object_state_raw_daily`" in sql:
                return []
            if "INFORMATION_SCHEMA.RESERVATIONS" in sql:
                return [{"raw_json": '{"reservation_name":"pool1","slot_capacity":200}'}]
            return []

        with mock.patch("optimizer.bq.query", side_effect=fake_disc_query), \
             mock.patch("optimizer.bq.execute", return_value=1):
            snap_n = collector_driver.snapshot_reservations(exec_cfg)
        self.assertGreaterEqual(snap_n, 1)
        self.assertTrue(any("`central-slots-prod.region-us-east4`.INFORMATION_SCHEMA.RESERVATIONS" in q for q in disc_queries))

    def test_slot_usage_timeline_and_dashboard_hydration(self):
        from optimizer import config as cfg_mod
        from review_app import main as review_main

        c = cfg_mod.Config({
            **cfg_mod._DEFAULTS,
            "project_id": "acme-slot-admin",
            "ops_dataset": "optimizer_ops",
        })
        sample_rows = [
            {"t": "2026-10-20 10:00", "demand": 0.0, "med_busy": 0.0, "busy_mins": 0},
            {"t": "2026-10-20 22:00", "demand": 28.9, "med_busy": 2.4, "busy_mins": 720},
        ]

        # 1. Primary JOBS_TIMELINE path
        with mock.patch("optimizer.bq.query", return_value=sample_rows):
            tl = rules.slot_usage_timeline(
                c, "acme-slot-admin", "us", days=30, reservation_id="acme-slot-admin:us.pool1"
            )
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[1]["demand"], 28.9)
        self.assertEqual(tl[1]["busy_mins"], 720)

        # 2. Fallback to jobs_hourly_slots when JOBS_TIMELINE fails
        def fake_fallback(_c, sql, params=None, location=None):
            if "JOBS_TIMELINE" in sql:
                raise RuntimeError("403 JOBS_TIMELINE denied")
            if "jobs_hourly_slots" in sql:
                return sample_rows
            return []

        with mock.patch("optimizer.bq.query", side_effect=fake_fallback):
            tl_fb = rules.slot_usage_timeline(
                c, "acme-slot-admin", "us", days=30, reservation_id="acme-slot-admin:us.pool1"
            )
        self.assertEqual(len(tl_fb), 2)
        self.assertEqual(tl_fb[0]["t"], "2026-10-20 10:00")

        # 3. Live dashboard hydration populates timeline + CUD metadata on pre-existing cards and caches hits
        review_main.invalidate_dashboard_cache()
        ev = {
            "reservation_fq": "acme-slot-admin.region-us-east4.prod-pool",
            "covered_baseline_slots": 200,
            "committed_slots_edition": 200,
            "sizing_window_days": 180,
            "capacity_sizing": {
                "source": "JOBS_TIMELINE_180D",
                "sizing_window_days": 180,
                "p50_slots_all_minutes": 0.0,
                "p99_slots_busy_minutes": 25.0,
                "peak_slots_1min": 28.9,
            },
        }
        with mock.patch.object(rules, "slot_usage_timeline", return_value=sample_rows) as m_tl:
            cap1 = review_main._hydrate_capacity_sizing(
                c, ev, rule_id="W-03", target_project="acme-slot-admin", target_region="us-east4", target_table="prod-pool"
            )
            ev2 = {"reservation_fq": "acme-slot-admin.region-us-east4.prod-pool", "capacity_sizing": {"sizing_window_days": 180}}
            cap2 = review_main._hydrate_capacity_sizing(
                c, ev2, rule_id="W-03", target_project="acme-slot-admin", target_region="us-east4", target_table="prod-pool"
            )
        m_tl.assert_called_once_with(
            c, "acme-slot-admin", "us-east4", days=180, reservation_id="acme-slot-admin:us-east4.prod-pool"
        )
        self.assertEqual(cap1["covered_baseline_slots"], 200)
        self.assertEqual(len(cap1["timeline"]), 2)
        self.assertEqual(len(cap2["timeline"]), 2)
        review_main.invalidate_dashboard_cache()

    def test_w03_active_assigned_properly_sized_vs_unassigned_leftovers(self):
        from optimizer import compiler, config as cfg_mod, scoring
        from optimizer.executor import class3

        c = cfg_mod.Config({
            **cfg_mod._DEFAULTS,
            "project_id": "acme-slot-admin",
            "ops_dataset": "optimizer_ops",
            "location": "US",
            "pricing": {**cfg_mod._DEFAULTS["pricing"], "capacity_sizing_days": 180},
            "executor": {**cfg_mod._DEFAULTS["executor"], "enable_reservation_changes": False},
        })
        prices = self.PRICES

        # 1. Active, assigned reservation whose provisioned baseline (200) & autoscale (200 -> 400 max)
        #    already match 180d telemetry -> emits a $0 PROPERLY_SIZED W-03 scorecard (not None!)
        matching_profile = {
            "p50_all": 225.0,
            "p95_all": 340.0,
            "p50_busy": 230.0,
            "p99_busy": 380.0,
            "peak": 395.0,
            "busy_minutes": 40_000.0,
            "total_minutes": 43_200.0,
            "active_days": 180.0,
            "slot_hours": 162_000.0,
            "autoscale_slot_hours": 185_000.0,
            "autoscale_slot_hours_above_baseline": 18_500.0,
            "current_burst_slot_hours": 18_500.0,
            "timeline": [{"t": "2026-10-20 12:00", "demand": 240.0, "med_busy": 225.0, "busy_mins": 720}],
        }
        row_optimal = {
            "admin_project": "acme-slot-admin",
            "region": "us",
            "reservation_name": "prod-bi-pool",
            "edition": "ENTERPRISE",
            "baseline_slots": 200,
            "autoscale_max_slots": 200,
            "edition_total_baseline_slots": 200,
            "committed_slots": 200,
            "query_assignments": 2,
            "assignment_ids": ["assign_prod_1", "assign_prod_2"],
            "assignees": ["PROJECT:acme-analytics", "PROJECT:acme-bi"],
            "reservation_id": "acme-slot-admin:US.prod-bi-pool",
            "active_days": 180.0,
            "jobs_window": 48_000,
            "jobs_30d": 8_000,
            "slot_ms_30d": 162_000.0 * 3_600_000.0,
            "bytes_billed_30d": 2_500.0 * (1024.0 ** 4),  # On-Demand equiv ~$15.6k/mo > reservation ~$8.4k/mo
            "distinct_projects": 2,
            "projects": ["acme-analytics", "acme-bi"],
        }
        with mock.patch.object(rules, "_slot_profile", return_value=matching_profile):
            f_opt = rules._map_reservation_rightsizing(row_optimal, prices, c)

        self.assertIsNotNone(f_opt)
        self.assertEqual(f_opt["rule_id"], "W-03")
        self.assertEqual(f_opt["gross_monthly_savings_usd"], 0.0)
        self.assertEqual(f_opt["evidence"]["diagnosis_kind"], "PROPERLY_SIZED")
        self.assertTrue(f_opt["proposed_change"]["no_op"])
        self.assertNotIn("generated_ddl_option2", f_opt["proposed_change"])
        self.assertNotIn("REQUIRES_TWO_PERSON_APPROVAL", f_opt["risk_notes"])
        self.assertIn("INFORMATIONAL_PROPERLY_SIZED", f_opt["risk_notes"])
        self.assertEqual(f_opt["proposed_change"]["recommended_baseline_slots"], 200)
        self.assertEqual(f_opt["proposed_change"]["recommended_autoscale_max_slots"], 400)
        self.assertEqual(f_opt["evidence"]["capacity_sizing"]["recommended_baseline_slots"], 200)
        self.assertEqual(f_opt["evidence"]["capacity_sizing"]["recommended_max_slots"], 400)
        self.assertEqual(len(f_opt["evidence"]["capacity_sizing"]["timeline"]), 1)

        # Compiler preserves PROPERLY_SIZED $0 card even when min_card_net_monthly_usd > 0
        scored_opt = scoring.score(f_opt, c, table_spend={}, table_cv={}, rule_history={})
        kept, notes = compiler.split_money_losing([scored_opt], min_net=50.0)
        self.assertEqual(len(kept), 1)
        self.assertEqual(notes, [])
        self.assertTrue(governance.is_finops_card(scored_opt))

        # Executor treats no_op=True as a safe no-op even when enable_reservation_changes=False
        from optimizer import bq as bq_mod
        cs_noop = {
            "change_set_id": "cs-noop-1",
            "rule_ids": ["W-03"],
            "target_project": "acme-slot-admin",
            "target_dataset": "RESERVATION_CAPACITY",
            "target_table": "prod-bi-pool",
            "target_region": "us",
            "state": "APPLYING",
            "proposed_change_json": bq_mod.dumps(f_opt["proposed_change"]),
        }
        with mock.patch("optimizer.bq.execute") as m_exec, mock.patch("optimizer.bq.query") as m_q:
            plan_noop = class3.run(c, cs_noop)
        self.assertTrue(plan_noop["no_op"])
        m_q.assert_not_called()
        # Only _progress state_history note runs — zero ALTER RESERVATION DDL
        self.assertTrue(all("ALTER RESERVATION" not in str(call.args[1]) for call in m_exec.call_args_list))

        # 2. Active, assigned 0-baseline pure-autoscale reservation with HUGE excess cap (0 -> 1000 when 0 -> 150 needed)
        #    and $0 monthly bill delta -> emits $0 AUTOSCALE_CAP_RIGHTSIZING card (not PROPERLY_SIZED)
        spiky_profile = {
            "p50_all": 0.0,
            "p95_all": 35.0,
            "p50_busy": 40.0,
            "p99_busy": 120.0,
            "peak": 135.0,
            "busy_minutes": 4_000.0,
            "total_minutes": 43_200.0,
            "active_days": 180.0,
            "slot_hours": 3_200.0,
            "autoscale_slot_hours": 4_000.0,
            "autoscale_slot_hours_above_baseline": 4_000.0,
            "current_burst_slot_hours": 4_000.0,
            "timeline": [{"t": "2026-10-20 12:00", "demand": 135.0, "med_busy": 40.0, "busy_mins": 120}],
        }
        row_excess_cap = {
            "admin_project": "acme-slot-admin",
            "region": "us",
            "reservation_name": "burst-etl-pool",
            "edition": "ENTERPRISE",
            "baseline_slots": 0,
            "autoscale_max_slots": 1000,
            "edition_total_baseline_slots": 0,
            "committed_slots": 0,
            "query_assignments": 1,
            "assignment_ids": ["assign_etl_1"],
            "assignees": ["PROJECT:acme-etl"],
            "reservation_id": "acme-slot-admin:US.burst-etl-pool",
            "active_days": 180.0,
            "jobs_window": 6_000,
            "jobs_30d": 1_000,
            "slot_ms_30d": 3_200.0 * 3_600_000.0,
            "bytes_billed_30d": 120.0 * (1024.0 ** 4),  # On-Demand ~$750/mo > autoscale ~$240/mo
            "distinct_projects": 1,
            "projects": ["acme-etl"],
        }
        with mock.patch.object(rules, "_slot_profile", return_value=spiky_profile):
            f_cap = rules._map_reservation_rightsizing(row_excess_cap, prices, c)

        self.assertIsNotNone(f_cap)
        self.assertEqual(f_cap["evidence"]["diagnosis_kind"], "AUTOSCALE_CAP_RIGHTSIZING")
        self.assertFalse(f_cap["proposed_change"]["no_op"])
        self.assertEqual(f_cap["proposed_change"]["recommended_baseline_slots"], 0)
        self.assertEqual(f_cap["proposed_change"]["recommended_autoscale_max_slots"], 150)
        self.assertIn("ALTER RESERVATION", f_cap["proposed_change"]["generated_ddl"])

        # 3. Unassigned leftover reservation (query_assignments == 0) with < $10/mo spend (like optimum-graph-demo)
        #    MUST remain suppressed (returns None)
        row_unassigned = {
            **row_excess_cap,
            "reservation_name": "optimum-graph-demo",
            "autoscale_max_slots": 100,
            "query_assignments": 0,
            "assignment_ids": [],
            "assignees": [],
            "jobs_window": 80,
            "jobs_30d": 15,
            "slot_ms_30d": 36.0 * 3_600_000.0,  # ~$2.70/mo
            "bytes_billed_30d": 0.1 * (1024.0 ** 4),
        }
        tiny_profile = {
            **spiky_profile,
            "p99_busy": 12.0,
            "peak": 15.0,
            "slot_hours": 36.0,
            "autoscale_slot_hours": 45.0,
            "autoscale_slot_hours_above_baseline": 45.0,
            "current_burst_slot_hours": 45.0,
        }
        with mock.patch.object(rules, "_slot_profile", return_value=tiny_profile):
            f_unassigned = rules._map_reservation_rightsizing(row_unassigned, prices, c)
        self.assertIsNone(f_unassigned)


if __name__ == "__main__":
    unittest.main()
