"""Settings catalog + setup check (no GCP calls: BigQuery and Cloud Asset are stubbed)."""
import os
import re
import tempfile
import unittest
from unittest import mock

import yaml

from optimizer import config, settings_catalog as sc, setup_check as chk

_NESTED = ("governance", "executor", "pricing")


class TestCatalogComplete(unittest.TestCase):
    def test_every_default_is_listed(self):
        keys = {s["key"] for s in sc.SETTINGS}
        for k, v in config._DEFAULTS.items():
            if k in _NESTED:
                for child in v:
                    self.assertIn(f"{k}.{child}", keys, f"{k}.{child} missing from settings_catalog")
            else:
                self.assertIn(k, keys, f"{k} missing from settings_catalog")

    def test_every_env_var_is_listed(self):
        with open(config.__file__) as f:
            src = f.read()
        for var in set(re.findall(r'"(BQOPT_[A-Z_]+)"', src)) - {"BQOPT_CONFIG", "BQOPT_LOOKBACK_DAYS"}:
            self.assertIn(var, sc.ENV_VARS, f"{var} missing from settings_catalog")

    def test_buckets_and_labels(self):
        ids = {b["id"] for b in sc.BUCKETS}
        for s in sc.SETTINGS:
            self.assertIn(s["bucket"], ids)
            self.assertTrue(s["label"] and s["desc"], s["key"])
        self.assertEqual(len({s["key"] for s in sc.SETTINGS}), len(sc.SETTINGS), "duplicate keys")


class TestExplain(unittest.TestCase):
    def _yaml(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_sources_and_secret(self):
        path = self._yaml('project_id: "p"\nregions: ["us", "us-east4"]\nslack_webhook: "https://hooks/x"\n')
        env = {"BQOPT_ASSET_SCOPE": "organizations/42", "BQOPT_FINOPS_APPROVERS": "a@x.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            out = sc.explain(path=path)
        by = {i["key"]: i for i in out["settings"]}
        self.assertEqual(by["regions"]["source"], "config.yaml")
        self.assertEqual(by["regions"]["value"], ["us", "us-east4"])
        self.assertEqual(by["asset_scope"]["source"], "env BQOPT_ASSET_SCOPE")
        self.assertEqual(by["asset_scope"]["value"], "organizations/42")
        self.assertEqual(by["region_alert_days"]["source"], "default")
        self.assertEqual(by["governance.finops_approvers"]["value"], ["a@x.com"])
        self.assertEqual(by["slack_webhook"]["value"], "set (hidden)")
        self.assertNotIn("hooks/x", sc.to_yaml(out))

    def test_yaml_export_round_trips(self):
        path = self._yaml('project_id: "p"\nlocation: "US"\nregions: auto\nasset_scope: "organizations/1"\n'
                          'executor:\n  enable_class3: true\n  change_window_utc: [0, 24]\n'
                          'pricing:\n  w01_min_on_demand_tib_30d: 50\nmin_card_net_monthly_usd: 5\n')
        with mock.patch.dict(os.environ, {}, clear=True):
            out = sc.explain(path=path)
            exported = sc.to_yaml(out)
            yaml.safe_load(exported)                       # valid YAML
            again = sc.explain(path=self._yaml(exported))
        before = {i["key"]: i["value"] for i in out["settings"] if not i["secret"]}
        after = {i["key"]: i["value"] for i in again["settings"] if not i["secret"]}
        self.assertEqual(before, after)
        self.assertEqual(after["executor.enable_class3"], True)
        self.assertEqual(after["pricing.w01_min_on_demand_tib_30d"], 50)


class TestSetupCheckHelpers(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(chk.classify("403 Access Denied: Table region-us.INFORMATION_SCHEMA.JOBS"), "denied")
        self.assertEqual(chk.classify("TABLE_STORAGE view hasn't been enabled ... enable_info_schema_storage"),
                         "not_enabled")
        self.assertEqual(chk.classify("403 Cloud Asset API has not been used in project 1 before or it is "
                                      "disabled. SERVICE_DISABLED"), "api_disabled")
        self.assertEqual(chk.classify("404 Not found: Dataset p:optimizer_ops"), "not_found")
        self.assertEqual(chk.classify("boom"), "other")

    def test_commands(self):
        c = config.Config({"project_id": "p", "asset_scope": "organizations/42"})
        self.assertEqual(chk.member("sa@p.iam.gserviceaccount.com"), "serviceAccount:sa@p.iam.gserviceaccount.com")
        self.assertEqual(chk.member("me@x.com"), "user:me@x.com")
        self.assertEqual(chk.org_id(c), "42")
        self.assertEqual(chk.grant("organizations", "42", "me@x.com", "roles/cloudasset.viewer"),
                         "gcloud organizations add-iam-policy-binding 42 --member=user:me@x.com "
                         "--role=roles/cloudasset.viewer")
        self.assertEqual(chk.grant_sql("SCHEMA", "p.optimizer_ops", "me@x.com", "roles/bigquery.dataEditor"),
                         'GRANT `roles/bigquery.dataEditor` ON SCHEMA `p.optimizer_ops` TO "user:me@x.com";')

    def test_summary_ignores_optional_failures(self):
        rows = [chk.result("a", "A", "", chk.OK, ""), chk.result("b", "B", "", chk.FAIL, "", optional=True)]
        self.assertTrue(chk.summarize(rows)["ready"])
        rows.append(chk.result("c", "C", "", chk.FAIL, ""))
        self.assertEqual(chk.summarize(rows)["blocking"], 1)


class _FakeDataset:
    location = "US"


class _FakeTable:
    project, dataset_id, table_id = "p", "hr", "people"


class _FakeClient:
    _credentials = None

    def get_dataset(self, ref):
        return _FakeDataset()

    def test_iam_permissions(self, table, perms):
        return {"permissions": list(perms)}

    def get_table(self, fqn):
        return _FakeTable()


class TestRunChecks(unittest.TestCase):
    def test_orchestration(self):
        c = config.cfg(path="/nonexistent.yaml", project_id="p", regions="auto",
                       asset_scope="organizations/42", employee_hierarchy_table="hr.people")

        def fake_query(_c, sql, params=None, location=None):
            if "SESSION_USER" in sql:
                return [{"who": "sa@p.iam.gserviceaccount.com"}]
            if "TABLE_STORAGE" in sql and location == "us-central1":
                raise RuntimeError("TABLE_STORAGE view hasn't been enabled. enable_info_schema_storage")
            if "_BY_ORGANIZATION" in sql or (".JOBS " in sql and location == "us-central1"):
                raise RuntimeError("403 Access Denied: User does not have bigquery.jobs.listAll")
            return [{"ok": 1}]

        route = (["us", "us-central1"], {"mode": "auto", "source": "asset_inventory", "scope": "organizations/42",
                                         "discovered": {"us": {"datasets": 3}, "us-central1": {"datasets": 1}}})
        with mock.patch.object(chk.bq, "query", fake_query), \
                mock.patch.object(chk.bq, "client", lambda *_a, **_k: _FakeClient()), \
                mock.patch.object(chk.regions, "plan_route", lambda *_a, **_k: route), \
                mock.patch.object(chk, "check_transfers",
                                  lambda c, ctx: chk.result("transfers", "T", "", chk.OK, "", optional=True)):
            rep = chk.run_checks(c)
        by = {x["id"]: x for x in rep["checks"]}
        self.assertEqual(rep["identity"], "sa@p.iam.gserviceaccount.com")
        self.assertEqual(by["run_queries"]["status"], chk.OK)
        self.assertEqual(by["region_discovery"]["status"], chk.OK)
        self.assertEqual(by["table_storage:us-central1"]["status"], chk.WARN)
        self.assertIn("ALTER PROJECT `p`", by["table_storage:us-central1"]["fix"])
        self.assertEqual(by["query_history:us"]["status"], chk.OK)          # jobs_view JOBS (default)
        self.assertEqual(by["query_history:us-central1"]["status"], chk.FAIL)
        self.assertIn("--role=roles/bigquery.resourceViewer", by["query_history:us-central1"]["fix"])
        self.assertEqual(by["table:employee_hierarchy_table"]["status"], chk.OK)
        self.assertEqual(by["cross_project"]["status"], chk.INFO)            # org view denied -> optional
        self.assertFalse(rep["summary"]["ready"])
        self.assertEqual(rep["checks"][0]["id"], "run_queries")              # general checks first
        regions_seen = [x["region"] for x in rep["checks"] if x["region"]]
        self.assertEqual(regions_seen, sorted(regions_seen, key=["us", "us-central1"].index))


if __name__ == "__main__":
    unittest.main()
