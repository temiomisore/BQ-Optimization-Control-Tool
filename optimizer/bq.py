"""Thin BigQuery helpers.

google-cloud-bigquery is imported lazily so pure-logic modules (scoring,
compiler) and their unit tests never need GCP credentials or the library.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .config import Config


import os
import time

_CLIENT_CACHE: dict[tuple[str, str | None], tuple[float, Any]] = {}
_CACHE_TTL_SEC = 1200.0  # Refresh cached token every 20 minutes


def client(c: Config):
    from google.cloud import bigquery  # lazy
    now = time.time()
    key = (c["project_id"], c.get("location"))
    cached = _CLIENT_CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]

    # 1. On Cloud Run (K_SERVICE is set), use native Metadata Server ADC directly
    if os.environ.get("K_SERVICE"):
        cl = bigquery.Client(project=c["project_id"], location=c.get("location"))
        _CLIENT_CACHE[key] = (now, cl)
        return cl

    # 2. Local workstation / notebook environment: prefer fresh gcloud CLI token, cached for 20 mins
    try:
        import subprocess
        from google.oauth2.credentials import Credentials
        res = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, timeout=5)
        lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
        token = next((l for l in lines if l.startswith("ya29.")), None)
        if token:
            creds = Credentials(token)
            cl = bigquery.Client(project=c["project_id"], location=c.get("location"), credentials=creds)
            _CLIENT_CACHE[key] = (now, cl)
            return cl
    except Exception:
        pass

    cl = bigquery.Client(project=c["project_id"], location=c.get("location"))
    _CLIENT_CACHE[key] = (now, cl)
    return cl


def _params(named: dict[str, Any] | None):
    if not named:
        return []
    from google.cloud import bigquery
    out = []
    for k, v in named.items():
        if isinstance(v, list):
            out.append(bigquery.ArrayQueryParameter(k, "STRING", [str(x) for x in v]))
        elif isinstance(v, bool):
            out.append(bigquery.ScalarQueryParameter(k, "BOOL", v))
        elif isinstance(v, int):
            out.append(bigquery.ScalarQueryParameter(k, "INT64", v))
        elif isinstance(v, float):
            out.append(bigquery.ScalarQueryParameter(k, "FLOAT64", v))
        else:
            out.append(bigquery.ScalarQueryParameter(k, "STRING", None if v is None else str(v)))
    return out


def query(c: Config, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Run a SELECT, return rows as plain dicts with optional maximum_bytes_billed cost guardrail."""
    from google.cloud import bigquery
    max_bytes = c.get("max_bytes_billed_per_query")
    jc_kwargs: dict[str, Any] = {"query_parameters": _params(params)}
    if max_bytes:
        jc_kwargs["maximum_bytes_billed"] = int(max_bytes)
    job = client(c).query(sql, job_config=bigquery.QueryJobConfig(**jc_kwargs))
    return [dict(r) for r in job.result()]


def execute(c: Config, sql: str, params: dict[str, Any] | None = None) -> int:
    """Run DML / DDL; return affected row count when the API reports one."""
    from google.cloud import bigquery
    job = client(c).query(sql, job_config=bigquery.QueryJobConfig(query_parameters=_params(params)))
    job.result()
    return job.num_dml_affected_rows or 0


def run_file(c: Config, path: str) -> None:
    """Submit a whole .sql file as one multi-statement job (scripts supported)."""
    with open(path) as f:
        client(c).query(f.read()).result()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def loads(s: str | None) -> Any:
    return json.loads(s, strict=False) if s else None


def deep_find(obj: Any, keys: Iterable[str]) -> Any:
    """Recursively find the first value under any of `keys` in nested JSON.

    Used against Recommender API payloads whose exact key names vary by
    recommender version — tolerant by design.
    """
    wanted = {k.lower() for k in keys}
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in wanted and v not in (None, "", []):
                    return v
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None
