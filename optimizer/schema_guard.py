"""Self-healing guard for the optimizer_ops schema.

Why this exists: older copies of this repo (e.g. a notebook runtime that still has a
months-old checkout) can run `scripts/demo_setup.py`, whose `init` rebuilds the
optimizer_ops views from *their* SQL. Those views lack columns the current code needs
(e.g. v_jobs_costed.billing_mode) and approvals then fail with
"Name billing_mode not found inside j".

How it works: every `init` run by THIS code stamps a `schema_version` label (a hash of
the DDL files) on a sentinel view. A stale copy's `CREATE OR REPLACE VIEW` drops that
label, so `ensure_current()` sees a mismatch and re-runs `init` from the current code.
`init` is non-destructive (CREATE ... IF NOT EXISTS for tables, CREATE OR REPLACE for
views), so cards, approvals and history are untouched.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from . import bq
from .config import Config

log = logging.getLogger(__name__)

DDL_FILES = ("sql/01_ops_schema.sql", "sql/04_change_sets.sql", "sql/03_derived_views.sql")
SENTINEL_VIEW = "v_jobs_costed"
LABEL_KEY = "schema_version"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCK = threading.Lock()
_LAST_OK: dict[str, float] = {}


def expected_version() -> str:
    """Short hash of the DDL this code would apply (label-safe: lowercase hex)."""
    h = hashlib.sha256()
    for rel in DDL_FILES:
        with open(os.path.join(_REPO_ROOT, rel), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def stamp(c: Config) -> None:
    """Record that the views were just built by this code. Called at the end of init."""
    client = bq.client(c)
    table = client.get_table(f"{c.ops}.{SENTINEL_VIEW}")
    table.labels = {**(table.labels or {}), LABEL_KEY: expected_version()}
    client.update_table(table, ["labels"])


def is_current(c: Config) -> bool:
    try:
        table = bq.client(c).get_table(f"{c.ops}.{SENTINEL_VIEW}")
    except Exception:
        return False  # missing view / dataset -> needs init
    return (table.labels or {}).get(LABEL_KEY) == expected_version()


def ensure_current(c: Config, max_age_sec: float = 0.0) -> bool:
    """Re-run init if the views were rebuilt by another (older) copy of the code.

    `max_age_sec` skips the check if it passed within that many seconds (use for hot
    paths like dashboard loads; approvals pass 0 so they always check).
    Returns True if a repair was performed.
    """
    key = c.ops
    if max_age_sec and time.time() - _LAST_OK.get(key, 0.0) < max_age_sec:
        return False
    with _LOCK:
        if is_current(c):
            _LAST_OK[key] = time.time()
            return False
        log.warning("optimizer_ops views are stale or unstamped; re-running init from current code")
        from . import cli  # local import: cli imports half the package
        cli.cmd_init(c)    # also stamps the label
        _LAST_OK[key] = time.time()
        return True


def is_stale_schema_error(e: Exception) -> bool:
    """BigQuery's error when a view is missing a column the current code expects."""
    msg = str(e)
    return "not found inside" in msg or "Unrecognized name" in msg
