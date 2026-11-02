"""Collector driver — the pieces SQL alone can't do (design doc §4.4).

Runs after the daily scheduled query:
  * backfill dataset_state_daily.storage_billing_model via the Datasets API
  * snapshot scheduled-query (DTS) configs into object_state_raw_daily
  * auto-discover and snapshot reservations, assignments & capacity commitments
    even when reservation_admin_project is unset in config.yaml
"""
from __future__ import annotations

import json

from . import bq, regions, security
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


def snapshot_reservations(c: Config) -> int:
    """Auto-discover (admin_project, region) reservation owners and snapshot RESERVATION,
    ASSIGNMENT and CAPACITY_COMMITMENT metadata into object_state_raw_daily when not already
    snapshotted today by sql/02_collector_run.sql or remote_collector."""
    home = regions.home_region(c)
    candidates: set[tuple[str, str]] = set()
    cfg_regs = c.get("regions")
    reg_list = [home] if (not isinstance(cfg_regs, list) or not cfg_regs) else [
        regions.normalize(r) or home for r in cfg_regs
    ]
    if home not in reg_list:
        reg_list.append(home)

    for raw_proj in (c.get("reservation_admin_project"), c.get("project_id")):
        if not raw_proj:
            continue
        try:
            vp = security.validate_project_id(str(raw_proj).strip().lower())
        except Exception:
            continue
        for r in reg_list:
            candidates.add((vp, r))

    # Extract any central slot-admin projects & regions directly from collected job history
    try:
        jrows = bq.query(c, f"""
            SELECT DISTINCT
              LOWER(REGEXP_EXTRACT(reservation_id, r'^(.+):[^:]+$'))       AS admin_project,
              LOWER(REGEXP_EXTRACT(reservation_id, r':([^.:]+)\\.[^.:]+$')) AS res_region
            FROM `{c.ops}.jobs_events`
            WHERE reservation_id IS NOT NULL""")
        for jr in jrows:
            ap = str(jr.get("admin_project") or "").strip().lower()
            rr = regions.normalize(jr.get("res_region")) or home
            if not ap:
                continue
            try:
                candidates.add((security.validate_project_id(ap), rr))
            except Exception:
                continue
    except Exception:
        pass

    if not candidates:
        return 0

    already: set[tuple[str, str]] = set()
    try:
        existing = bq.query(c, f"""
            SELECT DISTINCT LOWER(project_id) AS project_id, LOWER(region) AS region
            FROM `{c.ops}.object_state_raw_daily`
            WHERE snapshot_date = CURRENT_DATE()
              AND kind = 'RESERVATION'""")
        for er in existing:
            if er.get("project_id") and er.get("region"):
                already.add((str(er["project_id"]).lower(), str(er["region"]).lower()))
    except Exception:
        pass

    kinds = (
        ("RESERVATION", "RESERVATIONS"),
        ("ASSIGNMENT", "ASSIGNMENTS"),
        ("CAPACITY_COMMITMENT", "CAPACITY_COMMITMENTS"),
    )
    total = 0
    for admin_proj, reg in sorted(candidates):
        try:
            vreg = security.validate_region(reg)
        except Exception:
            continue
        if (admin_proj.lower(), vreg.lower()) in already:
            continue
        rloc = bq.location_for(vreg)
        fetched_by_kind: list[tuple[str, list[str]]] = []
        for kind, view in kinds:
            try:
                rows = bq.query(
                    c,
                    f"SELECT TO_JSON_STRING(t) AS raw_json "
                    f"FROM `{admin_proj}.region-{vreg}`.INFORMATION_SCHEMA.{view} t",
                    location=rloc,
                )
                raws = [str(r["raw_json"]) for r in rows if r.get("raw_json")]
                if raws:
                    fetched_by_kind.append((kind, raws))
            except Exception:
                continue
        if not fetched_by_kind:
            continue
        bq.execute(c, f"""
            DELETE FROM `{c.ops}.object_state_raw_daily`
            WHERE snapshot_date = CURRENT_DATE()
              AND LOWER(region) = @reg
              AND LOWER(project_id) = @proj
              AND kind IN ('RESERVATION', 'ASSIGNMENT', 'CAPACITY_COMMITMENT')""",
            {"reg": vreg.lower(), "proj": admin_proj.lower()})
        for kind, raws in fetched_by_kind:
            bq.execute(c, f"""
                INSERT INTO `{c.ops}.object_state_raw_daily`
                  (snapshot_date, region, kind, project_id, dataset_id, object_id, raw_json, run_id)
                SELECT CURRENT_DATE(), @reg, @kind, @proj, NULL, NULL, raw, 'driver'
                FROM UNNEST(@raws) AS raw""",
                {"reg": vreg, "kind": kind, "proj": admin_proj, "raws": raws})
            total += len(raws)
    return total


def run(c: Config) -> dict:
    out = {"billing_model_rows": backfill_storage_billing_model(c),
           "transfer_configs": snapshot_transfer_configs(c)}
    try:
        out["reservation_rows"] = snapshot_reservations(c)
    except Exception:
        out["reservation_rows"] = 0
    return out
