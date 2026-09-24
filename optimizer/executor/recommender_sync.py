"""Recommender API state write-back (design doc §4.3).

approve -> markClaimed ; verified -> markSucceeded ; regressed/failed ->
markFailed ; rejected -> markDismissed (best-effort — not all client
versions expose it). Keeping Active Assist in sync is what stops the console
from re-surfacing recommendations this tool already handled.
"""
from __future__ import annotations


def _client():
    from google.cloud import recommender_v1  # lazy
    return recommender_v1.RecommenderClient()


def mark(names: list[str] | None, state: str) -> list[str]:
    """Returns a note per recommendation for the audit trail; never raises."""
    notes = []
    if not names:
        return notes
    try:
        cli = _client()
    except Exception as e:  # lib missing / no creds
        return [f"recommender-sync skipped: {e}"]
    for name in names:
        try:
            rec = cli.get_recommendation(name=name)
            meta = {"tool": "bq-optimizer"}
            if state == "CLAIMED":
                cli.mark_recommendation_claimed(name=name, etag=rec.etag, state_metadata=meta)
            elif state == "SUCCEEDED":
                cli.mark_recommendation_succeeded(name=name, etag=rec.etag, state_metadata=meta)
            elif state == "FAILED":
                cli.mark_recommendation_failed(name=name, etag=rec.etag, state_metadata=meta)
            elif state == "DISMISSED":
                fn = getattr(cli, "mark_recommendation_dismissed", None)
                if fn:
                    fn(name=name, etag=rec.etag)
                else:
                    notes.append(f"dismiss unsupported by client lib for {name}")
                    continue
            notes.append(f"{state}:{name}")
        except Exception as e:
            notes.append(f"sync-error {name}: {e}")
    return notes
