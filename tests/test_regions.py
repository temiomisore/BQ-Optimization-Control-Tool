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


if __name__ == "__main__":
    unittest.main()
