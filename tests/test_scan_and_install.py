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
        import json

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        script = os.path.join(repo_root, "scripts", "install.sh")
        app_json_path = os.path.join(repo_root, "app.json")
        ds_json_path = os.path.join(repo_root, ".deploystack", "deploystack.json")
        ds_desc_path = os.path.join(repo_root, ".deploystack", "messages", "description.txt")
        ds_pre_path = os.path.join(repo_root, ".deploystack", "scripts", "preinstall.sh")
        root_pre_path = os.path.join(repo_root, "scripts", "preinstall.sh")
        tutorial = os.path.join(repo_root, "docs", "INSTALL.md")
        welcome = os.path.join(repo_root, "docs", "cloudshell_welcome.txt")
        readme = os.path.join(repo_root, "README.md")

        self.assertTrue(os.path.isfile(script))
        self.assertTrue(os.path.isfile(app_json_path))
        self.assertTrue(os.path.isfile(ds_json_path))
        self.assertTrue(os.path.isfile(ds_desc_path))
        self.assertTrue(os.path.isfile(ds_pre_path))
        self.assertTrue(os.path.isfile(root_pre_path))
        self.assertTrue(os.access(ds_pre_path, os.X_OK))
        self.assertTrue(os.access(root_pre_path, os.X_OK))
        self.assertTrue(os.path.isfile(tutorial))
        self.assertTrue(os.path.isfile(welcome))

        syn = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
        self.assertEqual(syn.returncode, 0, syn.stderr)
        for p_sh in (ds_pre_path, root_pre_path):
            syn_pre = subprocess.run(["sh", "-n", p_sh], capture_output=True, text=True)
            self.assertEqual(syn_pre.returncode, 0, syn_pre.stderr)

        hlp = subprocess.run(["bash", script, "--help"], capture_output=True, text=True)
        self.assertEqual(hlp.returncode, 0, hlp.stderr)
        self.assertIn("--project=PROJECT_ID", hlp.stdout)
        self.assertIn("--prompt-project", hlp.stdout)
        self.assertIn("--ops-dataset=DATASET", hlp.stdout)
        self.assertIn("--phase=PHASE", hlp.stdout)
        self.assertIn("--image=IMAGE_URI", hlp.stdout)

        with open(ds_json_path, encoding="utf-8") as f:
            ds_cfg = json.load(f)
        self.assertEqual(ds_cfg.get("name"), "bq-optimization-control-tool")
        self.assertIs(ds_cfg.get("collect_project"), False)
        self.assertIs(ds_cfg.get("collect_region"), False)
        # Keep path_* unset so `dsexec -verify` resolves `terraform|.deploystack/messages|.deploystack/scripts`
        for forbidden_key in ("path_terraform", "path_messages", "path_scripts"):
            self.assertNotIn(forbidden_key, ds_cfg)

        with open(ds_pre_path, encoding="utf-8") as f:
            pre_text = f.read()
        self.assertIn("./scripts/install.sh --prompt-project --yes", pre_text)
        self.assertIn("exit ", pre_text)

        with open(script, encoding="utf-8") as f:
            script_text = f.read()
        self.assertIn("roles/run.builder", script_text)
        self.assertIn("roles/storage.objectViewer", script_text)
        self.assertIn("gs://run-sources-${PROJECT_ID}-${RUN_REGION}", script_text)
        self.assertIn("--build-service-account=", script_text)
        self.assertIn("spec.template.spec.containers[0].image", script_text)

        # Validate app.json strictly against GoogleCloudPlatform/cloud-run-button's
        # `appFile` schema (which uses `json.Decoder.DisallowUnknownFields()`).
        with open(app_json_path, encoding="utf-8") as f:
            app_cfg = json.load(f)
        allowed_top = {
            "name", "env", "options", "build", "hooks",
            "description", "keywords", "logo", "repository", "website", "stack", "formation",
        }
        allowed_options = {
            "allow-unauthenticated", "memory", "cpu", "port", "http2", "concurrency", "max-instances",
        }
        allowed_hooks = {"precreate", "postcreate", "prebuild", "postbuild"}
        self.assertTrue(set(app_cfg.keys()).issubset(allowed_top), set(app_cfg.keys()) - allowed_top)
        self.assertEqual(app_cfg.get("name"), "bqopt-review-ui")
        opts = app_cfg.get("options", {})
        self.assertTrue(set(opts.keys()).issubset(allowed_options), set(opts.keys()) - allowed_options)
        self.assertIs(opts.get("allow-unauthenticated"), False)
        hooks = app_cfg.get("hooks", {})
        self.assertEqual(set(hooks.keys()), allowed_hooks)
        for h_name, h_obj in hooks.items():
            self.assertEqual(set(h_obj.keys()), {"commands"}, h_name)
            self.assertTrue(h_obj["commands"], h_name)
            self.assertIn("./scripts/install.sh", h_obj["commands"][0])

        with open(tutorial, encoding="utf-8") as f:
            tut_text = f.read()
        self.assertIn("<walkthrough-project-setup", tut_text)
        self.assertIn("./scripts/install.sh", tut_text)

        with open(readme, encoding="utf-8") as f:
            rd_text = f.read()
        self.assertIn(
            "https://console.cloud.google.com/cloudshell/editor?shellonly=true"
            "&cloudshell_image=gcr.io/ds-artifacts-cloudshell/deploystack_custom_image"
            "&cloudshell_git_repo=https://github.com/temiomisore/BQ-Optimization-Control-Tool"
            "&cloudshell_git_branch=main",
            rd_text,
        )
        # Never use gcr.io/cloudrun/button in README: its /bin/cloudshell_open calls
        # un-scoped serviceusage.Services.List against Cloud Shell's shared project
        # 618104708054 (`SU_ALLOCATE_QUOTA_RESOURCE_EXHAUSTED` HTTP 429).
        self.assertNotIn("cloudshell_image=gcr.io/cloudrun/button", rd_text)
        self.assertNotIn("cloudshell_print=", rd_text)
        self.assertNotIn("cloudshell_tutorial=", rd_text)
        self.assertNotIn("https://shell.cloud.google.com/cloudshell/editor", rd_text)

    def test_cloudrun_button_hook_phases_fresh_and_redeploy(self):
        import shutil
        import tempfile
        import yaml

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        orig_script = os.path.join(repo_root, "scripts", "install.sh")

        with tempfile.TemporaryDirectory() as tmp:
            work_repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(work_repo, "scripts"), exist_ok=True)
            script_copy = os.path.join(work_repo, "scripts", "install.sh")
            shutil.copy2(orig_script, script_copy)
            os.chmod(script_copy, 0o755)

            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            log_file = os.path.join(tmp, "calls.log")
            svc_flag = os.path.join(tmp, "service_exists")

            fake_gcloud = os.path.join(bin_dir, "gcloud")
            with open(fake_gcloud, "w", encoding="utf-8") as f:
                f.write(f"""#!/usr/bin/env bash
set -euo pipefail
echo "gcloud $*" >> "{log_file}"
if [[ "$*" == *"config get-value account"* ]]; then
  echo "alice@company.com"
  exit 0
fi
if [[ "$*" == *"projects describe"* ]]; then
  echo "987654321"
  exit 0
fi
if [[ "$*" == *"projects get-ancestors"* ]]; then
  echo '[{{"type":"organization","id":"14682041824"}}]'
  exit 0
fi
if [[ "$*" == *"iam service-accounts describe"* ]]; then
  exit 1
fi
if [[ "$*" == *"run services describe"* ]]; then
  if [[ ! -f "{svc_flag}" ]]; then
    exit 1
  fi
  if [[ "$*" == *"status.url"* ]]; then
    echo "https://bqopt-review-ui-xyz-uc.a.run.app"
  elif [[ "$*" == *"containers[0].image"* ]]; then
    echo "us-east4-docker.pkg.dev/demo-proj-123/cloud-run-source-deploy/bqopt-review-ui@sha256:deadbeef"
  fi
  exit 0
fi
if [[ "$*" == *"run deploy bqopt-review-ui"* ]]; then
  touch "{svc_flag}"
  exit 0
fi
if [[ "$*" == *"asset search-all-resources"* ]]; then
  printf "US\\nus-east4\\n"
  exit 0
fi
if [[ "$*" == *"scheduler jobs describe"* ]]; then
  exit 1
fi
exit 0
""")
            os.chmod(fake_gcloud, 0o755)

            for tool in ("bq", "docker"):
                p = os.path.join(bin_dir, tool)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(f'#!/usr/bin/env bash\necho "{tool} $*" >> "{log_file}"\nexit 0\n')
                os.chmod(p, 0o755)

            img_url = "us-east4-docker.pkg.dev/demo-proj-123/cloud-run-source-deploy/bqopt-review-ui"
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            env["BQOPT_NO_TTY"] = "1"
            env["BQOPT_SA_SLEEP_SECONDS"] = "0"
            env["BQOPT_IAM_SLEEP_SECONDS"] = "0"
            env["BQOPT_IAP_SLEEP_SECONDS"] = "0"
            # Simulate upstream cloud-run-button's GOOGLE_CLOUD_PROJECT="{}" clobber:
            env["GOOGLE_CLOUD_PROJECT"] = "{}"
            env["GOOGLE_CLOUD_REGION"] = "{}"
            env["IMAGE_URL"] = img_url

            # --- 1) Fresh project flow: prepare -> postbuild (noop) -> precreate -> finalize ---
            for phase in ("prepare", "postbuild", "precreate", "finalize"):
                res = subprocess.run(
                    ["bash", script_copy, f"--phase={phase}", f"--image={img_url}", "--yes"],
                    cwd=work_repo, env=env, capture_output=True, text=True,
                )
                self.assertEqual(res.returncode, 0, f"phase={phase} failed:\n{res.stdout}\n{res.stderr}")
                if phase == "prepare":
                    self.assertTrue(os.path.isfile(os.path.join(work_repo, "config.yaml")))
                    self.assertTrue(os.path.isfile(os.path.join(work_repo, ".bqopt_prepared.env")))
                if phase == "finalize":
                    self.assertIn("https://bqopt-review-ui-xyz-uc.a.run.app/#settings", res.stdout)
                    self.assertFalse(os.path.exists(os.path.join(work_repo, ".bqopt_prepared.env")))

            with open(os.path.join(work_repo, "config.yaml"), encoding="utf-8") as f:
                gen_cfg = yaml.safe_load(f)
            self.assertEqual(gen_cfg["project_id"], "demo-proj-123")
            self.assertEqual(gen_cfg["asset_scope"], "organizations/14682041824")
            self.assertEqual(gen_cfg["jobs_view"], "JOBS_BY_ORGANIZATION")
            self.assertEqual(gen_cfg["governance"]["settings_admins"], ["alice@company.com"])
            self.assertEqual(
                gen_cfg["governance"]["iap_audience"],
                "/projects/987654321/locations/us-east4/services/bqopt-review-ui",
            )

            with open(log_file, encoding="utf-8") as f:
                calls_fresh = f.read()
            self.assertNotIn("docker push", calls_fresh)  # fresh flow lets main.go push
            self.assertIn("gcloud run deploy bqopt-review-ui --project=demo-proj-123 --region=us-east4 --image=" + img_url, calls_fresh)
            self.assertIn("--iap", calls_fresh)
            self.assertIn("gcloud run jobs deploy bqopt-nightly-pipeline", calls_fresh)
            self.assertIn("@sha256:deadbeef", calls_fresh)
            self.assertIn("gcloud scheduler jobs create http bqopt-nightly-schedule", calls_fresh)
            self.assertIn("--schedule=0 3 * * *", calls_fresh)
            lo_hr, hi_hr = gen_cfg["executor"]["change_window_utc"]
            self.assertTrue(lo_hr <= 3 < hi_hr, f"03:00 UTC schedule must fall inside change_window_utc={gen_cfg['executor']['change_window_utc']}")

            # --- 2) Redeploy flow (`service_exists` already true): prepare -> postbuild runs push + finalize ---
            open(log_file, "w", encoding="utf-8").close()
            for phase in ("prepare", "postbuild"):
                res = subprocess.run(
                    ["bash", script_copy, f"--phase={phase}", f"--image={img_url}", "--yes"],
                    cwd=work_repo, env=env, capture_output=True, text=True,
                )
                self.assertEqual(res.returncode, 0, f"redeploy phase={phase} failed:\n{res.stdout}\n{res.stderr}")

            with open(log_file, encoding="utf-8") as f:
                calls_redeploy = f.read()
            self.assertIn(f"docker push {img_url}", calls_redeploy)
            self.assertIn("gcloud run jobs deploy bqopt-nightly-pipeline", calls_redeploy)
            self.assertFalse(os.path.exists(os.path.join(work_repo, ".bqopt_prepared.env")))

            # --- 3) Fail-closed if prepare failed (missing state file) ---
            open(log_file, "w", encoding="utf-8").close()
            fail_res = subprocess.run(
                ["bash", script_copy, "--phase=postbuild", f"--image={img_url}", "--yes"],
                cwd=work_repo, env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(fail_res.returncode, 0)
            with open(log_file, encoding="utf-8") as f:
                calls_fail = f.read()
            self.assertIn(f"docker rmi -f {img_url}", calls_fail)

    def test_deploystack_preinstall_prompt_project_and_local_docker(self):
        import pty
        import select
        import shutil
        import tempfile
        import yaml

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        orig_script = os.path.join(repo_root, "scripts", "install.sh")
        orig_pre = os.path.join(repo_root, ".deploystack", "scripts", "preinstall.sh")

        with tempfile.TemporaryDirectory() as tmp:
            work_repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(work_repo, "scripts"), exist_ok=True)
            os.makedirs(os.path.join(work_repo, ".deploystack", "scripts"), exist_ok=True)
            shutil.copy2(orig_script, os.path.join(work_repo, "scripts", "install.sh"))
            shutil.copy2(orig_pre, os.path.join(work_repo, ".deploystack", "scripts", "preinstall.sh"))
            os.chmod(os.path.join(work_repo, "scripts", "install.sh"), 0o755)
            os.chmod(os.path.join(work_repo, ".deploystack", "scripts", "preinstall.sh"), 0o755)

            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            log_file = os.path.join(tmp, "calls.log")
            api_attempt_file = os.path.join(tmp, "api_attempt")

            fake_gcloud = os.path.join(bin_dir, "gcloud")
            with open(fake_gcloud, "w", encoding="utf-8") as f:
                f.write(f"""#!/usr/bin/env bash
set -euo pipefail
echo "gcloud $*" >> "{log_file}"
if [[ "$*" == *"config get-value account"* ]]; then
  echo "admin@temiomisore.altostrat.com"
  exit 0
fi
if [[ "$*" == *"config get-value project"* ]]; then
  echo "alpha-proj"
  exit 0
fi
if [[ "$*" == *"projects list"* ]]; then
  printf "alpha-proj\\tAlpha Project\\ntemi-reverse-etl-project\\tReverse ETL\\n"
  exit 0
fi
if [[ "$*" == *"services enable"* ]]; then
  if [[ ! -f "{api_attempt_file}" ]]; then
    touch "{api_attempt_file}"
    echo "Error 429: RESOURCE_EXHAUSTED rateLimitExceeded" >&2
    exit 1
  fi
  exit 0
fi
if [[ "$*" == *"projects describe"* ]]; then
  echo "555666777"
  exit 0
fi
if [[ "$*" == *"projects get-ancestors"* ]]; then
  echo '[{{"type":"organization","id":"14682041824"}}]'
  exit 0
fi
if [[ "$*" == *"iam service-accounts describe"* ]]; then
  exit 1
fi
if [[ "$*" == *"artifacts repositories describe"* ]]; then
  exit 1
fi
if [[ "$*" == *"run services describe"* ]]; then
  if [[ "$*" == *"status.url"* ]]; then
    echo "https://bqopt-review-ui-retl-uc.a.run.app"
  elif [[ "$*" == *"containers[0].image"* ]]; then
    echo "us-central1-docker.pkg.dev/temi-reverse-etl-project/cloud-run-source-deploy/bqopt-review-ui@sha256:cafebabe"
  fi
  exit 0
fi
if [[ "$*" == *"asset search-all-resources"* ]]; then
  printf "US\\n"
  exit 0
fi
if [[ "$*" == *"scheduler jobs describe"* ]]; then
  exit 1
fi
exit 0
""")
            os.chmod(fake_gcloud, 0o755)

            for tool in ("bq", "docker"):
                p = os.path.join(bin_dir, tool)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(f'#!/usr/bin/env bash\necho "{tool} $*" >> "{log_file}"\nexit 0\n')
                os.chmod(p, 0o755)

            # Simulate `/usr/local/bin/deploystack install` sourcing `.deploystack/scripts/preinstall.sh`:
            # `exit` inside `preinstall.sh` must exit the wrapper before reaching `AFTER_PREINSTALL`.
            wrapper = os.path.join(tmp, "fake_deploystack.sh")
            with open(wrapper, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\n. .deploystack/scripts/preinstall.sh\necho AFTER_PREINSTALL_MUST_NOT_RUN\nexit 99\n")
            os.chmod(wrapper, 0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            env["GOOGLE_CLOUD_PROJECT"] = "alpha-proj"
            env["BQOPT_SA_SLEEP_SECONDS"] = "0"
            env["BQOPT_IAM_SLEEP_SECONDS"] = "0"
            env["BQOPT_IAP_SLEEP_SECONDS"] = "0"
            env["BQOPT_API_RETRY_SLEEP_SECONDS"] = "0"
            env.pop("BQOPT_NO_TTY", None)
            env.pop("BQOPT_SIMPLE_PICKER", None)

            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                ["bash", wrapper],
                cwd=work_repo,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave_fd)

            output_chunks: list[bytes] = []
            sent_keys = False
            deadline = time.time() + 15.0
            try:
                while time.time() < deadline:
                    rlist, _, _ = select.select([master_fd], [], [], 0.1)
                    if rlist:
                        try:
                            chunk = os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output_chunks.append(chunk)
                        joined = b"".join(output_chunks)
                        if (not sent_keys) and b"temi-reverse-etl-project" in joined:
                            # Press Down-arrow (`\x1b[B`) to move from `alpha-proj` -> `temi-reverse-etl-project`, then Enter!
                            os.write(master_fd, b"\x1b[B\r")
                            sent_keys = True
                    if proc.poll() is not None:
                        # Drain any remaining buffered bytes
                        while True:
                            r2, _, _ = select.select([master_fd], [], [], 0.05)
                            if not r2:
                                break
                            try:
                                c2 = os.read(master_fd, 4096)
                                if not c2:
                                    break
                                output_chunks.append(c2)
                            except OSError:
                                break
                        break
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    proc.kill()
                rc = proc.wait(timeout=5)

            full_out = b"".join(output_chunks).decode("utf-8", errors="replace")
            self.assertTrue(sent_keys, f"Arrow-key picker never rendered:\n{full_out}")
            self.assertEqual(rc, 0, f"deploystack preinstall failed (rc={rc}):\n{full_out}")
            self.assertNotIn("AFTER_PREINSTALL_MUST_NOT_RUN", full_out)
            self.assertIn("https://bqopt-review-ui-retl-uc.a.run.app/#settings", full_out)

            with open(os.path.join(work_repo, "config.yaml"), encoding="utf-8") as f:
                gen_cfg = yaml.safe_load(f)
            self.assertEqual(gen_cfg["project_id"], "temi-reverse-etl-project")

            with open(log_file, encoding="utf-8") as f:
                calls = f.read()
            self.assertNotIn("services list", calls)
            self.assertIn("gcloud artifacts repositories create cloud-run-source-deploy", calls)
            expected_img = "us-central1-docker.pkg.dev/temi-reverse-etl-project/cloud-run-source-deploy/bqopt-review-ui:latest"
            self.assertIn(f"docker build -t {expected_img} .", calls)
            self.assertIn(f"docker push {expected_img}", calls)
            self.assertIn(f"gcloud run deploy bqopt-review-ui --project=temi-reverse-etl-project --region=us-central1 --image={expected_img}", calls)
            self.assertIn("--schedule=0 3 * * *", calls)

    def test_execute_outside_change_window_defers_without_marking_failed(self):
        import datetime as dt
        from optimizer import cli
        from optimizer.config import Config
        from optimizer.executor import router

        c = Config({
            "project_id": "demo-proj-123",
            "ops_dataset": "optimizer_ops",
            "location": "US",
            "governance": {"finops_approvers": [], "finops_rule_ids": []},
            "executor": {
                "enable_class1": True,
                "enable_class2": False,
                "enable_class3": False,
                "change_window_utc": [3, 9],
                "max_structural_changes_per_table_per_days": 7,
            },
        })
        sample_card = {
            "change_set_id": "cs-window-1",
            "apply_class": 1,
            "rule_ids": ["C1-01"],
            "target_project": "demo-proj-123",
            "target_dataset": "analytics",
            "target_table": "orders",
            "execution_route": "DIRECT_GUARDED",
        }
        transitions: list[tuple] = []

        # 1) Outside window (02:00 UTC): card stays APPROVED (zero FAILED transitions).
        fake_now_outside = dt.datetime(2026, 11, 2, 2, 5, tzinfo=dt.timezone.utc)
        with mock.patch("optimizer.store.approved_ready", return_value=[sample_card]), \
             mock.patch("optimizer.store.transition", side_effect=lambda *a, **kw: transitions.append((a, kw))), \
             mock.patch("optimizer.executor.router.dt") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now_outside
            mock_dt.timezone = dt.timezone
            cli.cmd_execute(c)
        self.assertEqual(transitions, [], "Outside change_window_utc must defer APPROVED cards, never mark FAILED")

        # 2) Inside window (03:00 UTC — matching `0 3 * * *` scheduler): check_window succeeds.
        fake_now_inside = dt.datetime(2026, 11, 2, 3, 2, tzinfo=dt.timezone.utc)
        with mock.patch("optimizer.executor.router.dt") as mock_dt:
            mock_dt.datetime.now.return_value = fake_now_inside
            mock_dt.timezone = dt.timezone
            router.check_window(c)  # no exception


if __name__ == "__main__":
    unittest.main()
