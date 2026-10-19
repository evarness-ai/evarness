"""The app-tier stores open SQLite through the hardened helper and close every handle.

Each store here used to call ``sqlite3.connect`` itself: no WAL, and several through
``with sqlite3.connect(...)``, which commits but never closes. Building each one runs its
schema through its own connection path, so that is what these tests watch.

The email domain's two stores (categories, email accounts) left the core with their
modules (core/SDK boundary plan, PR 2); their cases are in
``tests/unit/evarness_personal/test_email/test_store_connections.py``.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evarness.kernel.governor.audit import GovernorAuditLogger
from evarness.memory.state.continuations import ContinuationStore
from evarness.memory.state.store import CheckpointStore
from evarness.runtime.router_audit import RouterAuditLogger
from evarness.services.activities.store import ActivityStore
from evarness.services.channels.web_push.store import PushSubscriptionStore
from evarness.services.digests.store import DigestStore
from evarness.services.heartbeat.run_store import HeartbeatRunStore
from evarness.services.missions.store import MissionStore
from evarness.services.notifications.store import ReminderStore
from evarness.services.rag.store import DocumentStore
from evarness.services.routines.store import RoutineStore
from evarness.services.tasks.store import TaskStore


def _schema_then(store: Any) -> Any:
    store.ensure_schema()
    return store


STORES: dict[str, Callable[[Path], Any]] = {
    "digests": DigestStore,
    "web_push": PushSubscriptionStore,
    "checkpoints": lambda p: CheckpointStore(db_path=p),
    "continuations": lambda p: ContinuationStore(db_path=p),
    # Creates its database on first use, not on construction: build it, then read once.
    "governor_audit": lambda p: GovernorAuditLogger(p).list_entries(),
    "router_audit": RouterAuditLogger,
    # These wrapped a bare connection in ``with``: committed, never closed. Each makes its
    # schema and reads once, so both paths open a connection.
    "activities": lambda p: _schema_then(ActivityStore(db_path=p)).get("absent"),
    "heartbeat_runs": lambda p: HeartbeatRunStore(db_path=p).first_seen("absent"),
    "missions": lambda p: MissionStore(p).load("absent"),
    "reminders": lambda p: _schema_then(ReminderStore(db_path=p)).get("absent"),
    "rag": lambda p: _schema_then(DocumentStore(db_path=p)).get_source("absent"),
    "routines": lambda p: RoutineStore(p).load("absent"),
    "tasks": lambda p: _schema_then(TaskStore(db_path=p)).get("absent"),
}


@pytest.mark.parametrize("name", sorted(STORES))
def test_the_store_is_wal_and_leaves_no_handle_open(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect
    this_thread = threading.get_ident()

    def watching(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        # The patch is process-wide: a thread an earlier test left running can open one
        # meanwhile, and it is not this store's, nor checkable from this thread.
        if threading.get_ident() == this_thread:
            opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", watching)
    db = tmp_path / f"{name}.db"
    STORES[name](db)
    monkeypatch.undo()

    assert opened, "the store opened no connection"
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
    check = sqlite3.connect(db)
    try:
        assert check.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        check.close()
