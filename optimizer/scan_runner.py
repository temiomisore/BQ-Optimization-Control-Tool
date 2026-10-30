"""On-demand BigQuery telemetry + rules scan triggered from the Review UI.

Supports two execution modes automatically:
  1. Cloud Run Job (`BQOPT_SCAN_JOB` or `BQOPT_PIPELINE_JOB`): launches an isolated
     Cloud Run Job execution (`python -m optimizer.cli scan`) via the Cloud Run v2 API
     so large 180-day backfills have a full 60-minute budget and survive tab closes.
  2. In-process thread fallback: runs `init/schema_guard -> collect -> rules` in a
     single-flight background thread (used locally, in tests, or if no job is configured).

Never runs `execute`: clicking "Run scan now" in the UI is strictly read-only on
customer workloads and only refreshes telemetry + recommendation cards in `optimizer_ops`.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
import uuid
from typing import Any, Callable

from . import bq, schema_guard
from .config import Config, cfg

_LOCK = threading.Lock()
_COND = threading.Condition(_LOCK)
_SCAN_THREAD_IDENT: int | None = None
_ORIG_STDOUT = None
_MAX_LOG_LINES = 45

_STATE: dict[str, Any] = {
    "state": "idle",          # idle | running | succeeded | failed
    "run_id": None,
    "mode": None,             # cloud_run_job | in_process
    "step": None,             # starting | init | collect | rules | done
    "step_label": None,
    "started_at": None,
    "finished_at": None,
    "triggered_by": None,
    "execution_name": None,   # projects/.../locations/.../jobs/.../executions/...
    "log_uri": None,
    "log_tail": [],
    "error": None,
    "seq": 0,
}

_TELEM_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_TELEM_TTL_SEC = 30.0
_ON_FINISH_CBS: list[Callable[[], None]] = []


def _utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _bump_locked(**updates: Any) -> None:
    _STATE.update(updates)
    _STATE["seq"] = int(_STATE.get("seq") or 0) + 1
    _COND.notify_all()


def _append_log(line: str) -> None:
    clean = line.rstrip()
    if not clean:
        return
    with _LOCK:
        tail = list(_STATE.get("log_tail") or [])
        tail.append(clean[:240])
        if len(tail) > _MAX_LOG_LINES:
            tail = tail[-_MAX_LOG_LINES:]
        _STATE["log_tail"] = tail
        _STATE["seq"] = int(_STATE.get("seq") or 0) + 1
        _COND.notify_all()


class _ThreadTeeStdout:
    """Forwards writes to real stdout, while copying lines from the active scan thread."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._buf = ""

    def write(self, s: str) -> int:
        res = self._wrapped.write(s)
        if _SCAN_THREAD_IDENT is not None and threading.get_ident() == _SCAN_THREAD_IDENT and s:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                _append_log(line)
        return res

    def flush(self) -> None:
        self._wrapped.flush()

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)


def _configured_job() -> tuple[str | None, str]:
    job = (os.environ.get("BQOPT_SCAN_JOB") or os.environ.get("BQOPT_PIPELINE_JOB") or "").strip() or None
    region = (
        os.environ.get("BQOPT_JOB_REGION")
        or os.environ.get("CLOUD_RUN_REGION")
        or "us-central1"
    ).strip()
    return job, region


def _authed_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def _trigger_cloud_run_job(project_id: str, region: str, job_name: str) -> dict[str, Any]:
    """Trigger `python -m optimizer.cli scan` on the configured Cloud Run v2 Job."""
    sess = _authed_session()
    url = f"https://run.googleapis.com/v2/projects/{project_id}/locations/{region}/jobs/{job_name}:run"
    body = {
        "overrides": {
            "containerOverrides": [
                {"args": ["-m", "optimizer.cli", "scan"]}
            ]
        }
    }
    resp = sess.post(url, json=body, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"Cloud Run job {job_name} returned HTTP {resp.status_code}: {resp.text[:240]}")
    payload = resp.json() if resp.content else {}
    # Long-running Operation metadata/response holds the Execution resource name.
    meta = payload.get("metadata") or {}
    response_obj = payload.get("response") or {}
    exec_name = (
        meta.get("name")
        or response_obj.get("name")
        or (payload.get("name") if "/executions/" in str(payload.get("name") or "") else None)
    )
    log_uri = meta.get("logUri") or response_obj.get("logUri")
    return {"execution_name": exec_name, "log_uri": log_uri}


def _poll_cloud_run_execution() -> None:
    """Refresh `_STATE` from Cloud Run v2 Execution status when mode == cloud_run_job."""
    with _LOCK:
        if _STATE["state"] != "running" or _STATE["mode"] != "cloud_run_job":
            return
        exec_name = _STATE.get("execution_name")
        cbs = list(_ON_FINISH_CBS)
    if not exec_name:
        return
    try:
        sess = _authed_session()
        resp = sess.get(f"https://run.googleapis.com/v2/{exec_name.lstrip('/')}", timeout=10)
        if resp.status_code >= 400:
            return
        ex = resp.json()
    except Exception:
        return

    succeeded = int(ex.get("succeededCount") or 0)
    failed = int(ex.get("failedCount") or 0)
    cancelled = int(ex.get("cancelledCount") or 0)
    completion_time = ex.get("completionTime")
    log_uri = ex.get("logUri")

    finished_now = False
    with _LOCK:
        if _STATE["state"] != "running" or _STATE.get("execution_name") != exec_name:
            return
        if log_uri and not _STATE.get("log_uri"):
            _STATE["log_uri"] = log_uri
        if succeeded > 0:
            _bump_locked(
                state="succeeded",
                step="done",
                step_label="Scan complete — recommendations updated",
                finished_at=completion_time or _utc_iso(),
            )
            finished_now = True
        elif failed > 0 or cancelled > 0:
            msg = "Cloud Run scan job failed."
            for cond in ex.get("conditions") or []:
                if cond.get("state") == "CONDITION_FAILED" and cond.get("message"):
                    msg = str(cond["message"])[:300]
                    break
            _bump_locked(
                state="failed",
                step="done",
                step_label="Scan failed",
                finished_at=completion_time or _utc_iso(),
                error=msg,
            )
            finished_now = True
        else:
            # Estimate visual progress smoothly while the Cloud Run Job executes
            _STATE["step"] = "collect"
            _STATE["step_label"] = "Scanning BigQuery regions & running 14 optimization rules (Cloud Run job)…"

    if finished_now:
        invalidate_telemetry_cache()
        for cb in cbs:
            try:
                cb()
            except Exception:
                pass


def _run_in_process(run_id: str) -> None:
    global _SCAN_THREAD_IDENT, _ORIG_STDOUT
    from . import cli

    with _LOCK:
        _SCAN_THREAD_IDENT = threading.get_ident()
        if not isinstance(sys.stdout, _ThreadTeeStdout):
            _ORIG_STDOUT = sys.stdout
            sys.stdout = _ThreadTeeStdout(sys.stdout)

    try:
        c = cfg()
        with _LOCK:
            _bump_locked(step="init", step_label="Step 1/3 · Checking control-plane schema…")
        try:
            bq.client(c).get_dataset(c.ops)
            schema_guard.ensure_current(c, max_age_sec=0)
        except Exception:
            cli.cmd_init(c)

        with _LOCK:
            _bump_locked(step="collect", step_label="Step 2/3 · Discovering regions & collecting BigQuery telemetry…")
        cli.cmd_collect(cfg())

        with _LOCK:
            _bump_locked(step="rules", step_label="Step 3/3 · Running 14 optimization rules & pricing savings…")
        cli.cmd_rules(cfg())

        with _LOCK:
            cbs = list(_ON_FINISH_CBS)
            _bump_locked(
                state="succeeded",
                step="done",
                step_label="Scan complete — recommendations updated",
                finished_at=_utc_iso(),
            )
        invalidate_telemetry_cache()
        for cb in cbs:
            try:
                cb()
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001
        err_str = f"{type(e).__name__}: {str(e).splitlines()[0][:260]}" if str(e) else type(e).__name__
        _append_log(f"ERROR: {err_str}")
        with _LOCK:
            _bump_locked(
                state="failed",
                step="done",
                step_label="Scan failed",
                finished_at=_utc_iso(),
                error=err_str,
            )
    finally:
        with _LOCK:
            _SCAN_THREAD_IDENT = None


def invalidate_telemetry_cache() -> None:
    _TELEM_CACHE["ts"] = 0.0
    _TELEM_CACHE["data"] = None


def _telemetry_summary(c: Config) -> dict[str, Any]:
    now = time.time()
    if _TELEM_CACHE["data"] is not None and now - _TELEM_CACHE["ts"] < _TELEM_TTL_SEC:
        return dict(_TELEM_CACHE["data"])
    out: dict[str, Any] = {"has_telemetry": False, "jobs_count": 0, "last_job_at": None}
    try:
        rows = bq.query(
            c,
            f"SELECT COUNT(1) AS cnt, MAX(creation_time) AS latest FROM `{c.ops}.jobs_events`",
        )
        if rows:
            cnt = int(rows[0].get("cnt") or 0)
            latest = rows[0].get("latest")
            out["jobs_count"] = cnt
            out["has_telemetry"] = cnt > 0
            if isinstance(latest, dt.datetime):
                out["last_job_at"] = (latest if latest.tzinfo else latest.replace(tzinfo=dt.timezone.utc)).isoformat()
            elif latest:
                out["last_job_at"] = str(latest)
    except Exception:
        pass
    _TELEM_CACHE["data"] = dict(out)
    _TELEM_CACHE["ts"] = now
    return out


def start_scan(
    c: Config,
    *,
    triggered_by: str,
    on_finish: Callable[[], None] | None = None,
    prefer_job: bool = True,
) -> tuple[dict[str, Any], bool]:
    """Start a scan if none is currently running. Returns (status_dict, already_running)."""
    # Refresh Cloud Run Job status first in case a prior job already finished.
    _poll_cloud_run_execution()

    with _LOCK:
        if _STATE["state"] == "running":
            return dict(_STATE), True
        run_id = str(uuid.uuid4())
        _ON_FINISH_CBS.clear()
        if on_finish is not None:
            _ON_FINISH_CBS.append(on_finish)
        _STATE.update({
            "state": "running",
            "run_id": run_id,
            "mode": "starting",
            "step": "starting",
            "step_label": "Starting BigQuery scan…",
            "started_at": _utc_iso(),
            "finished_at": None,
            "triggered_by": triggered_by,
            "execution_name": None,
            "log_uri": None,
            "log_tail": [],
            "error": None,
            "seq": int(_STATE.get("seq") or 0) + 1,
        })
        _COND.notify_all()

    job_name, job_region = _configured_job()
    if prefer_job and job_name:
        try:
            res = _trigger_cloud_run_job(c["project_id"], job_region, job_name)
            if res.get("execution_name"):
                with _LOCK:
                    _bump_locked(
                        mode="cloud_run_job",
                        step="collect",
                        step_label=f"Running Cloud Run scan job ({job_name})…",
                        execution_name=res["execution_name"],
                        log_uri=res.get("log_uri"),
                        log_tail=[f"Started Cloud Run job {job_name} in {job_region} ({res['execution_name'].split('/')[-1]})"],
                    )
                return get_status(c), False
        except Exception as e:  # noqa: BLE001
            _append_log(f"Cloud Run job unavailable ({str(e).splitlines()[0][:140]}); running scan directly in app…")

    with _LOCK:
        _bump_locked(mode="in_process", step="init", step_label="Step 1/3 · Checking control-plane schema…")
    t = threading.Thread(target=_run_in_process, args=(run_id,), daemon=True, name=f"bqopt-scan-{run_id[:8]}")
    t.start()
    return get_status(c), False


def get_status(c: Config, *, wait_since_seq: int | None = None, wait_sec: float = 0.0) -> dict[str, Any]:
    """Return current scan status; optionally hold open briefly while an in-process scan runs."""
    _poll_cloud_run_execution()
    if wait_sec > 0 and wait_since_seq is not None:
        deadline = time.monotonic() + min(wait_sec, 15.0)
        with _LOCK:
            while (
                _STATE["state"] == "running"
                and _STATE["mode"] == "in_process"
                and int(_STATE.get("seq") or 0) <= wait_since_seq
            ):
                rem = deadline - time.monotonic()
                if rem <= 0:
                    break
                _COND.wait(timeout=rem)

    with _LOCK:
        snap = copy_state = {
            k: (list(v) if isinstance(v, list) else v)
            for k, v in _STATE.items()
        }
    if copy_state["state"] != "running":
        snap["telemetry"] = _telemetry_summary(c)
    else:
        snap["telemetry"] = _TELEM_CACHE["data"] or {"has_telemetry": False, "jobs_count": 0, "last_job_at": None}
    return snap
