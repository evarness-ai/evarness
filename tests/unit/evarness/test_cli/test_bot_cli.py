"""``evarness bot``: on the store directly, through ``BotService`` (ADR-0138).

No API runs in these tests: the CLI must work with none, the way ``evarness approvals``
does. Each change leaves the same audit row the API and the SDK leave.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from evarness.kernel.governance.audit import AuditLog
from evarness.kernel.governance.bots import BOT_ENV, BOT_IDENTITY_HOOK, default_bots_db_path
from evarness.main import app

runner = CliRunner()
SHA = "e" * 64


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    monkeypatch.setenv("EVARNESS_GOVERNANCE_AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.delenv(BOT_ENV, raising=False)
    return tmp_path


def _register(home: Path, name: str = "helper") -> Any:
    return runner.invoke(
        app,
        [
            "bot",
            "register",
            name,
            "--workspace",
            str(home / "bots" / name),
            "--definition-sha256",
            SHA,
            "--json",
        ],
    )


def test_list_and_show_on_a_home_without_bots_create_nothing(home: Path) -> None:
    result = runner.invoke(app, ["bot", "list"])
    assert result.exit_code == 0 and "No bots registered" in result.output
    result = runner.invoke(app, ["bot", "list", "--json"])
    assert json.loads(result.output) == {"bots": []}
    result = runner.invoke(app, ["bot", "show", "helper"])
    assert result.exit_code == 1 and "no bot named" in result.output
    assert not default_bots_db_path().exists()


def test_register_list_show(home: Path) -> None:
    result = _register(home)
    assert result.exit_code == 0, result.output
    bot = json.loads(result.output)["bot"]
    assert bot["name"] == "helper" and bot["status"] == "active"
    listed = runner.invoke(app, ["bot", "list"])
    assert listed.exit_code == 0 and "helper" in listed.output and "active" in listed.output
    shown = json.loads(runner.invoke(app, ["bot", "show", "helper", "--json"]).output)
    assert shown["bot"]["bot_id"] == bot["bot_id"]
    plain = runner.invoke(app, ["bot", "show", "helper"])
    assert plain.exit_code == 0 and "bot:helper" in plain.output


def test_status_changes_and_their_audit_rows(home: Path) -> None:
    bot_id = json.loads(_register(home).output)["bot"]["bot_id"]
    off = runner.invoke(app, ["bot", "status", "helper", "disabled"])
    assert off.exit_code == 0 and "disabled" in off.output
    on = runner.invoke(app, ["bot", "status", "helper", "active", "--json"])
    assert json.loads(on.output)["bot"]["status"] == "active"
    refused = runner.invoke(app, ["bot", "status", "helper", "revoked"])
    assert refused.exit_code == 1 and "--yes" in refused.output
    gone = runner.invoke(app, ["bot", "status", "helper", "revoked", "--yes"])
    assert gone.exit_code == 0 and "revoked" in gone.output
    again = runner.invoke(app, ["bot", "status", "helper", "active"])
    assert again.exit_code == 1 and "terminal" in again.output
    rows = [r for r in AuditLog(db_path=home / "audit.db").query() if r.plugin == "BotService"]
    assert [r.hook_point for r in rows] == [BOT_IDENTITY_HOOK] * 4
    assert {r.bot_id for r in rows} == {None}  # written by the owner's process, not a bot
    assert {json.loads(r.payload_json)["subject_bot_id"] for r in rows} == {bot_id}
    assert [r.reason.rsplit(" ", 1)[-1] for r in rows[1:]] == ["disabled", "active", "revoked"]


def test_the_errors(home: Path) -> None:
    missing = runner.invoke(app, ["bot", "status", "ghost", "disabled"])
    assert missing.exit_code == 1 and "no bot named" in missing.output
    outside = runner.invoke(
        app,
        ["bot", "register", "rogue", "--workspace", "/elsewhere", "--definition-sha256", SHA],
    )
    assert outside.exit_code == 1 and "workspace_path" in outside.output
    bad = runner.invoke(
        app,
        [
            "bot",
            "register",
            "Bad Name",
            "--workspace",
            str(home / "bots" / "x"),
            "--definition-sha256",
            SHA,
        ],
    )
    assert bad.exit_code == 1 and "bot name" in bad.output
    _register(home)
    paused = runner.invoke(app, ["bot", "status", "helper", "paused"])
    assert paused.exit_code == 1 and "status must be one of" in paused.output


def test_unknown_bot_status_change_creates_no_file(home: Path) -> None:
    result = runner.invoke(app, ["bot", "status", "ghost", "disabled"])
    assert result.exit_code == 1 and "no bot named" in result.output
    assert not default_bots_db_path().exists()
    assert not (home / "audit.db").exists()


def test_register_defaults_to_the_derived_workspace(home: Path) -> None:
    result = runner.invoke(app, ["bot", "register", "helper", "--definition-sha256", SHA, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["bot"]["workspace_path"] == str(
        home.resolve() / "bots" / "helper"
    )


def test_every_change_is_refused_in_a_bots_own_process(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evarness.kernel.governance.bots import reset_process_bot

    _register(home)
    monkeypatch.setenv(BOT_ENV, "helper")
    reset_process_bot()
    result = runner.invoke(app, ["bot", "status", "helper", "disabled"])
    assert result.exit_code == 1 and "EVARNESS_BOT" in result.output
    assert (
        json.loads(runner.invoke(app, ["bot", "show", "helper", "--json"]).output)["bot"]["status"]
        == "active"
    )
