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

# Job-location sentinel: let BigQuery infer each job's location from the tables it references
# (needed when a statement touches a dataset outside the home location, e.g. us-central1).
AUTO = "auto"


def location_for(region: str | None) -> str | None:
    """Normalise a collector region name ('us', 'us-east4', 'region-us-east4') or a location
    ('US') into a BigQuery job location. None stays None (= the client's home location)."""
    if region is None:
        return None
    r = str(region).strip().lower()
    if not r:
        return None
    if r == AUTO:
        return AUTO
    if r.startswith("region-"):
        r = r[len("region-"):]
    return r.upper() if r in ("us", "eu") else r


def client(c: Config, location: str | None = None):
    """Cached client. Default: pinned to the home location (config `location`). With
    location=AUTO the client has no default location, so each job's location is inferred."""
    from google.cloud import bigquery  # lazy
    now = time.time()
    default_loc = None if location == AUTO else c.get("location")
    key = (c["project_id"], AUTO if location == AUTO else default_loc)
    cached = _CLIENT_CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]

    # 1. On Cloud Run (K_SERVICE is set), use native Metadata Server ADC directly
    if os.environ.get("K_SERVICE"):
        cl = bigquery.Client(project=c["project_id"], location=default_loc)
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
            cl = bigquery.Client(project=c["project_id"], location=default_loc, credentials=creds)
            _CLIENT_CACHE[key] = (now, cl)
            return cl
    except Exception:
        pass

    cl = bigquery.Client(project=c["project_id"], location=default_loc)
    _CLIENT_CACHE[key] = (now, cl)
    return cl


def _job_target(c: Config, location: str | None):
    """(client, explicit job location) for a statement. None = home location."""
    loc = location_for(location)
    if loc == AUTO:
        return client(c, AUTO), None
    return client(c), loc


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


_RESERVATION_TRANSIENT = ("does not have the reservation", "no slots are configured")


def _with_reservation_retry(fn, attempts: int = 12, delay_s: float = 10.0):
    """After a reservation assignment is created/dropped, BigQuery can reject queries for a
    few minutes ('project does not have the reservation in the data region or no slots are
    configured') while routing propagates. Retry those — and only those — errors."""
    import time
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1 or not any(s in str(e) for s in _RESERVATION_TRANSIENT):
                raise
            time.sleep(delay_s)


def query(c: Config, sql: str, params: dict[str, Any] | None = None,
          location: str | None = None) -> list[dict]:
    """Run a SELECT, return rows as plain dicts with optional maximum_bytes_billed cost guardrail.
    `location`: None = home location; a region (e.g. 'us-central1') or AUTO for other locations."""
    from google.cloud import bigquery
    max_bytes = c.get("max_bytes_billed_per_query")
    jc_kwargs: dict[str, Any] = {"query_parameters": _params(params)}
    if max_bytes:
        jc_kwargs["maximum_bytes_billed"] = int(max_bytes)

    def _run():
        cl, loc = _job_target(c, location)
        job = cl.query(sql, job_config=bigquery.QueryJobConfig(**jc_kwargs), location=loc)
        return [dict(r) for r in job.result()]
    return _with_reservation_retry(_run)


def execute(c: Config, sql: str, params: dict[str, Any] | None = None,
            location: str | None = None) -> int:
    """Run DML / DDL; return affected row count when the API reports one.
    `location`: None = home location; a region or AUTO for statements on other locations."""
    from google.cloud import bigquery

    def _run():
        cl, loc = _job_target(c, location)
        job = cl.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=_params(params)),
                       location=loc)
        job.result()
        return job.num_dml_affected_rows or 0
    return _with_reservation_retry(_run)


def probe_reservation(c: Config, location: str | None = None) -> str | None:
    """Run a trivial query and return the reservation it ran on (None = on-demand)."""
    def _run():
        cl, loc = _job_target(c, location)
        job = cl.query("SELECT 1", location=loc)
        job.result()
        return (job._properties.get("statistics") or {}).get("reservation_id")
    return _with_reservation_retry(_run)


def _conform(value: Any, field) -> Any:
    """Make one value JSON-loadable for a schema field (timestamps -> ISO strings,
    NUMERIC -> str, NULL repeated -> [], nested records recursively)."""
    import base64
    import datetime as _dt
    import decimal
    if field is not None and field.mode == "REPEATED":
        items = value if isinstance(value, (list, tuple)) else ([] if value is None else [value])
        sub = field.__class__(field.name, field.field_type, mode="NULLABLE", fields=field.fields)
        return [_conform(v, sub) for v in items]
    if value is None:
        return None
    if field is not None and field.field_type in ("RECORD", "STRUCT") and isinstance(value, dict):
        subs = {f.name: f for f in field.fields}
        return {k: _conform(value.get(k), f) for k, f in subs.items()}
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, dict):
        return {k: _conform(v, None) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_conform(v, None) for v in value]
    return value


def load_rows(c: Config, table_fqn: str, rows: list[dict], schema: list,
              write_disposition: str = "WRITE_TRUNCATE", expire_hours: int | None = 24) -> int:
    """Upload rows into a table in the HOME location with a load job (no query, so the rows can
    come from any region). Only schema fields are kept. Used for cross-region staging tables."""
    import datetime as _dt
    from google.cloud import bigquery
    cl = client(c)
    payload = [{f.name: _conform(r.get(f.name), f) for f in schema} for r in rows]
    job = cl.load_table_from_json(payload, table_fqn, job_config=bigquery.LoadJobConfig(
        schema=schema, write_disposition=write_disposition,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON))
    job.result()
    if expire_hours:
        tbl = cl.get_table(table_fqn)
        tbl.expires = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=expire_hours)
        cl.update_table(tbl, ["expires"])
    return len(payload)


def run_file(c: Config, path: str) -> None:
    """Submit a whole .sql file as one multi-statement job (scripts supported)."""
    with open(path) as f:
        sql = f.read()
    _with_reservation_retry(lambda: client(c).query(sql).result())


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
