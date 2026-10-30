"""Unit tests for multi-region collection + automatic region discovery (no GCP calls)."""
import datetime as dt
import os
import unittest

from optimizer import bq, regions, remote_collector as rc
from optimizer.config import Config


def cfg(**kw):
    base = {"project_id": "p1", "ops_dataset": "optimizer_ops", "location": "US",
            "regions": "auto", "regions_include": [], "regions_exclude": []}
    base.update(kw)
    return Config(base)


class TestRegionNames(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(regions.normalize("US"), "us")
        self.assertEqual(regions.normalize("region-us-east4"), "us-east4")
        self.assertEqual(regions.normalize(" us-central1 "), "us-central1")
        self.assertIsNone(regions.normalize("bad region!"))
        self.assertIsNone(regions.normalize("auto"))
        self.assertIsNone(regions.normalize(None))

    def test_location_for(self):
        self.assertEqual(bq.location_for("us"), "US")
        self.assertEqual(bq.location_for("eu"), "EU")
        self.assertEqual(bq.location_for("region-us-east4"), "us-east4")
        self.assertEqual(bq.location_for("US-CENTRAL1"), "us-central1")
        self.assertEqual(bq.location_for("auto"), bq.AUTO)
        self.assertIsNone(bq.location_for(None))
        self.assertIsNone(bq.location_for(""))

    def test_is_auto(self):
        self.assertTrue(regions.is_auto(cfg(regions="auto")))
        self.assertTrue(regions.is_auto(cfg(regions=["auto", "us-east4"])))
        self.assertFalse(regions.is_auto(cfg(regions=["us"])))


class TestRoute(unittest.TestCase):
    def test_auto_uses_discovery_plus_include_minus_exclude_home_first(self):
        c = cfg(regions_include=["europe-west1"], regions_exclude=["us-central1"])
        route = regions.resolve(c, ["us-central1", "us-east4", "us"])
        self.assertEqual(route, ["us", "europe-west1", "us-east4"])

    def test_auto_falls_back_to_last_known_when_discovery_failed(self):
        self.assertEqual(regions.resolve(cfg(), None, ["us-east4"]), ["us", "us-east4"])

    def test_home_always_visited_even_if_not_discovered(self):
        self.assertEqual(regions.resolve(cfg(), ["us-east4"]), ["us", "us-east4"])

    def test_home_can_be_excluded_explicitly(self):
        self.assertEqual(regions.resolve(cfg(regions_exclude=["us"]), ["us", "us-east4"]), ["us-east4"])

    def test_list_mode_ignores_discovery(self):
        c = cfg(regions=["us", "US-EAST4"])
        self.assertEqual(regions.resolve(c, ["asia-south1"]), ["us", "us-east4"])

    def test_non_us_home(self):
        c = cfg(location="us-east4", regions=["us-east4"])
        self.assertEqual(regions.home_region(c), "us-east4")
        self.assertEqual(regions.resolve(c), ["us-east4"])


class TestDiscoveryHelpers(unittest.TestCase):
    def test_aggregate_assets(self):
        results = [{"location": "US", "project": "projects/1"},
                   {"location": "us", "project": "projects/2"},
                   {"location": "us-central1", "project": "projects/1"},
                   {"location": None, "project": "projects/3"}]
        self.assertEqual(regions.aggregate_assets(results),
                         {"us": {"datasets": 2, "projects": 2},
                          "us-central1": {"datasets": 1, "projects": 1}})

    def test_first_run_flags_nothing_then_flags_new_regions(self):
        self.assertEqual(regions.new_regions([], ["us", "us-central1"]), [])
        self.assertEqual(regions.new_regions(["us"], ["us", "europe-west1"]), ["europe-west1"])

    def test_env_override(self):
        from optimizer.config import cfg as load
        old = {k: os.environ.get(k) for k in ("BQOPT_REGIONS", "GOOGLE_CLOUD_PROJECT", "BQOPT_ASSET_SCOPE")}
        try:
            os.environ.update({"BQOPT_REGIONS": "us, us-east4", "GOOGLE_CLOUD_PROJECT": "p1",
                               "BQOPT_ASSET_SCOPE": "organizations/42"})
            c = load(path="/nonexistent.yaml")
            self.assertEqual(c["regions"], ["us", "us-east4"])
            self.assertEqual(c["asset_scope"], "organizations/42")
            os.environ["BQOPT_REGIONS"] = "AUTO"
            self.assertEqual(load(path="/nonexistent.yaml")["regions"], "auto")
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class TestRemoteCollectorPure(unittest.TestCase):
    def test_windows_cover_range_without_overlap(self):
        s = dt.datetime(2026, 11, 1, 5, tzinfo=dt.timezone.utc)
        e = s + dt.timedelta(days=2, hours=3)
        w = rc.windows(s, e)
        self.assertEqual(len(w), 3)
        self.assertEqual(w[0][0], s)
        self.assertEqual(w[-1][1], e)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(w, w[1:])))

    def test_fetch_start(self):
        now = dt.datetime(2026, 11, 3, 1, tzinfo=dt.timezone.utc)
        wm = dt.datetime(2026, 11, 2, 0, 40, tzinfo=dt.timezone.utc)
        self.assertEqual(rc.fetch_start(wm, now, 180), wm - dt.timedelta(days=1))   # watermark - overlap
        self.assertEqual(rc.fetch_start(None, now, 30), now - dt.timedelta(days=30))  # first run
        old = now - dt.timedelta(days=400)
        self.assertEqual(rc.fetch_start(old, now, 180), now - dt.timedelta(days=180))  # retention floor

    def test_jobs_sql_variants(self):
        org = rc.jobs_sql("us-central1", "JOBS_BY_ORGANIZATION")
        self.assertIn("`region-us-central1`.INFORMATION_SCHEMA.JOBS_BY_ORGANIZATION", org)
        self.assertIn("CAST(NULL AS STRING) AS query_preview", org)
        self.assertIn("'us-central1' AS region", org)
        proj = rc.jobs_sql("us-east4", "JOBS")
        self.assertIn("SUBSTR(query, 1, 1024) AS query_preview", proj)
        self.assertIn("dml_statistics.inserted_row_count", proj)
        self.assertIn("TIMESTAMP(@start)", proj)

    def test_slots_sql_uses_real_values(self):
        sql = rc.slots_sql("us-central1", "JOBS")
        self.assertIn("SUM(period_slot_ms) AS total_slot_ms", sql)
        self.assertNotIn("FARM_FINGERPRINT", sql)   # no synthetic demo floor for remote regions

    def test_table_state_without_storage_keeps_columns(self):
        with_s, without = rc.table_state_sql("us-central1", True), rc.table_state_sql("us-central1", False)
        self.assertIn("TABLE_STORAGE", with_s)
        self.assertNotIn("TABLE_STORAGE", without)
        for col in ("total_logical_bytes", "storage_last_modified", "run_id", "table_created"):
            self.assertIn(col, without)

    def test_rejects_invalid_and_home_region(self):
        with self.assertRaises(ValueError):
            rc.collect_region(cfg(), "us", "run")
        with self.assertRaises(ValueError):
            rc.collect_region(cfg(), "not a region!", "run")


class TestConform(unittest.TestCase):
    def test_nested_and_repeated_values_become_json(self):
        from google.cloud.bigquery import SchemaField
        ts = dt.datetime(2026, 11, 2, 9, tzinfo=dt.timezone.utc)
        schema = [
            SchemaField("creation_time", "TIMESTAMP"),
            SchemaField("labels", "RECORD", mode="REPEATED",
                        fields=[SchemaField("key", "STRING"), SchemaField("value", "STRING")]),
            SchemaField("destination_table", "RECORD",
                        fields=[SchemaField("project_id", "STRING"), SchemaField("table_id", "STRING")]),
        ]
        row = {"creation_time": ts, "labels": None, "destination_table": {"project_id": "p", "table_id": "t"},
               "not_in_schema": 1}
        out = {f.name: bq._conform(row.get(f.name), f) for f in schema}
        self.assertEqual(out["creation_time"], ts.isoformat())
        self.assertEqual(out["labels"], [])
        self.assertEqual(out["destination_table"], {"project_id": "p", "table_id": "t"})


class TestCrossProjectDiscoveryAndCollection(unittest.TestCase):
    def test_extract_project_id_ignores_numeric_project_number(self):
        # Cloud Asset Inventory's `project` field is numeric ("projects/377289738115");
        # the string project ID must come from `name` or `parentFullResourceName`.
        res_name = {
            "location": "US",
            "name": "//bigquery.googleapis.com/projects/analytics-prod-01/datasets/orders",
            "parentFullResourceName": "//cloudresourcemanager.googleapis.com/projects/analytics-prod-01",
            "project": "projects/377289738115",
        }
        self.assertEqual(regions.extract_project_id(res_name), "analytics-prod-01")
        res_parent = {
            "location": "us-east4",
            "parentFullResourceName": "//cloudresourcemanager.googleapis.com/projects/altostrat.com:fin-mart",
            "project": "projects/999888777",
        }
        self.assertEqual(regions.extract_project_id(res_parent), "altostrat.com:fin-mart")
        self.assertIsNone(regions.extract_project_id({"location": "US", "project": "projects/377289738115"}))

    def test_projects_by_region(self):
        results = [
            {"location": "US", "name": "//bigquery.googleapis.com/projects/proj-b/datasets/d1",
             "project": "projects/222"},
            {"location": "us", "name": "//bigquery.googleapis.com/projects/proj-a/datasets/d2",
             "project": "projects/111"},
            {"location": "US", "name": "//bigquery.googleapis.com/projects/proj-b/datasets/d3",
             "project": "projects/222"},
            {"location": "us-east4", "name": "//bigquery.googleapis.com/projects/proj-c/datasets/d4",
             "project": "projects/333"},
        ]
        self.assertEqual(regions.projects_by_region(results),
                         {"us": ["proj-a", "proj-b"], "us-east4": ["proj-c"]})

    def test_project_qualified_sql_builders(self):
        ts = rc.table_state_sql("us-east4", True, project="proj-b")
        self.assertIn("`proj-b.region-us-east4`.INFORMATION_SCHEMA.TABLES", ts)
        self.assertIn("`proj-b.region-us-east4`.INFORMATION_SCHEMA.TABLE_OPTIONS", ts)
        self.assertIn("`proj-b.region-us-east4`.INFORMATION_SCHEMA.TABLE_STORAGE", ts)
        self.assertIn("'optimizer_ops'", ts)
        self.assertIn("`proj-b.region-us-east4`.INFORMATION_SCHEMA.COLUMNS",
                      rc.columns_sql("us-east4", project="proj-b"))
        self.assertIn("`proj-b.region-us-east4`.INFORMATION_SCHEMA.SCHEMATA",
                      rc.datasets_sql("us-east4", project="proj-b"))
        self.assertIn("`proj-b.region-us-east4`.INFORMATION_SCHEMA.VIEWS",
                      rc.objects_sql("us-east4", "VIEW", project="proj-b"))
        self.assertIn("`region-us-east4`.INFORMATION_SCHEMA.TABLE_STORAGE_BY_ORGANIZATION",
                      rc.org_storage_sql("us-east4"))
        with self.assertRaises(ValueError):
            rc.table_state_sql("us-east4", True, project="377289738115")  # numeric project number rejected

    def test_projects_for_region_modes_and_fallback(self):
        from unittest import mock
        # Single-project scope -> host project only
        c_single = cfg(project_id="host-proj", asset_scope="projects/host-proj")
        self.assertEqual(rc.projects_for_region(c_single, "us",
                                                {"projects_by_region": {"us": ["host-proj", "other-proj"]}}),
                         ["host-proj"])
        # Dataset-scoped -> host project only
        c_ds = cfg(project_id="host-proj", asset_scope="organizations/42", target_dataset="only_ds")
        self.assertEqual(rc.projects_for_region(c_ds, "us",
                                                {"projects_by_region": {"us": ["host-proj", "other-proj"]}}),
                         ["host-proj"])
        # Org scope with Cloud Asset Inventory discovery -> host first, then discovered projects
        c_org = cfg(project_id="host-proj", asset_scope="organizations/42")
        self.assertEqual(rc.projects_for_region(c_org, "us-east4",
                                                {"projects_by_region": {"us-east4": ["proj-b", "host-proj", "proj-c"]}}),
                         ["host-proj", "proj-b", "proj-c"])
        # Org scope fallback to jobs_events when Cloud Asset Inventory wasn't available
        with mock.patch.object(rc.bq, "query", return_value=[{"pid": "proj-x"}, {"pid": "12345"}, {"pid": "host-proj"}]):
            self.assertEqual(rc.projects_for_region(c_org, "us-east4", {"projects_by_region": {}}),
                             ["host-proj", "proj-x"])

    def test_multi_project_table_state_org_storage_and_fault_isolation(self):
        from unittest import mock
        c = cfg(project_id="p1", asset_scope="organizations/42")
        captured = {}

        def fake_q(_c, sql, params=None, region=None):
            if "TABLE_STORAGE_BY_ORGANIZATION" in sql:
                return [
                    {"project_id": "p1", "table_schema": "ds1", "table_name": "t1",
                     "total_rows": 100, "total_logical_bytes": 4096},
                    {"project_id": "p2", "table_schema": "ds2", "table_name": "t2",
                     "total_rows": 500, "total_logical_bytes": 65536},
                ]
            if "`p3.region-us-east4`" in sql:
                raise RuntimeError("403 Access Denied: Project p3 lacks metadataViewer")
            if "`p1.region-us-east4`" in sql:
                return [{"project_id": "p1", "dataset_id": "ds1", "table_id": "t1", "total_logical_bytes": None}]
            if "`p2.region-us-east4`" in sql:
                return [{"project_id": "p2", "dataset_id": "ds2", "table_id": "t2", "total_logical_bytes": None}]
            return []

        def fake_replace(_c, region, table, rows, kinds=None, exclude_project=None):
            captured["rows"] = rows
            captured["exclude_project"] = exclude_project
            return len(rows)

        with mock.patch.object(rc, "_q", fake_q), mock.patch.object(rc, "_replace_today", fake_replace):
            n, note = rc.collect_table_state(c, "us-east4", "r1", projects=["p1", "p2", "p3"])

        self.assertEqual(n, 2)
        self.assertIsNone(captured["exclude_project"])
        by_proj = {r["project_id"]: r for r in captured["rows"]}
        self.assertEqual(by_proj["p1"]["total_logical_bytes"], 4096)
        self.assertEqual(by_proj["p2"]["total_logical_bytes"], 65536)
        self.assertIn("2/3 project(s) scanned", note)
        self.assertIn("org TABLE_STORAGE", note)
        self.assertIn("skipped 1: p3", note)

    def test_collect_extra_projects_home_preserves_host_project(self):
        from unittest import mock
        from google.cloud.bigquery import SchemaField
        c = cfg(project_id="p1", asset_scope="organizations/42")
        executed_sql = []

        def fake_q(_c, sql, params=None, region=None):
            if "TABLE_STORAGE_BY_ORGANIZATION" in sql:
                raise RuntimeError("403 org storage disabled")
            if "`p2.region-us`.INFORMATION_SCHEMA.TABLE_STORAGE" in sql:
                raise RuntimeError("TABLE_STORAGE view hasn't been enabled")
            if "`p2.region-us`.INFORMATION_SCHEMA.TABLES" in sql:
                return [{"project_id": "p2", "dataset_id": "sales", "table_id": "orders", "total_logical_bytes": None}]
            if "`p2.region-us`.INFORMATION_SCHEMA.COLUMNS" in sql:
                return [{"project_id": "p2", "dataset_id": "sales", "table_id": "orders", "column_name": "dt"}]
            if "`p2.region-us`.INFORMATION_SCHEMA.SCHEMATA" in sql and "SCHEMATA_OPTIONS" not in sql:
                return [{"schema_name": "sales"}]
            if "`p2.sales.INFORMATION_SCHEMA.PARTITIONS`" in sql:
                return [{"project_id": "p2", "dataset_id": "sales", "table_id": "orders", "partition_id": "20261030"}]
            if "`p2.region-us`.INFORMATION_SCHEMA.SCHEMATA" in sql:
                return [{"project_id": "p2", "dataset_id": "sales", "location": "US"}]
            if "`p2.region-us`.INFORMATION_SCHEMA.VIEWS" in sql:
                return [{"project_id": "p2", "dataset_id": "sales", "object_id": "v_orders"}]
            return []

        def fake_execute(_c, sql, params=None, location=None):
            executed_sql.append((sql, params))
            return 1

        with mock.patch.object(rc, "_q", fake_q), \
                mock.patch.object(rc, "_schema", lambda *_a, **_k: [SchemaField("project_id", "STRING")]), \
                mock.patch.object(rc.bq, "load_rows", lambda *_a, **_k: None), \
                mock.patch.object(rc, "_drop", lambda *_a, **_k: None), \
                mock.patch.object(rc.bq, "execute", fake_execute):
            res = rc.collect_extra_projects_home(c, "us", ["p2"], "run-123")

        self.assertEqual(res["3_table_state_xproj"], 1)
        self.assertEqual(res["4_columns_xproj"], 1)
        self.assertEqual(res["5_partitions_xproj"], 1)
        self.assertEqual(res["6_dataset_state_xproj"], 1)
        self.assertEqual(res["7_objects_xproj"], 1)
        tx_calls = [(s, p) for s, p in executed_sql if "DELETE FROM" in s]
        self.assertEqual(len(tx_calls), 5)
        for sql, params in tx_calls:
            self.assertIn("project_id != @excl_proj", sql)
            self.assertEqual(params["excl_proj"], "p1")

    def test_driver_backfills_storage_billing_model_across_projects(self):
        from unittest import mock
        from optimizer import collector_driver
        c = cfg(project_id="p1")
        updates = []

        class FakeDs:
            def __init__(self, model):
                self.storage_billing_model = model

        class FakeCli:
            def get_dataset(self, ref):
                if ref == "p3.locked":
                    raise RuntimeError("403 Access Denied")
                return FakeDs("PHYSICAL" if ref == "p2.mart" else "LOGICAL")

        def fake_exec(_c, sql, params=None):
            updates.append((sql, params))
            return len(params["fqns"])

        with mock.patch.object(collector_driver.bq, "client", return_value=FakeCli()), \
                mock.patch.object(collector_driver.bq, "query", return_value=[
                    {"project_id": "p1", "dataset_id": "core"},
                    {"project_id": "p2", "dataset_id": "mart"},
                    {"project_id": "p3", "dataset_id": "locked"},
                ]), \
                mock.patch.object(collector_driver.bq, "execute", fake_exec):
            n = collector_driver.backfill_storage_billing_model(c)

        self.assertEqual(n, 2)
        by_model = {p["m"]: p["fqns"] for _, p in updates}
        self.assertEqual(by_model, {"LOGICAL": ["p1.core"], "PHYSICAL": ["p2.mart"]})

    def test_query_previews_sql_and_hydration_fault_isolation(self):
        from unittest import mock
        sql = rc.query_previews_sql("us-east4", "exec-proj-01")
        self.assertIn("`exec-proj-01.region-us-east4`.INFORMATION_SCHEMA.JOBS_BY_PROJECT", sql)
        self.assertIn("SUBSTR(query, 1, 1024)", sql)

        # No-op when jobs_view already includes SQL text
        c_proj = cfg(project_id="p1", jobs_view="JOBS")
        self.assertEqual(rc.hydrate_query_previews(c_proj, "us", "r1")[0], 0)

        # First hydration (hydrated_90d == 0) scans 90d across compute-only projects too, isolating 403s
        c_org = cfg(project_id="p1", jobs_view="JOBS_BY_ORGANIZATION", asset_scope="organizations/42")
        queried_projs = []
        executed_updates = []

        def fake_bq_query(_c, q, params=None, location=None):
            if "missing_projs_90d" in q:
                return [{
                    "hydrated_90d": 0,
                    "missing_projs_90d": ["p1", "compute-only-proj", "locked-proj"],
                    "missing_projs_inc": ["p1"],
                }]
            return []

        def fake_q(_c, q, params=None, region=None):
            proj = params["proj"]
            queried_projs.append((proj, params["days"], region))
            if proj == "locked-proj":
                raise RuntimeError("403 Access Denied on locked-proj")
            return [{"project_id": proj, "query_hash": f"h_{proj}", "query_preview": f"SELECT * FROM {proj}.d.t"}]

        def fake_exec(_c, q, params=None, location=None):
            executed_updates.append((q, params))
            return 42

        with mock.patch.object(rc.bq, "query", fake_bq_query), \
                mock.patch.object(rc, "_q", fake_q), \
                mock.patch.object(rc.bq, "load_rows", lambda *_a, **_k: 2), \
                mock.patch.object(rc, "_drop", lambda *_a, **_k: None), \
                mock.patch.object(rc.bq, "execute", fake_exec):
            n, note = rc.hydrate_query_previews(c_org, "us", "run-1", projects=["p1"])

        self.assertEqual(n, 42)
        self.assertEqual([p for p, _, _ in queried_projs], ["p1", "compute-only-proj", "locked-proj"])
        self.assertTrue(all(days == 90 for _, days, _ in queried_projs))
        self.assertIn("2/3 project(s) (90d)", note)
        self.assertIn("skipped 1: locked-proj", note)
        self.assertEqual(len(executed_updates), 1)
        self.assertEqual(executed_updates[0][1]["days"], 90)

    def test_collector_sql_hourly_slots_has_no_ungrouped_period_start(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "sql", "02_collector_run.sql"), encoding="utf-8") as f:
            collector_sql = f.read()
        self.assertNotIn("FARM_FINGERPRINT(CAST(TIMESTAMP_TRUNC(period_start", collector_sql)
        self.assertIn("SUM(period_slot_ms) AS total_slot_ms", collector_sql)

    def test_cross_project_schema_context_and_stub_dry_run(self):
        from unittest import mock
        from optimizer import rules

        c = cfg(project_id="host-proj")
        sql = """
        WITH helper AS (SELECT 1 AS x)
        SELECT o.order_id, h.x
        FROM helper h
        JOIN analytics_ds.orders o ON TRUE
        LEFT JOIN `sibling-proj.core_ds.customers` AS cu ON cu.id = o.cust_id
        """
        qualified = rules._qualify_default_project(sql, "billing-proj")
        self.assertIn("FROM helper h", qualified)  # CTE untouched
        self.assertIn("JOIN `billing-proj.analytics_ds.orders` o", qualified)
        self.assertIn("LEFT JOIN `sibling-proj.core_ds.customers` AS cu", qualified)

        fetched_tables = []

        def fake_bq_query(_c, q, params=None, location=None):
            if "INFORMATION_SCHEMA.TABLES" in q:
                fetched_tables.append((q, params["tbl"]))
                return [{"ddl": f"CREATE TABLE {params['tbl']} (id INT64);"}]
            if "INFORMATION_SCHEMA.COLUMNS" in q:
                tbl = params["tbl"]
                if tbl == "orders":
                    return [{"column_name": "order_id", "data_type": "INT64"},
                            {"column_name": "cust_id", "data_type": "INT64"}]
                if tbl == "customers":
                    return [{"column_name": "id", "data_type": "INT64"}]
            return []

        with mock.patch.object(rules.bq, "query", fake_bq_query):
            ddl = rules._fetch_schema_context_for_sql(c, sql, default_project="billing-proj")
        self.assertIn("CREATE TABLE orders", ddl)
        self.assertIn("CREATE TABLE customers", ddl)
        self.assertTrue(any("billing-proj.analytics_ds" in q for q, _ in fetched_tables))

        # 403 on direct dry-run triggers metadata-only stub dry-run; 400 syntax error fails immediately
        dry_run_queries = []

        class FakeJob:
            total_bytes_processed = 0

        class FakeClient:
            def __init__(self, first_err):
                self.first_err = first_err
                self.calls = 0

            def query(self, q, job_config=None):
                self.calls += 1
                dry_run_queries.append(q)
                if self.calls == 1:
                    raise RuntimeError(self.first_err)
                return FakeJob()

        with mock.patch.object(rules.bq, "query", fake_bq_query), \
                mock.patch.object(rules.bq, "client", return_value=FakeClient("403 Access Denied: Table sibling-proj:core_ds.customers")):
            ok, b = rules._dry_run_sql(c, sql, default_project="billing-proj")
        self.assertTrue(ok)
        self.assertEqual(b, 0)
        self.assertEqual(len(dry_run_queries), 2)
        self.assertIn("UNNEST(ARRAY<INT64>[])", dry_run_queries[1])

        with mock.patch.object(rules.bq, "query", fake_bq_query), \
                mock.patch.object(rules.bq, "client", return_value=FakeClient("400 Syntax error: Unexpected keyword WHERE")):
            ok_bad, _ = rules._dry_run_sql(c, "SELECT FROM WHERE", default_project="billing-proj")
        self.assertFalse(ok_bad)


if __name__ == "__main__":
    unittest.main()
