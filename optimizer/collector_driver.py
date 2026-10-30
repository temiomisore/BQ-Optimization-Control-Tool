"""Collector driver — the pieces SQL alone can't do (design doc §4.4).

Runs after the daily scheduled query:
  * backfill dataset_state_daily.storage_billing_model via the Datasets API
  * snapshot scheduled-query (DTS) configs into object_state_raw_daily
"""
from __future__ import annotations

import json

from . import bq
from .config import Config


def backfill_storage_billing_model(c: Config) -> int:
    cli = bq.client(c)
    n = 0
    target_ds = c.get("target_dataset")
    if target_ds:
        refs = [(c["project_id"], target_ds)]
    else:
        try:
            rows = bq.query(c, f"""
                SELECT DISTINCT project_id, dataset_id
                FROM `{c.ops}.dataset_state_daily`
                WHERE snapshot_date = CURRENT_DATE()
                  AND project_id IS NOT NULL AND dataset_id IS NOT NULL""")
            refs = [(r["project_id"], r["dataset_id"]) for r in rows]
        except Exception:
            refs = []
        if not refs:
            refs = [(c["project_id"], item.dataset_id)
                    for item in cli.list_datasets(project=c["project_id"])]

    by_model: dict[str, list[str]] = {}
    for proj, ds_id in refs:
        try:
            ds = cli.get_dataset(f"{proj}.{ds_id}")
            model = getattr(ds, "storage_billing_model", None)
            if model:
                by_model.setdefault(str(model), []).append(f"{proj}.{ds_id}")
        except Exception:
            continue

    for model, fqns in by_model.items():
        n += bq.execute(c, f"""
            UPDATE `{c.ops}.dataset_state_daily`
            SET storage_billing_model = @m
            WHERE snapshot_date = CURRENT_DATE()
              AND CONCAT(project_id, '.', dataset_id) IN UNNEST(@fqns)""",
            {"m": model, "fqns": fqns})
    return n


def snapshot_transfer_configs(c: Config) -> int:
    try:
        from google.cloud import bigquery_datatransfer_v1 as dts
    except ImportError:
        return 0
    dcli = dts.DataTransferServiceClient()
    n = 0
    bq.execute(c, f"""
        DELETE FROM `{c.ops}.object_state_raw_daily`
        WHERE snapshot_date = CURRENT_DATE() AND kind = 'SCHEDULED_QUERY'""")
    for tc in dcli.list_transfer_configs(parent=f"projects/{c['project_id']}"):
        raw = json.dumps({
            "name": tc.name, "display_name": tc.display_name,
            "data_source_id": tc.data_source_id, "schedule": tc.schedule,
            "destination_dataset_id": tc.destination_dataset_id,
            "disabled": tc.disabled, "owner": getattr(tc, "owner_info", None) and tc.owner_info.email,
            "params": dict(tc.params) if tc.params else {},
        }, default=str)
        bq.execute(c, f"""
            INSERT INTO `{c.ops}.object_state_raw_daily`
              (snapshot_date, region, kind, project_id, dataset_id, object_id, raw_json, run_id)
            VALUES (CURRENT_DATE(), NULL, 'SCHEDULED_QUERY', @p, @d, @o, @raw, 'driver')""",
            {"p": c["project_id"], "d": tc.destination_dataset_id,
             "o": tc.display_name, "raw": raw})
        n += 1
    return n


def run(c: Config) -> dict:
    return {"billing_model_rows": backfill_storage_billing_model(c),
            "transfer_configs": snapshot_transfer_configs(c)}
