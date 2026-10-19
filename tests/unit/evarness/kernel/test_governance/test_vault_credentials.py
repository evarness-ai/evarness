"""Tests for the Keychain-backed credentials module (ADR-0011).

Mocks the ``keyring`` library at the module level so tests never touch
the real system keychain. Redirects ``CREDENTIALS_DIR`` to a per-test
tmp_path so filesystem operations don't pollute ``~/.evarness/``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import keyring.errors
import pytest

from evarness.kernel.governance.vault import credentials


@pytest.fixture
def tmp_creds_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the module's CREDENTIALS_DIR to a tmp_path for the test."""
    redirected = tmp_path / "credentials"
    monkeypatch.setattr(credentials, "CREDENTIALS_DIR", redirected)
    return redirected


# ─── Keychain token operations ────────────────────────────────────────


def test_save_load_token_roundtrip() -> None:
    """save_token then load_token returns the same value."""
    store: dict[tuple[str, str], str] = {}

    def fake_set(service: str, account: str, value: str) -> None:
        store[(service, account)] = value

    def fake_get(service: str, account: str) -> str | None:
        return store.get((service, account))

    with (
        patch.object(credentials.keyring, "set_password", fake_set),
        patch.object(credentials.keyring, "get_password", fake_get),
    ):
        credentials.save_token("gmail", "user@example.com", "abc123")
        assert credentials.load_token("gmail", "user@example.com") == "abc123"


def _owner_edit_error() -> keyring.errors.PasswordSetError:
    return keyring.errors.PasswordSetError(
        "Can't store password on keychain: (-25244, 'Unknown Error')"
    )


def test_save_token_recreates_an_entry_another_program_owns() -> None:
    """macOS refuses to edit an item another program made; it is deleted and rewritten."""
    store: dict[tuple[str, str], str] = {("evarness-imap", "imap:me@example.com"): "old"}
    owned_elsewhere = {("evarness-imap", "imap:me@example.com")}

    def fake_set(service: str, account: str, value: str) -> None:
        if (service, account) in owned_elsewhere:
            raise _owner_edit_error()
        store[(service, account)] = value

    def fake_delete(service: str, account: str) -> None:
        owned_elsewhere.discard((service, account))
        store.pop((service, account), None)

    with (
        patch.object(credentials.keyring, "set_password", fake_set),
        patch.object(credentials.keyring, "delete_password", fake_delete),
    ):
        credentials.save_token("imap", "imap:me@example.com", "new", recreate_foreign=True)

    assert store == {("evarness-imap", "imap:me@example.com"): "new"}


def test_save_token_names_the_fix_when_the_entry_cannot_be_recreated() -> None:
    def fake_set(service: str, account: str, value: str) -> None:
        raise _owner_edit_error()

    def fake_delete(service: str, account: str) -> None:
        raise keyring.errors.PasswordDeleteError("denied")

    with (
        patch.object(credentials.keyring, "set_password", fake_set),
        patch.object(credentials.keyring, "delete_password", fake_delete),
        pytest.raises(credentials.CredentialStoreError) as caught,
    ):
        credentials.save_token("imap", "imap:me@example.com", "new", recreate_foreign=True)

    assert 'security delete-generic-password -s evarness-imap -a "imap:me@example.com"' in str(
        caught.value
    )


def test_save_token_does_not_delete_on_other_keychain_errors() -> None:
    """Only the owner-edit failure deletes anything; a locked or broken keychain does not."""
    deleted: list[tuple[str, str]] = []

    def fake_set(service: str, account: str, value: str) -> None:
        raise keyring.errors.PasswordSetError("Can't store password on keychain: (-1, 'other')")

    with (
        patch.object(credentials.keyring, "set_password", fake_set),
        patch.object(credentials.keyring, "delete_password", lambda s, a: deleted.append((s, a))),
        pytest.raises(keyring.errors.PasswordSetError),
    ):
        credentials.save_token("gmail", "me@example.com", "new", recreate_foreign=True)

    assert deleted == []


def test_save_token_does_not_recreate_unless_asked() -> None:
    deleted: list[tuple[str, str]] = []

    def fake_set(service: str, account: str, value: str) -> None:
        raise _owner_edit_error()

    with (
        patch.object(credentials.keyring, "set_password", fake_set),
        patch.object(credentials.keyring, "delete_password", lambda s, a: deleted.append((s, a))),
        pytest.raises(keyring.errors.PasswordSetError),
    ):
        credentials.save_token("gmail", "me@example.com", "new")

    assert deleted == []


def test_save_token_never_deletes_the_vault_master_key() -> None:
    """Everything the vault holds is encrypted under it; even an opt-in cannot remove it."""
    deleted: list[tuple[str, str]] = []

    def fake_set(service: str, account: str, value: str) -> None:
        raise _owner_edit_error()

    with (
        patch.object(credentials.keyring, "set_password", fake_set),
        patch.object(credentials.keyring, "delete_password", lambda s, a: deleted.append((s, a))),
        pytest.raises(keyring.errors.PasswordSetError),
    ):
        credentials.save_token("vault", "master-key", "k", recreate_foreign=True)

    assert deleted == []


def test_load_token_missing_returns_none() -> None:
    """load_token returns None when no entry exists."""
    with patch.object(credentials.keyring, "get_password", return_value=None):
        assert credentials.load_token("gmail", "missing@example.com") is None


def test_delete_token_idempotent() -> None:
    """delete_token swallows PasswordDeleteError when entry is absent."""

    def raising_delete(service: str, account: str) -> None:
        raise keyring.errors.PasswordDeleteError("not found")

    with patch.object(credentials.keyring, "delete_password", raising_delete):
        # Must not raise.
        credentials.delete_token("gmail", "missing@example.com")


def test_service_name_prefix() -> None:
    """Saved tokens use the 'evarness-<provider>' service prefix."""
    captured: list[tuple[str, str, str]] = []

    def fake_set(service: str, account: str, value: str) -> None:
        captured.append((service, account, value))

    with patch.object(credentials.keyring, "set_password", fake_set):
        credentials.save_token("gcalendar", "x@y.com", "tok")

    assert captured == [("evarness-gcalendar", "x@y.com", "tok")]


# ─── Non-secret config-file operations ────────────────────────────────


def test_save_config_creates_dir_with_700(tmp_creds_dir: Path) -> None:
    """save_config creates the credentials/ dir with mode 0o700 on POSIX."""
    credentials.save_config("gmail", {"client_id": "x"})

    assert tmp_creds_dir.is_dir()
    if os.name == "posix":
        mode = oct(tmp_creds_dir.stat().st_mode & 0o777)
        assert mode == "0o700", f"expected 0o700, got {mode}"


def test_load_config_missing_returns_none(tmp_creds_dir: Path) -> None:
    """load_config returns None when the config file is absent."""
    assert credentials.load_config("nonexistent") is None


def test_save_config_roundtrip(tmp_creds_dir: Path) -> None:
    """save_config then load_config returns the same dict."""
    payload = {"client_id": "abc", "scopes": ["read", "write"], "last_auth_at": "2026-05-25"}
    credentials.save_config("gmail", payload)

    assert credentials.load_config("gmail") == payload


def test_list_providers_from_filesystem(tmp_creds_dir: Path) -> None:
    """list_providers enumerates config files only, sorted alphabetically."""
    credentials.save_config("gmail", {"x": 1})
    credentials.save_config("gcalendar", {"y": 2})
    # An unrelated file in the dir should be ignored.
    (tmp_creds_dir / "README.md").write_text("# notes")

    assert credentials.list_providers() == ["gcalendar", "gmail"]
