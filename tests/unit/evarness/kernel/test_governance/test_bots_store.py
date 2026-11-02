"""BotIdentityStore and BotService (ADR-0138, spec section 4.6).

The row, its rules and the audit row each change leaves. Every test gets a home of its
own, so ``evarness_home()`` (the workspace rule) and ``governance_data_dir()`` (where
``bots.db`` lives) point at ``tmp_path``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from evarness.kernel.governance.bots import (
    BOT_ENV,
    BOT_IDENTITY_HOOK,
    BotIdentityStore,
    BotNotFoundError,
    BotRevokedError,
    BotService,
    current_bot_identity,
    default_bots_db_path,
    open_existing_store,
    selected_bot_name,
    validate_bot_name,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


class _Ledger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, **row: Any) -> int:
        self.rows.append(row)
        return len(self.rows)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    monkeypatch.delenv(BOT_ENV, raising=False)
    return tmp_path


@pytest.fixture
def store(home: Path) -> BotIdentityStore:
    return BotIdentityStore()


def _ws(home: Path, name: str) -> str:
    return str(home / "bots" / name)


# ------------------------------------------------------------------------------- store
def test_register_creates_one_active_row_with_its_scopes(
    home: Path, store: BotIdentityStore
) -> None:
    row = store.register("helper", _ws(home, "helper"), SHA_A.upper())
    assert row.status == "active" and row.active and row.revoked_at is None
    assert row.vault_namespace == "bot:helper" and row.memory_scope == "bot:helper"
    assert row.definition_sha256 == SHA_A  # lower-cased
    assert row.workspace_path == str((home / "bots" / "helper").resolve())
    assert len(row.bot_id) == 26 and row.created_at == row.updated_at
    assert store.list() == [row] and store.get("helper") == row
    assert store.db_path == default_bots_db_path() == home / "governance" / "bots.db"
    assert stat.S_IMODE(os.stat(store.db_path).st_mode) == 0o600


def test_register_again_updates_the_hash_and_keeps_the_one_row(
    home: Path, store: BotIdentityStore
) -> None:
    first = store.register("helper", _ws(home, "helper"), SHA_A)
    second = store.register("helper", _ws(home, "helper"), SHA_B)
    assert second.bot_id == first.bot_id and second.definition_sha256 == SHA_B
    assert second.created_at == first.created_at and second.updated_at >= first.updated_at
    assert len(store.list()) == 1


def test_a_home_holds_one_row_per_bot(home: Path, store: BotIdentityStore) -> None:
    store.register("helper", _ws(home, "helper"), SHA_A)
    store.register("scout", _ws(home, "scout"), SHA_A)
    assert [r.name for r in store.list()] == ["helper", "scout"]


def test_register_refuses_a_moved_workspace(home: Path, store: BotIdentityStore) -> None:
    store.register("helper", _ws(home, "helper"), SHA_A)
    with pytest.raises(ValueError, match="never moves"):
        store.register("helper", _ws(home, "elsewhere"), SHA_A)


@pytest.mark.parametrize("other", ["helper", "helper/inner", "."])
def test_two_bots_never_share_or_nest_a_workspace(
    home: Path, store: BotIdentityStore, other: str
) -> None:
    store.register("helper", _ws(home, "helper"), SHA_A)
    target = (home / "bots" / other) if other != "." else (home / "bots")
    with pytest.raises(ValueError, match="B3"):
        store.register("scout", str(target), SHA_A)
    assert [r.name for r in store.list()] == ["helper"]


@pytest.mark.parametrize("where", ["outside", "home", "relative"])
def test_the_workspace_is_a_folder_inside_the_home(
    home: Path, store: BotIdentityStore, tmp_path_factory: pytest.TempPathFactory, where: str
) -> None:
    path = {
        "outside": str(tmp_path_factory.mktemp("elsewhere") / "helper"),
        "home": str(home),
        "relative": "bots/helper",
    }[where]
    with pytest.raises(ValueError, match="workspace_path"):
        store.register("helper", path, SHA_A)
    assert store.list() == []


@pytest.mark.parametrize("name", ["", "a", "Helper", "1bot", "a.b", "bot:x", "x" * 33, "a b"])
def test_a_name_is_the_definitions_slug(name: str) -> None:
    with pytest.raises(ValueError):
        validate_bot_name(name)


@pytest.mark.parametrize("digest", ["", "abc", "g" * 64, "a" * 63])
def test_the_definition_hash_is_sha256_hex(
    home: Path, store: BotIdentityStore, digest: str
) -> None:
    with pytest.raises(ValueError, match="definition_sha256"):
        store.register("helper", _ws(home, "helper"), digest)


def test_status_goes_both_ways_between_active_and_disabled(
    home: Path, store: BotIdentityStore
) -> None:
    store.register("helper", _ws(home, "helper"), SHA_A)
    assert store.set_status("helper", "disabled").status == "disabled"
    back = store.set_status("helper", "active")
    assert back.status == "active" and back.revoked_at is None


def test_revoked_is_terminal(home: Path, store: BotIdentityStore) -> None:
    store.register("helper", _ws(home, "helper"), SHA_A)
    row = store.set_status("helper", "revoked")
    assert row.status == "revoked" and row.revoked and row.revoked_at is not None
    for status in ("active", "disabled", "revoked"):
        with pytest.raises(BotRevokedError):
            store.set_status("helper", status)
    with pytest.raises(BotRevokedError):
        store.register("helper", _ws(home, "helper"), SHA_A)
    assert store.get("helper") == row


def test_status_of_an_unknown_bot_or_status(home: Path, store: BotIdentityStore) -> None:
    with pytest.raises(BotNotFoundError):
        store.set_status("nobody", "disabled")
    store.register("helper", _ws(home, "helper"), SHA_A)
    with pytest.raises(ValueError, match="status must be one of"):
        store.set_status("helper", "paused")


def test_open_existing_store_never_creates_the_file(home: Path) -> None:
    assert open_existing_store() is None
    assert not default_bots_db_path().exists()
    BotIdentityStore()
    assert open_existing_store() is not None


# --------------------------------------------------------------------- selected bot
def test_selected_bot_name_reads_evarness_bot(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert selected_bot_name() is None
    monkeypatch.setenv(BOT_ENV, "  ")
    assert selected_bot_name() is None
    monkeypatch.setenv(BOT_ENV, "helper")
    assert selected_bot_name() == "helper"
    monkeypatch.setenv(BOT_ENV, "Not A Name")
    with pytest.raises(ValueError):
        selected_bot_name()


def test_current_bot_identity(
    home: Path, store: BotIdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = store.register("helper", _ws(home, "helper"), SHA_A)
    store.register("scout", _ws(home, "scout"), SHA_A)
    assert current_bot_identity() is None  # no EVARNESS_BOT: the owner's own assistant
    monkeypatch.setenv(BOT_ENV, "helper")
    assert current_bot_identity() == row
    monkeypatch.setenv(BOT_ENV, "ghost")
    with pytest.raises(BotNotFoundError):
        current_bot_identity()


def test_current_bot_identity_on_a_home_without_bots_creates_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert current_bot_identity() is None
    monkeypatch.setenv(BOT_ENV, "helper")
    with pytest.raises(BotNotFoundError):
        current_bot_identity()
    assert not default_bots_db_path().exists()


# ------------------------------------------------------------------------- service
def test_the_service_leaves_one_audit_row_per_change(
    home: Path, store: BotIdentityStore, caplog: pytest.LogCaptureFixture
) -> None:
    ledger = _Ledger()
    service = BotService(store, ledger=ledger)
    caplog.set_level("INFO", logger="evarness.kernel.governance.bots.service")
    row = service.register("helper", _ws(home, "helper"), SHA_A, actor="cli:owner")
    service.register("helper", _ws(home, "helper"), SHA_B, actor="cli:owner")
    service.set_status("helper", "disabled", actor="service")
    service.set_status("helper", "revoked", actor="service")
    events = [(r["payload"]["event"], r["payload"]["status"]) for r in ledger.rows]
    assert events == [
        ("bot.register", "active"),
        ("bot.register", "active"),
        ("bot.status", "disabled"),
        ("bot.status", "revoked"),
    ]
    for r in ledger.rows:
        assert r["hook_point"] == BOT_IDENTITY_HOOK and r["plugin"] == "BotService"
        assert r["payload"]["bot_id"] == row.bot_id and r["run_id"] == "bot:helper"
        # Ids, the status, the hash and the actor: never the workspace or the definition.
        assert set(r["payload"]) == {
            "event",
            "bot_id",
            "bot_name",
            "status",
            "definition_sha256",
            "actor",
        }
    assert ledger.rows[-1]["severity"] == "warn"
    messages = [rec.getMessage() for rec in caplog.records]
    assert any(m.startswith("bot.register") and row.bot_id in m for m in messages)
    assert any(m.startswith("bot.status") and "status=revoked" in m for m in messages)


def test_a_refused_change_leaves_no_audit_row_and_logs_the_refusal(
    home: Path, store: BotIdentityStore, caplog: pytest.LogCaptureFixture
) -> None:
    ledger = _Ledger()
    service = BotService(store, ledger=ledger)
    caplog.set_level("WARNING", logger="evarness.kernel.governance.bots.service")
    with pytest.raises(BotNotFoundError):
        service.set_status("nobody", "disabled", actor="cli:owner")
    with pytest.raises(ValueError):
        service.register("helper", "/not/in/the/home", SHA_A, actor="cli:owner")
    assert ledger.rows == []
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("bot.status refused" in m and "BotNotFoundError" in m for m in messages)
    assert any("bot.register refused" in m and "ValueError" in m for m in messages)


def test_the_service_writes_to_the_real_audit_log_with_the_bot_id_column(home: Path) -> None:
    from evarness.kernel.governance.audit import AuditLog

    audit = AuditLog(db_path=home / "audit.db")
    row = BotService(BotIdentityStore(), ledger=audit).register(
        "helper", _ws(home, "helper"), SHA_A, actor="cli:owner"
    )
    (written,) = audit.query()[0:1]
    assert written.hook_point == BOT_IDENTITY_HOOK and written.bot_id == row.bot_id
