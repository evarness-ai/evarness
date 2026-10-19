"""The app-tier stores open SQLite through the hardened helper and close every handle.

Each store here used to call ``sqlite3.connect`` itself: no WAL, and several through
``with sqlite3.connect(...)``, which commits but never closes. Building each one runs its
schema through its own connection path, so that is what these tests watch.

The email domain's two stores, moved here from
``tests/unit/evarness/foundation/test_persistence/test_store_connections.py`` with
their modules (core/SDK boundary plan, PR 2).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evarness_personal.email.accounts import EmailAccountStore
from evarness_personal.email.category_store import CategoryStore
from evarness_personal.email.store import EmailStore
from evarness_personal.plugins.email_workflows.judgments import JudgmentStore


def _categories(path: Path) -> Any:
    store = CategoryStore(db_path=path)
    store.ensure_schema()
    return store


def _email_accounts(path: Path) -> Any:
    store = EmailAccountStore(db_path=path)
    store.ensure_schema()
    return store


def _email(path: Path) -> Any:
    # Wrapped a bare connection in ``with``: committed, never closed. Schema and a read.
    store = EmailStore(db_path=path)
    store.ensure_schema()
    store.get_cursor("p", "a", "c")
    return store


def _judgments(path: Path) -> Any:
    store = JudgmentStore(db_path=path)
    store.ensure_schema()
    return store


STORES: dict[str, Callable[[Path], Any]] = {
    "categories": _categories,
    "email": _email,
    "email_accounts": _email_accounts,
    "judgments": _judgments,
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


def test_a_category_write_still_commits(tmp_path: Path) -> None:
    """The store's _connect now closes as well as commits: a write is still there after."""
    store = _categories(tmp_path / "c.db")
    with store._connect() as conn:  # the method changed shape
        conn.execute("CREATE TABLE probe (x INTEGER)")
        conn.execute("INSERT INTO probe VALUES (1)")
    check = sqlite3.connect(tmp_path / "c.db")
    try:
        assert check.execute("SELECT x FROM probe").fetchall() == [(1,)]
    finally:
        check.close()
