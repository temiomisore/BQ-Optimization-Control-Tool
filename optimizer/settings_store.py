"""Settings saved from the review UI's Settings page (Phase 2).

Every save is a new row in optimizer_ops.settings_changes (append-only), so the table is also
the history: who changed what, when, from what, and why. The value in force for a key is its
latest row; a RESET row means "nothing saved, use config.yaml / the default again". Undo is
just another row that puts the previous value back.

The settings editors list lives here too (key `_access.settings_editors`), managed by settings
admins (governance.settings_admins, deploy-time only).

config.cfg() reads the saved values through provider() once an entry point enables it:
review app + CLI (nightly job). Precedence: defaults < config.yaml < saved here < env vars.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from typing import Any, Mapping

from . import bq

EDITORS_KEY = "_access.settings_editors"
TABLE = "settings_changes"
DDL = """
CREATE TABLE IF NOT EXISTS `{ops}.settings_changes` (
  change_id              STRING NOT NULL,
  key                    STRING NOT NULL,   -- dotted setting key, or _access.settings_editors
  action                 STRING NOT NULL,   -- SET | RESET (back to config.yaml / default)
  value_json             STRING,            -- new value (JSON); NULL for RESET
  previous_value_json    STRING,            -- value in force before (for the history view)
  previous_override_json STRING,            -- value saved here before, NULL if none (for undo)
  changed_by             STRING,
  changed_at             TIMESTAMP NOT NULL,
  note                   STRING
)"""

_TTL_SEC = 30.0
_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
_READY: set[tuple] = set()


def _key(c: Mapping) -> tuple:
    return (c["project_id"], c.get("ops_dataset") or "optimizer_ops")


def _ops(c: Mapping) -> str:
    return "{}.{}".format(*_key(c))


def invalidate() -> None:
    with _LOCK:
        _CACHE.clear()


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def current(c: Mapping, fresh: bool = False) -> dict[str, dict]:
    """{key: {value, changed_by, changed_at, note}} for every key with a saved value."""
    k, now = _key(c), time.time()
    with _LOCK:
        hit = _CACHE.get(k)
        if hit and not fresh and now - hit[0] < _TTL_SEC:
            return hit[1]
    try:
        rows = bq.query(c, f"""
            SELECT key, action, value_json, changed_by, changed_at, note
            FROM `{_ops(c)}.{TABLE}`
            WHERE TRUE
            QUALIFY ROW_NUMBER() OVER (PARTITION BY key ORDER BY changed_at DESC, change_id DESC) = 1""")
    except Exception as e:  # noqa: BLE001
        if "not found" not in str(e).lower():
            raise
        rows = []                      # table not created yet: nothing saved
    out = {r["key"]: {"value": json.loads(r["value_json"]), "changed_by": r.get("changed_by"),
                      "changed_at": _iso(r.get("changed_at")), "note": r.get("note")}
           for r in rows if r.get("action") == "SET" and r.get("value_json") is not None}
    with _LOCK:
        _CACHE[k] = (now, out)
    return out


def provider(probe: Mapping) -> dict[str, Any]:
    """For config.use_ui_settings(): saved values of UI-editable settings only, so a row
    written outside the app can never flip a safety switch."""
    from .settings_catalog import EDITABLE_KEYS
    return {k: v["value"] for k, v in current(probe).items() if k in EDITABLE_KEYS}


def editors(c: Mapping) -> list[str]:
    v = (current(c).get(EDITORS_KEY) or {}).get("value") or []
    return sorted({str(e).strip().lower() for e in v if str(e).strip()})


def _ensure(c: Mapping) -> None:
    k = _key(c)
    if k not in _READY:
        bq.execute(c, DDL.format(ops=_ops(c)))
        _READY.add(k)


def record(c: Mapping, key: str, action: str, value: Any, actor: str, note: str | None,
           previous_value: Any = None, previous_override: Any = None, had_override: bool = False) -> str:
    """Append one change. action SET (value = new value) or RESET (value ignored).
    `had_override` says whether a value was saved here before (then `previous_override` is it)."""
    if action not in ("SET", "RESET"):
        raise ValueError("action must be SET or RESET")
    _ensure(c)
    change_id = str(uuid.uuid4())
    bq.execute(c, f"""
        INSERT INTO `{_ops(c)}.{TABLE}`
          (change_id, key, action, value_json, previous_value_json, previous_override_json,
           changed_by, changed_at, note)
        VALUES (@id, @key, @action, @val, @prev, @prev_override, @by, CURRENT_TIMESTAMP(), @note)""",
        {"id": change_id, "key": key, "action": action,
         "val": json.dumps(value) if action == "SET" else None,
         "prev": json.dumps(previous_value),
         "prev_override": json.dumps(previous_override) if had_override else None,
         "by": actor, "note": (note or "").strip()[:500] or None})
    invalidate()
    return change_id


def history(c: Mapping, key: str | None = None, limit: int = 100) -> list[dict]:
    try:
        rows = bq.query(c, f"""
            SELECT change_id, key, action, value_json, previous_value_json, previous_override_json,
                   changed_by, changed_at, note
            FROM `{_ops(c)}.{TABLE}`
            WHERE (@key IS NULL OR key = @key)
            ORDER BY changed_at DESC
            LIMIT {int(limit)}""", {"key": key})
    except Exception as e:  # noqa: BLE001
        if "not found" in str(e).lower():
            return []
        raise

    def _j(s: Any) -> Any:
        return None if s is None else json.loads(s)
    return [{"change_id": r["change_id"], "key": r["key"], "action": r["action"],
             "value": _j(r.get("value_json")), "previous_value": _j(r.get("previous_value_json")),
             "had_previous_override": r.get("previous_override_json") is not None,
             "previous_override": _j(r.get("previous_override_json")),
             "changed_by": r.get("changed_by"), "changed_at": _iso(r.get("changed_at")),
             "note": r.get("note")} for r in rows]


def get_change(c: Mapping, change_id: str) -> dict | None:
    for r in history(c, limit=1000):
        if r["change_id"] == change_id:
            return r
    return None


def undo_plan(change: Mapping) -> tuple[str, Any]:
    """('SET', value) or ('RESET', None) that puts a key back the way it was before `change`."""
    if change.get("had_previous_override"):
        return "SET", change.get("previous_override")
    return "RESET", None


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
