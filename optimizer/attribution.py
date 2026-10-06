"""Spend attribution: who is accountable for a principal's BigQuery spend.

Customer review (Sept 29, 2026): much of the spend comes from service
accounts (PowerBI dashboards, ETL). That spend has to roll up to the human who
owns the service account, and from there to their Director. The customer keeps a
service-account -> owner table already; its column names are configurable:

  service_account_owner_table: "gov-project.iam.sa_owner_map"
  service_account_owner_columns:
    service_account: sa_email        # required
    owner: owner_email               # required
    owner_team: team                 # optional
    director_email: director_email   # optional
    director_name: director_name     # optional
    application: app_name            # optional

The same applies to the employee hierarchy (`employee_hierarchy_table` plus
`employee_hierarchy_columns`). `optimizer.cli init` re-points the source views
optimizer_ops.v_employee_hierarchy_src / v_service_account_owners_src at those
tables; everything downstream (v_principal_directory, director rollups, owner
routing) reads the source views only.

Pure helpers here have no GCP imports; `load_sa_owner_map` imports bq lazily.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

SA_SUFFIX = ".gserviceaccount.com"

HIERARCHY_COLUMNS = ("user_email", "user_name", "department", "manager_email",
                     "director_email", "director_name", "cost_center")
HIERARCHY_REQUIRED = ("user_email",)

SA_COLUMNS = {             # canonical output column -> config key
    "service_account_email": "service_account",
    "owner_email": "owner",
    "owner_team": "owner_team",
    "director_email": "director_email",
    "director_name": "director_name",
    "application": "application",
}
SA_REQUIRED = ("service_account", "owner")

# project (may be domain-scoped, e.g. example.com:proj) . dataset . table
_TABLE_RE = re.compile(r"^[A-Za-z0-9_.:\-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_\-$]+$")
_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AttributionConfigError(ValueError):
    pass


def is_service_account(email: str | None) -> bool:
    return str(email or "").strip().lower().endswith(SA_SUFFIX)


def _table(name: Any, key: str) -> str:
    s = str(name or "").strip().strip("`")
    if not _TABLE_RE.match(s):
        raise AttributionConfigError(
            f"{key} must be a fully qualified table 'project.dataset.table', got {name!r}")
    return s


def _col(name: Any, key: str) -> str:
    s = str(name or "").strip().strip("`")
    if not _COL_RE.match(s):
        raise AttributionConfigError(f"{key}: invalid column name {name!r}")
    return s


def _select(mapping: dict[str, str | None]) -> str:
    out = []
    for canon, src in mapping.items():
        out.append(f"CAST(`{src}` AS STRING) AS {canon}" if src else f"CAST(NULL AS STRING) AS {canon}")
    return ",\n  ".join(out)


def source_view_sql(c: Mapping) -> list[str]:
    """CREATE OR REPLACE VIEW statements that point the attribution source
    views at the customer's own tables. Empty list when nothing is configured
    (the defaults in sql/03_derived_views.sql read the local optimizer_ops tables)."""
    ops = f"{c['project_id']}.{c.get('ops_dataset') or 'optimizer_ops'}"
    stmts: list[str] = []

    eh = c.get("employee_hierarchy_table")
    if eh:
        tbl = _table(eh, "employee_hierarchy_table")
        cols = dict(c.get("employee_hierarchy_columns") or {})
        mapping: dict[str, str | None] = {}
        for canon in HIERARCHY_COLUMNS:
            # Unmapped columns default to our own column name; map a column to
            # null when the customer's table does not have it.
            src = cols[canon] if canon in cols else canon
            if canon in HIERARCHY_REQUIRED and not src:
                raise AttributionConfigError(f"employee_hierarchy_columns.{canon} is required")
            mapping[canon] = _col(src, f"employee_hierarchy_columns.{canon}") if src else None
        stmts.append(f"CREATE OR REPLACE VIEW `{ops}.v_employee_hierarchy_src` AS\n"
                     f"SELECT\n  {_select(mapping)}\nFROM `{tbl}`")

    sa = c.get("service_account_owner_table")
    if sa:
        tbl = _table(sa, "service_account_owner_table")
        cols = dict(c.get("service_account_owner_columns") or {})
        for req in SA_REQUIRED:
            if not cols.get(req):
                raise AttributionConfigError(
                    f"service_account_owner_columns.{req} is required when service_account_owner_table is set")
        mapping = {canon: (_col(cols[key], f"service_account_owner_columns.{key}") if cols.get(key) else None)
                   for canon, key in SA_COLUMNS.items()}
        stmts.append(f"CREATE OR REPLACE VIEW `{ops}.v_service_account_owners_src` AS\n"
                     f"SELECT\n  {_select(mapping)}\nFROM `{tbl}`")
    return stmts


def sa_owner_map(rows: list[Mapping]) -> dict[str, dict]:
    """Rows of v_principal_directory (SERVICE_ACCOUNT) -> {sa_email: owner info}."""
    out: dict[str, dict] = {}
    for r in rows or []:
        email = str(r.get("email") or "").strip().lower()
        owner = str(r.get("accountable_owner_email") or "").strip().lower()
        if not email or not owner:
            continue
        out[email] = {
            "owner_email": owner,
            "director_name": r.get("director_name"),
            "director_email": r.get("director_email"),
            "department": r.get("department"),
            "application": r.get("application"),
            "attribution_source": r.get("attribution_source") or "SERVICE_ACCOUNT_OWNER",
        }
    return out


def resolve_sa_owner(email: str | None, sa_map: Mapping[str, dict] | None) -> dict | None:
    if not email or not sa_map:
        return None
    return sa_map.get(str(email).strip().lower())


def load_sa_owner_map(c: Any) -> dict[str, dict]:
    """Service-account owner map from the warehouse; {} if the views are not
    there yet (run `python -m optimizer.cli init`)."""
    from . import bq  # lazy: keeps this module importable without GCP libs
    try:
        rows = bq.query(c, f"""
            SELECT email, accountable_owner_email, director_name, director_email,
                   department, application, attribution_source
            FROM `{c.ops}.v_principal_directory`
            WHERE principal_type = 'SERVICE_ACCOUNT'""")
    except Exception as e:  # noqa: BLE001
        print(f"[!] service-account owner map unavailable ({str(e)[:120]}); "
              f"run `python -m optimizer.cli init` to create v_principal_directory")
        return {}
    return sa_owner_map(rows)
