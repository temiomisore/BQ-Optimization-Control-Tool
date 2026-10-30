"""Tests for on-demand UI scan (`optimizer/scan_runner.py`, `/api/scan/*`, `cli scan`) and 1-click installer."""
from __future__ import annotations

import os
import subprocess
import time
import unittest
from unittest import mock

from optimizer import cli, config, scan_runner


def _cfg(**gov):
    base = {
        "project_id": "test-proj",
        "ops_dataset": "optimizer_ops",
        "location": "US",
        "governance": {
            "settings_admins": ["admin@acme.com"],
            "finops_approvers": ["finops@acme.com"],
            "trust_client_identity": True,
        },
    }
    base["governance"].update(gov)
    return config.Config(base)


class TestScanRunner(unittest.TestCase):
    def setUp(self):
        with scan_runner._LOCK:
            scan_runner._STATE.update({
                "state": "idle",
                "run_id": None,
                "mode": None,
                "step": None,
                "step_label": None,
                "started_at": None,
                "finished_at": None,
                "triggered_by": None,
                "execution_name": None,
                "log_uri": None,
                "log_tail": [],
                "error": None,
                "seq": 0,
            })
        scan_runner.invalidate_telemetry_cache()

    def test_in_process_scan_collects_and_runs_rules_without_execute(self):
        c = _cfg()
        calls: list[str] = []

        class _FakeClient:
            def get_dataset(self, _ds):
                calls.append("get_dataset")

        def fake_collect(_c):
            print("[*] Collected 42 jobs across 2 regions")
            calls.append("collect")

        def fake_rules(_c):
            print("findings=5 inserted=5")
            calls.append("rules")

        def forbid_execute(_c):
            raise AssertionError("cmd_execute must NEVER be called by an on-demand scan")

        finished_flag = {"done": False}
        with mock.patch.dict(os.environ, {"BQOPT_SCAN_JOB": "", "BQOPT_PIPELINE_JOB": ""}, clear=False), \
                mock.patch.object(scan_runner, "cfg", return_value=c), \
                mock.patch.object(scan_runner.bq, "client", return_value=_FakeClient()), \
                mock.patch.object(scan_runner.bq, "query", return_value=[{"cnt": 42, "latest": "2026-10-30T12:00:00Z"}]), \
                mock.patch.object(scan_runner.schema_guard, "ensure_current", lambda *_a, **_k: calls.append("guard")), \
                mock.patch.object(cli, "cmd_collect", fake_collect), \
                mock.patch.object(cli, "cmd_rules", fake_rules), \
                mock.patch.object(cli, "cmd_execute", forbid_execute):
            snap, already = scan_runner.start_scan(
                c, triggered_by="admin@acme.com", on_finish=lambda: finished_flag.__setitem__("done", True)
            )
            self.assertFalse(already)
            for _ in range(50):
                cur = scan_runner.get_status(c)
                if cur["state"] != "running":
                    break
                time.sleep(0.02)

            final = scan_runner.get_status(c)
            self.assertEqual(final["state"], "succeeded")
            self.assertEqual(final["mode"], "in_process")
            self.assertTrue(finished_flag["done"])
            self.assertEqual(calls, ["get_dataset", "guard", "collect", "rules"])
            self.assertTrue(any("Collected 42 jobs" in ln for ln in final["log_tail"]))
            self.assertEqual(final["telemetry"]["jobs_count"], 42)

    def test_cloud_run_job_mode_and_fallback(self):
        c = _cfg()
        exec_path = "projects/test-proj/locations/us-central1/jobs/bqopt-nightly-pipeline/executions/exec-123"
        finished_flag = {"done": False}

        class _FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.content = b"{}"
                self.text = str(payload)

            def json(self):
                return self._payload

        class _FakeSession:
            def __init__(self):
                self.posts = []

            def post(self, url, json=None, timeout=None):
                self.posts.append((url, json))
                return _FakeResp(200, {"metadata": {"name": exec_path, "logUri": "https://console.cloud.google.com/logs"}})

            def get(self, url, timeout=None):
                return _FakeResp(200, {"succeededCount": 1, "completionTime": "2026-10-30T14:00:00Z"})

        fake_sess = _FakeSession()
        with mock.patch.dict(os.environ, {"BQOPT_PIPELINE_JOB": "bqopt-nightly-pipeline", "BQOPT_JOB_REGION": "us-central1"}, clear=False), \
                mock.patch.object(scan_runner, "_authed_session", return_value=fake_sess), \
                mock.patch.object(scan_runner.bq, "query", return_value=[{"cnt": 100, "latest": "2026-10-30T14:00:00Z"}]):
            snap, already = scan_runner.start_scan(
                c, triggered_by="admin@acme.com", on_finish=lambda: finished_flag.__setitem__("done", True)
            )
            self.assertFalse(already)
            self.assertEqual(snap["mode"], "cloud_run_job")
            self.assertEqual(
                fake_sess.posts[0][1]["overrides"]["containerOverrides"][0]["args"],
                ["-m", "optimizer.cli", "scan"],
            )
            polled = scan_runner.get_status(c)
            self.assertEqual(polled["state"], "succeeded")
            self.assertTrue(finished_flag["done"])

    def test_cli_cmd_scan_never_calls_execute(self):
        c = _cfg()
        order: list[str] = []

        class _FakeClient:
            def get_dataset(self, _ds):
                order.append("get_dataset")

        with mock.patch.object(cli.bq, "client", return_value=_FakeClient()), \
                mock.patch("optimizer.schema_guard.ensure_current", lambda *_a, **_k: order.append("guard")), \
                mock.patch.object(cli, "cmd_collect", lambda _c: order.append("collect")), \
                mock.patch.object(cli, "cmd_rules", lambda _c: order.append("rules")), \
                mock.patch.object(cli, "cmd_execute", side_effect=AssertionError("must not execute")):
            cli.cmd_scan(c)
        self.assertEqual(order, ["get_dataset", "guard", "collect", "rules"])


class TestScanEndpointsAndInstaller(unittest.TestCase):
    def test_scan_api_enforces_editor_permission_and_dynamic_check_command(self):
        from review_app import main as app_mod

        c = _cfg(settings_admins=["admin@acme.com"], trust_client_identity=True)
        client = app_mod.app.test_client()
        fake_status = {
            "state": "idle", "run_id": None, "mode": None, "step": None, "step_label": None,
            "started_at": None, "finished_at": None, "triggered_by": None, "execution_name": None,
            "log_uri": None, "log_tail": [], "error": None, "seq": 1,
            "telemetry": {"has_telemetry": True, "jobs_count": 12, "last_job_at": "2026-10-30T10:00:00Z"},
        }

        with mock.patch.object(app_mod, "cfg", return_value=c), \
                mock.patch.object(app_mod.settings_store, "current", return_value={}), \
                mock.patch.object(app_mod.settings_store, "editors", return_value=["editor@acme.com"]), \
                mock.patch("optimizer.scan_runner.get_status", return_value=dict(fake_status)), \
                mock.patch("optimizer.scan_runner.start_scan", return_value=(dict(fake_status, state="running"), False)):
            # Viewer cannot trigger a scan
            r_view = client.get("/api/scan/status?principal=viewer@acme.com")
            self.assertEqual(r_view.status_code, 200)
            self.assertFalse(r_view.get_json()["can_trigger"])

            r_deny = client.post("/api/scan/run", json={"principal": "viewer@acme.com"})
            self.assertEqual(r_deny.status_code, 403)
            self.assertEqual(r_deny.get_json()["code"], "SETTINGS_PERMISSION_REQUIRED")

            # Editor and Admin can trigger a scan
            for allowed in ("editor@acme.com", "admin@acme.com"):
                r_ok = client.post("/api/scan/run", json={"principal": allowed})
                self.assertEqual(r_ok.status_code, 200)
                self.assertTrue(r_ok.get_json()["ok"])

        with mock.patch.dict(os.environ, {"BQOPT_PIPELINE_JOB": "bqopt-nightly-pipeline", "BQOPT_JOB_REGION": "us-east4"}, clear=False):
            cmd = app_mod._check_command()
            self.assertIn("bqopt-nightly-pipeline", cmd)
            self.assertIn("--region=us-east4", cmd)

    def test_install_script_syntax_help_and_docs(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        script = os.path.join(repo_root, "scripts", "install.sh")
        tutorial = os.path.join(repo_root, "docs", "INSTALL.md")
        welcome = os.path.join(repo_root, "docs", "cloudshell_welcome.txt")
        readme = os.path.join(repo_root, "README.md")

        self.assertTrue(os.path.isfile(script))
        self.assertTrue(os.path.isfile(tutorial))
        self.assertTrue(os.path.isfile(welcome))

        syn = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
        self.assertEqual(syn.returncode, 0, syn.stderr)

        hlp = subprocess.run(["bash", script, "--help"], capture_output=True, text=True)
        self.assertEqual(hlp.returncode, 0, hlp.stderr)
        self.assertIn("--project=PROJECT_ID", hlp.stdout)
        self.assertIn("--ops-dataset=DATASET", hlp.stdout)

        with open(script, encoding="utf-8") as f:
            script_text = f.read()
        self.assertIn("roles/run.builder", script_text)
        self.assertIn("--build-service-account=", script_text)
        self.assertIn("spec.template.spec.containers[0].image", script_text)

        with open(tutorial, encoding="utf-8") as f:
            tut_text = f.read()
        self.assertIn("<walkthrough-project-setup", tut_text)
        self.assertIn("./scripts/install.sh", tut_text)

        with open(readme, encoding="utf-8") as f:
            rd_text = f.read()
        self.assertIn("https://console.cloud.google.com/cloudshell/editor?shellonly=true", rd_text)
        self.assertIn("cloudshell_print=docs/cloudshell_welcome.txt", rd_text)
        self.assertIn("cloudshell_tutorial=docs/INSTALL.md", rd_text)
        self.assertNotIn("https://shell.cloud.google.com/cloudshell/editor", rd_text)


if __name__ == "__main__":
    unittest.main()
