"""Unit tests for the pure-logic modules (no GCP deps needed)."""
import unittest

from optimizer import compiler, scoring


def finding(rule="C1-01", cls=1, target=("p", "d", "t"), **kw):
    f = {"rule_id": rule, "apply_class": cls, "source": "CUSTOM_RULE",
         "target_project": target[0], "target_dataset": target[1], "target_table": target[2],
         "gross_monthly_savings_usd": 100.0, "observation_days": 30,
         "proposed_change": {}, "confidence_hint": 0.8}
    f.update(kw)
    return f


CFG = {"amortization_months": 12, "risk_weights": {1: 1.0, 2: 1.3, 3: 2.0, 4: 1.5}}


class TestScoring(unittest.TestCase):
    def test_summation_cap_limits_native_estimates(self):
        # A $5,000 native claim on a table that only spends $400/mo is capped at
        # spend x NATIVE_CAP_SCAN_REDUCTION (400 x 0.68 = 272) — never above spend.
        f = finding(source="NATIVE_RECOMMENDER", gross_monthly_savings_usd=5000.0)
        d = scoring.apply_summation_cap(f, {("p", "d", "t"): 400.0})
        self.assertEqual(f["gross_monthly_savings_usd"], 272.0)
        self.assertLessEqual(f["gross_monthly_savings_usd"], 400.0)
        self.assertEqual(d, 0.5)
        self.assertIn("NATIVE_ESTIMATE_CAPPED_AT_TABLE_SPEND", f["risk_notes"])

    def test_score_math(self):
        f = finding(one_time_apply_cost_usd=120.0)  # amortized 10/mo
        scoring.score(f, CFG, table_spend={}, table_cv={}, rule_history={})
        self.assertAlmostEqual(f["net_monthly_value_usd"], 90.0)
        self.assertGreater(f["score"], 0)
        self.assertLessEqual(f["confidence"], 1.0)

    def test_zero_gross_guardrail_scores_zero(self):
        f = finding(rule="C1-02", gross_monthly_savings_usd=0.0)
        scoring.score(f, CFG, table_spend={}, table_cv={}, rule_history={})
        self.assertEqual(f["score"], 0.0)

    def test_history_clamped(self):
        conf, factors = scoring.confidence(finding(), table_cv={}, rule_history={"C1-01": 9.0},
                                           d_summation=1.0)
        self.assertEqual(factors["d_history"], 1.25)


class TestCompiler(unittest.TestCase):
    def test_partition_and_cluster_merge_into_one_rebuild(self):
        fs = [finding("C3-01", 3, proposed_change={"action": "REPARTITION", "partition_column": "dt"},
                      gross_monthly_savings_usd=200.0),
              finding("C1-01", 1, proposed_change={"action": "SET_CLUSTERING",
                                                   "cluster_columns": ["cust"]},
                      gross_monthly_savings_usd=50.0)]
        out, notes = compiler.compile(fs, open_sets=[])
        self.assertEqual(len(out), 1)
        cs = out[0]
        self.assertEqual(sorted(cs["rule_ids"]), ["C1-01", "C3-01"])
        self.assertEqual(cs["proposed_change"]["cluster_columns"], ["cust"])
        self.assertEqual(cs["gross_monthly_savings_usd"], 250.0)

    def test_dedupe_against_open_sets(self):
        open_sets = [{"target_project": "p", "target_dataset": "d", "target_table": "t",
                      "rule_ids": ["C1-01"], "apply_class": 1, "state": "PENDING_REVIEW"}]
        out, _ = compiler.compile([finding("C1-01")], open_sets)
        self.assertEqual(out, [])

    def test_open_rebuild_supersedes_new_class1(self):
        open_sets = [{"target_project": "p", "target_dataset": "d", "target_table": "t",
                      "rule_ids": ["C3-01"], "apply_class": 3, "state": "APPROVED"}]
        out, notes = compiler.compile([finding("C1-02")], open_sets)
        self.assertEqual(out, [])
        self.assertTrue(any("SUPERSEDED_BY_OPEN_REBUILD" in n for n in notes))

    def test_internal_backup_tables_suppressed(self):
        backup_finding = finding("C3-03", 3, target=("p", "d", "fact_web_visits__bqopt_bak_202609101641"))
        out, notes = compiler.compile([backup_finding], open_sets=[])
        self.assertEqual(out, [])
        self.assertTrue(any("SUPPRESSED_INTERNAL_TABLE" in n for n in notes))


class TestAntiPatterns(unittest.TestCase):
    def test_select_star_detection(self):
        from optimizer import rules
        cfg = {"project_id": "p", "location": "US"}
        row = {"query_hash": "q1", "sample_preview": "SELECT * FROM dataset.table", "est_on_demand_usd": 10.0, "executions": 5}
        findings = rules._map_c4_antipatterns(row, {}, cfg)
        self.assertTrue(any(f["rule_id"] == "C4-01" for f in findings))

    def test_cross_join_detection(self):
        from optimizer import rules
        cfg = {"project_id": "p", "location": "US"}
        row = {"query_hash": "q2", "sample_preview": "SELECT a.id FROM t1 a CROSS JOIN t2 b", "est_on_demand_usd": 20.0, "executions": 10}
        findings = rules._map_c4_antipatterns(row, {}, cfg)
        self.assertTrue(any(f["rule_id"] == "C4-05" for f in findings))

    def test_function_wrapped_date_detection(self):
        from optimizer import rules
        cfg = {"project_id": "p", "location": "US"}
        row = {"query_hash": "q3", "sample_preview": "SELECT id FROM t1 WHERE DATE(created_at) = '2026-08-01'", "est_on_demand_usd": 15.0, "executions": 8}
        findings = rules._map_c4_antipatterns(row, {}, cfg)
        self.assertTrue(any(f["rule_id"] == "C4-03" for f in findings))

    def test_self_join_detection(self):
        from optimizer import rules
        cfg = {"project_id": "p", "location": "US"}
        row = {"query_hash": "q4", "sample_preview": "SELECT a.id, b.id FROM dataset.orders a JOIN dataset.orders b ON a.cust = b.cust", "est_on_demand_usd": 30.0, "executions": 12}
        findings = rules._map_c4_antipatterns(row, {}, cfg)
        self.assertTrue(any(f["rule_id"] == "C4-04" for f in findings))

    def test_count_distinct_detection(self):
        from optimizer import rules
        cfg = {"project_id": "p", "location": "US"}
        row = {"query_hash": "q5", "sample_preview": "SELECT user_id, COUNT(DISTINCT session_id) FROM events GROUP BY 1", "est_on_demand_usd": 25.0, "executions": 15}
        findings = rules._map_c4_antipatterns(row, {}, cfg)
        self.assertTrue(any(f["rule_id"] == "C4-07" for f in findings))


class TestNewRuleMappers(unittest.TestCase):
    def setUp(self):
        self.cfg = {"project_id": "test-p", "location": "US"}
        self.prices = {
            "p_log_active": "0.02",
            "p_log_lt": "0.01",
            "active_physical_gib_usd": "0.04",
            "on_demand_usd_per_tib": "6.25",
            "slot_hour_usd_enterprise": "0.06",
        }

    def test_time_travel_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "dataset_id": "ds", "table_id": "tbl", "time_travel_physical_bytes": 10 * 1024**3}
        f = rules._map_time_travel(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "C1-04")
        self.assertEqual(f["apply_class"], 1)
        self.assertEqual(f["proposed_change"]["action"], "SET_TIME_TRAVEL_WINDOW")

    def test_adaptive_opts_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "complex_queries": 20, "slot_ms_30d": 10000000}
        f = rules._map_adaptive_opts(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "C1-06")
        self.assertEqual(f["apply_class"], 1)
        self.assertEqual(f["proposed_change"]["action"], "ENABLE_ADAPTIVE_OPTIMIZATION")

    def test_editions_fit_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "total_bytes_billed_30d": 200 * 1024**4, "total_slot_ms_30d": 50000000}
        f = rules._map_editions_fit(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "W-01")
        self.assertEqual(f["apply_class"], 3)
        self.assertEqual(f["proposed_change"]["action"], "CAPACITY_PRICING_MIGRATION")

    def test_zombie_dts_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "dataset_id": "ds", "table_id": "tbl", "write_jobs": 10, "bytes_billed": 5 * 1024**4, "owner_email": "a@b.com"}
        f = rules._map_zombie_dts(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "C1-08")
        self.assertEqual(f["apply_class"], 1)
        self.assertEqual(f["proposed_change"]["action"], "PAUSE_SCHEDULED_QUERY")

    def test_pk_fk_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "dataset_id": "ds", "table_id": "tbl", "join_queries": 25, "slot_ms_30d": 10000000}
        f = rules._map_pk_fk(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "C1-10")
        self.assertEqual(f["apply_class"], 1)
        self.assertEqual(f["proposed_change"]["action"], "ADD_PK_FK_CONSTRAINT")

    def test_search_index_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "dataset_id": "ds", "table_id": "tbl", "total_logical_bytes": 10 * 1024**3, "lookup_queries": 20, "bytes_billed": 2 * 1024**4}
        f = rules._map_search_index(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "C2-02")
        self.assertEqual(f["apply_class"], 2)
        self.assertEqual(f["proposed_change"]["action"], "CREATE_SEARCH_INDEX")

    def test_partition_granularity_mapper(self):
        from optimizer import rules
        row = {"project_id": "test-p", "dataset_id": "ds", "table_id": "tbl", "partition_count": 2000, "avg_partition_bytes": 10 * 1024**2}
        f = rules._map_partition_granularity(row, self.prices, self.cfg)
        self.assertEqual(f["rule_id"], "C3-03")
        self.assertEqual(f["apply_class"], 3)
        self.assertEqual(f["proposed_change"]["action"], "REPARTITION")


class TestRollbackSnooze(unittest.TestCase):
    def test_rejection_snooze_days_categories(self):
        from optimizer.store import REJECTION_SNOOZE_DAYS
        for cat in ["REGRESSION_PERFORMANCE", "REGRESSION_COST", "PIPELINE_BREAK", "DATA_MISMATCH", "MANUAL_REVERT"]:
            self.assertIn(cat, REJECTION_SNOOZE_DAYS)
            self.assertEqual(REJECTION_SNOOZE_DAYS[cat], 90)

    def test_dedupe_against_rolled_back_snoozed_sets(self):
        # Open sets includes rolled-back tables that have an active snooze
        open_sets = [{"target_project": "p", "target_dataset": "d", "target_table": "t",
                      "rule_ids": ["C3-01"], "apply_class": 3, "state": "ROLLED_BACK"}]
        out, notes = compiler.compile([finding("C3-01")], open_sets)
        self.assertEqual(out, [])

    def test_google_ast_rewrites_and_map(self):
        from optimizer import rules
        self.assertEqual(rules._AST_PATTERN_MAP["CTEsEvalMultipleTimes"]["rule_id"], "C4-AST-MULTI-CTE")
        self.assertEqual(rules._AST_PATTERN_MAP["LatestRecordWithAnalyticFun"]["rule_id"], "C4-AST-ANALYTIC-LATEST")
        rewrite = rules._synthesize_ast_rewrite(
            "CTEsEvalMultipleTimes",
            "WITH cte AS (SELECT * FROM t) SELECT * FROM cte a JOIN cte b ON a.id=b.id",
            "CTE with multiple references: alias cte defined at line 1 is referenced 2 times."
        )
        self.assertIn("CREATE TEMP TABLE tmp_cte", rewrite)


if __name__ == "__main__":
    unittest.main()


