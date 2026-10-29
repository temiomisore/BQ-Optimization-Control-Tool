"""Phase 2 settings editing: validation, who may edit, precedence, change log (BigQuery stubbed)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from optimizer import config, governance, settings_catalog as sc, settings_store as st


def _cfg(**gov):
    base = {"project_id": "p", "ops_dataset": "optimizer_ops", "location": "US",
            "governance": {"settings_admins": [], "finops_approvers": [], "trust_client_identity": False}}
    base["governance"].update(gov)
    return config.Config(base)


class TestValidate(unittest.TestCase):
    c = _cfg()

    def test_numbers(self):
        self.assertEqual(sc.validate("region_alert_days", "10", self.c), 10)
        for bad in ("0", "abc", "2.5", "1000"):
            with self.assertRaises(ValueError):
                sc.validate("region_alert_days", bad, self.c)
        self.assertEqual(sc.validate("pricing.reservation_savings_realization", 0.8, self.c), 0.8)
        with self.assertRaises(ValueError):
            sc.validate("pricing.reservation_savings_realization", 1.5, self.c)

    def test_regions(self):
        self.assertEqual(sc.validate("regions", "auto", self.c), "auto")
        self.assertEqual(sc.validate("regions", "US, us-east4 us-east4", self.c), ["us", "us-east4"])
        with self.assertRaises(ValueError):
            sc.validate("regions", "mars!", self.c)
        with self.assertRaises(ValueError):                       # home location can't be skipped
            sc.validate("regions_exclude", "us", self.c)
        self.assertEqual(sc.validate("regions_exclude", "europe-west2", self.c), ["europe-west2"])
        self.assertEqual(sc.validate("regions_include", "", self.c), [])

    def test_enum_bool_table_email(self):
        self.assertEqual(sc.validate("jobs_view", "jobs_by_organization", self.c), "JOBS_BY_ORGANIZATION")
        with self.assertRaises(ValueError):
            sc.validate("jobs_view", "JOBS_BY_MOON", self.c)
        self.assertIs(sc.validate("ai_judge.enabled", "off", self.c), False)
        self.assertEqual(sc.validate("employee_hierarchy_table", "hr-proj.people.hierarchy", self.c),
                         "hr-proj.people.hierarchy")
        self.assertIsNone(sc.validate("employee_hierarchy_table", "  ", self.c))
        with self.assertRaises(ValueError):
            sc.validate("employee_hierarchy_table", "people.hierarchy", self.c)
        with self.assertRaises(ValueError):
            sc.validate("default_owner", "not-an-email", self.c)

    def test_maps_and_weights(self):
        self.assertEqual(sc.validate("employee_hierarchy_columns", "user_email: email\ncost_center: null", self.c),
                         {"user_email": "email", "cost_center": None})
        with self.assertRaises(ValueError):
            sc.validate("employee_hierarchy_columns", "user email: e-mail", self.c)
        self.assertEqual(sc.validate("risk_weights", "1: 1\n2: 1.3\n3: 2\n4: 1.5", self.c),
                         {"1": 1.0, "2": 1.3, "3": 2.0, "4": 1.5})
        with self.assertRaises(ValueError):
            sc.validate("risk_weights", "1: 1\n2: 1.3", self.c)

    def test_locked_and_unknown(self):
        for key in ("executor.enable_class3", "governance.settings_admins", "project_id", "approval_ttl_days"):
            with self.assertRaises(ValueError):
                sc.validate(key, True, self.c)
        with self.assertRaises(ValueError):
            sc.validate("nope", 1, self.c)


class TestAccess(unittest.TestCase):
    def setUp(self):
        self.c = _cfg(settings_admins=["Sayali@X.com"], finops_approvers=["shawn@x.com"])

    def _who(self, email, source=governance.SOURCE_IAP):
        return {"email": email, "source": source}

    def test_admin(self):
        a = sc.access_for(self._who("sayali@x.com"), self.c, [])
        self.assertTrue(a["is_admin"] and a["is_editor"])
        self.assertFalse(a["is_finops"])
        self.assertTrue(sc.can_edit(sc.BY_KEY["regions"], a)[0])
        self.assertTrue(sc.can_edit(sc.BY_KEY["verify_window_days"], a)[0])             # admin-only
        ok, why = sc.can_edit(sc.BY_KEY["min_card_net_monthly_usd"], a)                 # money: FinOps
        self.assertFalse(ok)
        self.assertIn("FinOps", why)
        self.assertFalse(sc.can_edit(sc.BY_KEY["executor.enable_class3"], a)[0])        # safety switch

    def test_editor_and_finops(self):
        a = sc.access_for(self._who("shawn@x.com"), self.c, ["Shawn@x.com"])
        self.assertTrue(a["is_editor"] and a["is_finops"])
        self.assertFalse(a["is_admin"])
        self.assertTrue(sc.can_edit(sc.BY_KEY["min_card_net_monthly_usd"], a)[0])
        self.assertFalse(sc.can_edit(sc.BY_KEY["verify_window_days"], a)[0])

    def test_untrusted_identity_gets_nothing(self):
        a = sc.access_for(self._who("sayali@x.com", governance.SOURCE_CLIENT), self.c, [])
        self.assertFalse(a["is_admin"] or a["is_editor"])
        self.assertFalse(sc.can_edit(sc.BY_KEY["regions"], a)[0])
        demo = _cfg(settings_admins=["sayali@x.com"], trust_client_identity=True)
        a = sc.access_for(self._who("sayali@x.com", governance.SOURCE_CLIENT), demo, [])
        self.assertTrue(a["is_admin"] and a["demo_identity"])

    def test_env_admins_and_env_pin(self):
        with mock.patch.dict(os.environ, {"BQOPT_SETTINGS_ADMINS": "ops@x.com", "BQOPT_REGIONS": "us"}):
            a = sc.access_for(self._who("ops@x.com"), self.c, [])
            self.assertTrue(a["is_admin"])
            ok, why = sc.can_edit(sc.BY_KEY["regions"], a)
            self.assertFalse(ok)
            self.assertIn("BQOPT_REGIONS", why)


class TestPrecedence(unittest.TestCase):
    def tearDown(self):
        config.use_ui_settings(None)

    def _yaml(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_default_yaml_ui_env(self):
        path = self._yaml('project_id: "p"\nregion_alert_days: 5\nregions: ["us"]\n'
                          'pricing:\n  reservation_savings_realization: 1.0\n  w01_min_on_demand_tib_30d: 7\n')
        config.use_ui_settings(lambda probe: {"region_alert_days": 9, "regions": ["eu"],
                                              "pricing.reservation_savings_realization": 0.5})
        with mock.patch.dict(os.environ, {"BQOPT_REGIONS": "us,us-east4"}, clear=True):
            c = config.cfg(path)
            self.assertEqual(c["region_alert_days"], 9)                    # UI beats config.yaml
            self.assertEqual(c["pricing"]["reservation_savings_realization"], 0.5)
            self.assertEqual(c["pricing"]["w01_min_on_demand_tib_30d"], 7)  # sibling kept
            self.assertEqual(c["regions"], ["us", "us-east4"])             # env beats UI
            self.assertEqual(config.cfg(path, ui=False)["region_alert_days"], 5)

    def test_provider_failure_falls_back(self):
        path = self._yaml('project_id: "p"\nregion_alert_days: 5\n')

        def boom(probe):
            raise RuntimeError("bigquery down")
        config.use_ui_settings(boom)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.cfg(path)["region_alert_days"], 5)

    def test_store_provider_ignores_locked_keys(self):
        saved = {"region_alert_days": {"value": 3}, "executor.enable_class3": {"value": True},
                 st.EDITORS_KEY: {"value": ["a@x.com"]}}
        with mock.patch.object(st, "current", lambda *_a, **_k: saved):
            self.assertEqual(st.provider(_cfg()), {"region_alert_days": 3})


class TestStore(unittest.TestCase):
    def setUp(self):
        st.invalidate()

    def test_current_latest_set_only(self):
        rows = [{"key": "region_alert_days", "action": "SET", "value_json": "10", "changed_by": "a@x.com",
                 "changed_at": None, "note": None},
                {"key": "regions", "action": "RESET", "value_json": None, "changed_by": "a@x.com",
                 "changed_at": None, "note": None},
                {"key": st.EDITORS_KEY, "action": "SET", "value_json": json.dumps(["B@X.com"]),
                 "changed_by": "a@x.com", "changed_at": None, "note": None}]
        with mock.patch.object(st.bq, "query", lambda *_a, **_k: rows):
            cur = st.current(_cfg())
            self.assertEqual(cur["region_alert_days"]["value"], 10)
            self.assertNotIn("regions", cur)
            self.assertEqual(st.editors(_cfg()), ["b@x.com"])

    def test_missing_table_means_nothing_saved(self):
        def nf(*_a, **_k):
            raise RuntimeError("404 Not found: Table p:optimizer_ops.settings_changes")
        with mock.patch.object(st.bq, "query", nf):
            self.assertEqual(st.current(_cfg(), fresh=True), {})

    def test_record_and_undo(self):
        calls = []
        with mock.patch.object(st.bq, "execute", lambda c, sql, params=None, **k: calls.append(params) or 1):
            st.record(_cfg(), "default_owner", "SET", "x@y.com", "a@x.com", "  why  ",
                      previous_value=None, previous_override=None, had_override=True)
        params = calls[-1]
        self.assertEqual(params["val"], json.dumps("x@y.com"))
        self.assertEqual(params["prev_override"], "null")      # an explicit saved None is kept
        self.assertEqual(params["note"], "why")
        self.assertEqual(st.undo_plan({"had_previous_override": True, "previous_override": None}), ("SET", None))
        self.assertEqual(st.undo_plan({"had_previous_override": False}), ("RESET", None))


class TestExplainWithAccess(unittest.TestCase):
    def test_sources_and_lock_reasons(self):
        c = _cfg(settings_admins=["a@x.com"])
        access = sc.access_for({"email": "a@x.com", "source": governance.SOURCE_IAP}, c, [])
        saved = {"region_alert_days": {"value": 9, "changed_by": "a@x.com", "changed_at": "t", "note": "n"}}
        with mock.patch.dict(os.environ, {}, clear=True):
            out = sc.explain(c, path="/nonexistent.yaml", saved=saved, access=access)
        by = {i["key"]: i for i in out["settings"]}
        self.assertEqual(by["region_alert_days"]["source"], "saved in the app")
        self.assertEqual(by["region_alert_days"]["saved"]["by"], "a@x.com")
        self.assertTrue(by["region_alert_days"]["editable_by_you"])
        self.assertFalse(by["executor.enable_class3"]["editable_by_you"])
        self.assertIn("deploy time", by["executor.enable_class3"]["locked_reason"])
        self.assertEqual(by["regions"]["applies_text"], sc.APPLIES["collect"])


if __name__ == "__main__":
    unittest.main()
