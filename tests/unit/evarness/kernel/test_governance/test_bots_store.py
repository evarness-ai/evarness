"""BotIdentityStore and BotService (ADR-0138, spec section 4.6).

The row, its rules and the audit row each change leaves. Every test gets a home of its
own, so ``evarness_home()`` (the workspace rule) and ``governance_data_dir()`` (where
``bots.db`` lives) point at ``tmp_path``.
"""

from __future__ import annotations

import json
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
    BotWriteRefusedError,
    bot_workspace,
    current_bot_identity,
    default_bots_db_path,
    is_bot_process,
    open_existing_store,
    process_bot,
    process_bot_name,
    reset_process_bot,
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
    if os.name == "posix":  # Windows has no POSIX file modes (folder ACLs protect it)
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


def test_register_refuses_a_moved_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace never moves: a home relocated under a running bot's row is refused."""
    monkeypatch.delenv(BOT_ENV, raising=False)
    store = BotIdentityStore(db_path=tmp_path / "bots.db")
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path / "first"))
    store.register("helper", str(tmp_path / "first" / "bots" / "helper"), SHA_A)
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path / "second"))
    with pytest.raises(ValueError, match="never moves"):
        store.register("helper", str(tmp_path / "second" / "bots" / "helper"), SHA_A)


def test_the_workspace_is_derived_from_the_name(home: Path, store: BotIdentityStore) -> None:
    assert bot_workspace("helper") == home.resolve() / "bots" / "helper"
    row = store.register("helper", str(bot_workspace("helper")), SHA_A)
    assert row.workspace_path == str(home.resolve() / "bots" / "helper")


@pytest.mark.parametrize(
    "where",
    [
        "governance",  # the kernel's folder: bots.db, audit.db
        "workspace",  # the owner's workspace
        "data",  # the owner's data
        ".",  # the home itself
        "bots",  # every bot's parent
        "bots/scout",  # another bot's folder
        "bots/helper/inner",  # inside its own folder
        "bots/helper/..",  # resolves to bots
    ],
)
def test_any_other_folder_in_the_home_is_refused(
    home: Path, store: BotIdentityStore, where: str
) -> None:
    with pytest.raises(ValueError, match="<home>/bots/<name>"):
        store.register("helper", str(home / where), SHA_A)
    assert store.list() == []


@pytest.mark.parametrize("where", ["outside", "relative", "empty"])
def test_a_folder_outside_the_home_or_relative_is_refused(
    home: Path, store: BotIdentityStore, tmp_path_factory: pytest.TempPathFactory, where: str
) -> None:
    path = {
        "outside": str(tmp_path_factory.mktemp("elsewhere") / "bots" / "helper"),
        "relative": "bots/helper",
        "empty": "",
    }[where]
    with pytest.raises(ValueError, match="workspace_path"):
        store.register("helper", path, SHA_A)
    assert store.list() == []


def test_a_differently_cased_spelling_is_the_same_folder_and_cannot_be_shared(
    home: Path, store: BotIdentityStore
) -> None:
    """On a case-insensitive file system ``Bots/HELPER`` is ``bots/helper``: it is accepted
    for ``helper`` only, stored in the canonical spelling, and refused for any other bot."""
    (home / "bots" / "helper").mkdir(parents=True)
    row = store.register("helper", str(home / "BOTS" / "HELPER"), SHA_A)
    assert row.workspace_path == str(bot_workspace("helper"))
    with pytest.raises(ValueError, match="<home>/bots/<name>"):
        store.register("scout", str(home / "Bots" / "Helper"), SHA_A)
    assert [r.name for r in store.list()] == ["helper"]


@pytest.mark.parametrize("target", ["governance", "workspace"])
def test_a_workspace_symlinked_elsewhere_is_refused(
    home: Path, store: BotIdentityStore, target: str
) -> None:
    (home / target).mkdir(exist_ok=True)
    (home / "bots").mkdir()
    (home / "bots" / "helper").symlink_to(home / target, target_is_directory=True)
    with pytest.raises(ValueError, match="<home>/bots/<name>"):
        store.register("helper", str(home / "bots" / "helper"), SHA_A)


def test_a_bots_folder_symlinked_elsewhere_is_refused(home: Path, store: BotIdentityStore) -> None:
    (home / "governance").mkdir(exist_ok=True)
    (home / "bots").symlink_to(home / "governance", target_is_directory=True)
    with pytest.raises(ValueError, match="<home>/bots/<name>"):
        store.register("helper", str(home / "bots" / "helper"), SHA_A)


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


# --------------------------------------------------------------------- the process's bot
def test_process_bot_reads_evarness_bot_once(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert process_bot() == (None, None) and process_bot_name() is None
    assert not is_bot_process()
    monkeypatch.setenv(BOT_ENV, "helper")
    assert process_bot_name() is None  # pinned: a later change in the process is ignored
    reset_process_bot()
    assert process_bot_name() == "helper" and is_bot_process()
    monkeypatch.setenv(BOT_ENV, "scout")
    assert process_bot_name() == "helper"


@pytest.mark.parametrize("value", ["  ", ""])
def test_a_blank_evarness_bot_is_unset(
    home: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(BOT_ENV, value)
    assert process_bot() == (None, None)


def test_an_invalid_evarness_bot_is_a_bot_process_with_an_error(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BOT_ENV, "Not A Name")
    name, error = process_bot()
    assert name is None and error and is_bot_process()
    with pytest.raises(ValueError):
        process_bot_name()


def test_current_bot_identity(
    home: Path, store: BotIdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = store.register("helper", _ws(home, "helper"), SHA_A)
    store.register("scout", _ws(home, "scout"), SHA_A)
    assert current_bot_identity() is None  # no EVARNESS_BOT: the owner's own assistant
    monkeypatch.setenv(BOT_ENV, "helper")
    reset_process_bot()
    assert current_bot_identity() == row
    monkeypatch.setenv(BOT_ENV, "ghost")
    reset_process_bot()
    with pytest.raises(BotNotFoundError):
        current_bot_identity()


def test_current_bot_identity_on_a_home_without_bots_creates_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert current_bot_identity() is None
    monkeypatch.setenv(BOT_ENV, "helper")
    reset_process_bot()
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
        assert r["payload"]["subject_bot_id"] == row.bot_id and r["run_id"] == "bot:helper"
        # Ids, the status, the hash and the actor: never the workspace or the definition.
        assert set(r["payload"]) == {
            "event",
            "subject_bot_id",
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
    assert written.hook_point == BOT_IDENTITY_HOOK
    # The owner's process is no bot: the row is ABOUT the bot, not written BY it.
    assert written.bot_id is None
    assert json.loads(written.payload_json)["subject_bot_id"] == row.bot_id


def test_a_bots_own_process_may_not_change_any_identity(
    home: Path, store: BotIdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _Ledger()
    store.register("helper", _ws(home, "helper"), SHA_A)
    store.set_status("helper", "disabled")
    monkeypatch.setenv(BOT_ENV, "helper")
    reset_process_bot()
    service = BotService(store, ledger=ledger)
    with pytest.raises(BotWriteRefusedError):
        service.set_status("helper", "active", actor="sdk:bot")
    with pytest.raises(BotWriteRefusedError):
        service.register("scout", _ws(home, "scout"), SHA_A, actor="sdk:bot")
    assert isinstance(BotWriteRefusedError("x"), PermissionError)
    assert store.get("helper").status == "disabled"  # type: ignore[union-attr]
    assert ledger.rows == []


def test_reads_and_refused_changes_open_neither_store_nor_audit_log(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evarness.kernel.governance.bots import service as service_module

    def _no_ledger() -> Any:
        raise AssertionError("the audit log was opened")

    monkeypatch.setattr(service_module, "_default_ledger", _no_ledger)
    service = BotService()
    assert service.list() == [] and service.get("helper") is None
    with pytest.raises(BotNotFoundError):
        service.set_status("ghost", "disabled", actor="cli:owner")
    assert not default_bots_db_path().exists()
