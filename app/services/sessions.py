"""
Sign-in sessions, and Power BI dashboards that last only as long as one.

Every sign-in starts a session (its id travels in the sign-in token). With
sign-in on, a Power BI file uploaded during a session belongs to that
session: when the session ends, the dashboard and everything read from the
file are hard-deleted — the model snapshot, the uploaded file, data pulled
from its visuals and tables, results and templates built on them, and
investigations about it. Excel/CSV files and results comparing only those
stay.

A session ends when the person signs out, after BIP_SESSION_IDLE_MINUTES
without a request (default 30; the open website pings every few minutes, so
closing the tab is what starts the clock), or when the sign-in itself
expires (BIP_SESSION_DAYS). Ended sessions are cleaned up on the next
request that reaches the API (at most every few minutes) and when someone
comes back with an expired session.

  BIP_TEMPORARY_DASHBOARDS=false   keep dashboards across sessions instead
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.auth import auth_enabled, current_owner, set_owner
from app.models.graph import utcnow
from app.store.database import SessionRecord, Store

log = logging.getLogger("bip.sessions")

TOUCH_EVERY = 60.0          # seconds between "still here" writes for one session
SWEEP_EVERY = 300.0         # seconds between clean-ups of ended sessions

_touched: dict[str, float] = {}
_lock = threading.Lock()
_last_sweep = 0.0
_sweeping = False


def temporary_dashboards() -> bool:
    return auth_enabled() and os.environ.get("BIP_TEMPORARY_DASHBOARDS", "true").strip().lower() not in ("0", "false", "no", "off")


def idle_seconds() -> float:
    return max(1.0, float(os.environ.get("BIP_SESSION_IDLE_MINUTES", "30") or 30) * 60)


def _max_age_seconds() -> float:
    return float(os.environ.get("BIP_SESSION_DAYS", "7") or 7) * 86400


def _age(iso: str) -> float:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def _expired(s: SessionRecord) -> bool:
    return _age(s.last_seen_at) > idle_seconds() or _age(s.created_at) > _max_age_seconds()


# ---------------------------------------------------------------- lifecycle
def start(store: Store, username: str) -> str:
    """A new sign-in: record its session and return the id for the token."""
    sid = uuid.uuid4().hex
    token = set_owner(username)
    try:
        now = utcnow().isoformat()
        store.sessions[sid] = SessionRecord(session_id=sid, created_at=now, last_seen_at=now)
    finally:
        _reset(token)
    with _lock:
        _touched[sid] = time.monotonic()
    return sid


def touch(store: Store, sid: str) -> bool:
    """A request in this session (the owner is already set): False if the
    session has ended — signed out, idle too long or expired."""
    now = time.monotonic()
    with _lock:
        if now - _touched.get(sid, -1e9) < TOUCH_EVERY:
            return True
    record = store.sessions.get(sid)
    if record is None:
        return False
    if _expired(record):
        end(store, sid)
        return False
    record.last_seen_at = utcnow().isoformat()
    store.sessions[sid] = record
    with _lock:
        _touched[sid] = now
    return True


def end(store: Store, sid: str) -> int:
    """End a session of the current owner: remove its Power BI dashboards
    (when they're temporary). Returns how many were removed."""
    with _lock:
        _touched.pop(sid, None)
    removed = 0
    if temporary_dashboards():
        for record in store.connection_records.values():
            if record.session_id == sid and _purge_quietly(store, record.id):
                removed += 1
    store.sessions.delete(sid)
    return removed


def sweep(store: Store) -> dict:
    """End every expired session of every account, and remove temporary
    dashboards whose session is gone (older ones included)."""
    ended = removed = 0
    for account in store.db.list_users():
        if not account:
            continue
        token = set_owner(account["username"])
        try:
            live = set()
            for sid, s in list(store.sessions.items()):
                if _expired(s):
                    removed += end(store, sid)
                    ended += 1
                else:
                    live.add(sid)
            if temporary_dashboards():
                for record in store.connection_records.values():
                    if record.platform == "pbix" and record.session_id not in live and _purge_quietly(store, record.id):
                        removed += 1
        finally:
            _reset(token)
    if ended or removed:
        log.info("session clean-up: %s sessions ended, %s dashboards removed", ended, removed)
    return {"sessions_ended": ended, "dashboards_removed": removed}


def maybe_sweep(store: Store) -> None:
    """Run a clean-up in the background, at most every few minutes."""
    global _last_sweep, _sweeping
    with _lock:
        if _sweeping or time.monotonic() - _last_sweep < SWEEP_EVERY:
            return
        _sweeping, _last_sweep = True, time.monotonic()

    def run() -> None:
        global _sweeping
        try:
            sweep(store)
        except Exception:  # noqa: BLE001 - try again next time
            log.exception("session clean-up failed")
        finally:
            with _lock:
                _sweeping = False

    threading.Thread(target=run, name="bip-session-sweep", daemon=True).start()


# ------------------------------------------------------------------ removal
def purge_dashboard(store: Store, connection_id: str) -> bool:
    """Hard-delete a Power BI dashboard and everything read from its file
    (for the current owner)."""
    from app.services import recon_service

    record = store.connection_records.get(connection_id)
    if record is None:
        return False
    datasets = {d.dataset_id for d in store.datasets.values() if d.source.connection_id == connection_id}
    # copies of those (a dataset re-used as-is) carry the same rows
    while True:
        more = {d.dataset_id for d in store.datasets.values()
                if d.source.kind == "dataset" and d.source.dataset_id in datasets and d.dataset_id not in datasets}
        if not more:
            break
        datasets |= more
    snapshots = {s.snapshot_id for s in store.snapshots.values() if s.connection_id == connection_id}
    for r in store.data_reconciliations.values():
        if r.left_dataset_id in datasets or r.right_dataset_id in datasets:
            store.data_reconciliations.delete(r.reconciliation_id)
    for r in store.reconciliations.values():
        if r.snapshot_id_a in snapshots or r.snapshot_id_b in snapshots:
            store.reconciliations.delete(r.reconciliation_id)
    for d in store.definitions.values():
        if connection_id in (d.left.connection_id, d.right.connection_id):
            store.definitions.delete(d.definition_id)
    for i in store.investigations.values():
        if i.connection_id == connection_id:
            store.investigations.delete(i.investigation_id)
    for dataset_id in datasets:
        store.datasets.delete(dataset_id)
    for snapshot_id in snapshots:
        store.snapshots.delete(snapshot_id)
    store.remove_connection(connection_id)
    for file_id in record.file_ids:
        recon_service.delete_file(store, file_id, force=True)
    return True


def _purge_quietly(store: Store, connection_id: str) -> bool:
    try:
        return purge_dashboard(store, connection_id)
    except Exception:  # noqa: BLE001 - the next clean-up retries
        log.exception("couldn't remove dashboard %s of %s", connection_id, current_owner())
        return False


def _reset(token) -> None:
    from app.auth import _owner

    _owner.reset(token)


def current_session_id() -> Optional[str]:
    """The session the current request belongs to (None when sign-in is off)."""
    from app.auth import current_session

    return current_session()
