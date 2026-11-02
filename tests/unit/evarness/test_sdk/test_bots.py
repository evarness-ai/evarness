"""``evarness.sdk.bots`` and a bot on a real governed turn (ADR-0138).

The SDK functions a platform calls, then the whole composition root through the
governed test harness: a process started with ``EVARNESS_BOT`` stamps its bot's id on
every row of the turn and runs nothing once the bot is disabled, and a process without
it (the owner's single assistant) behaves exactly as before bots existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evarness.kernel.governance.bots import BOT_ENV, default_bots_db_path, reset_process_bot
from evarness.sdk import PluginAPI
from evarness.sdk.bots import (
    BotNotFoundError,
    BotRevokedError,
    bot_workspace,
    current_bot,
    register_bot,
    set_bot_status,
)
from evarness.testing import harness, plugin

SHA = "f" * 64

_SCRIPT: dict[str, Any] = {
    "rules": [
        {
            "name": "answer",
            "match": {"user": r"(?s)Observation:.*sunny"},
            "reply": {"content": "Thought: Done.\nFinal Answer: It is sunny."},
        },
        {
            "name": "look",
            "match": {"user": r"User: What is the weather"},
            "reply": {"content": "Thought: Look.\nAction: weather_now\nAction Input: {}"},
        },
    ]
}


def _weather(api: PluginAPI) -> None:
    api.register_tool("weather_now", "The weather now. Args: {}.", lambda args: "sunny")


def _plugin() -> Any:
    return plugin(
        _weather,
        manifest={
            "name": "weather",
            "provides": ["tool"],
            "tools": {"weather_now": {"effect": "read"}},
        },
    )


# ----------------------------------------------------------------------------- the SDK
def test_the_sdk_registers_switches_and_reads_the_current_bot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    monkeypatch.setenv("EVARNESS_GOVERNANCE_AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.delenv(BOT_ENV, raising=False)
    assert current_bot() is None
    assert not default_bots_db_path().exists()  # a read made no file
    row = register_bot("helper", str(bot_workspace("helper")), SHA)
    assert row.status == "active"
    monkeypatch.setenv(BOT_ENV, "helper")
    assert current_bot() is None  # pinned: still the owner's process
    reset_process_bot()  # a new process, started as the bot
    assert current_bot() == row
    with pytest.raises(PermissionError):
        set_bot_status("helper", "disabled")  # the bot cannot flip its own switch
    monkeypatch.delenv(BOT_ENV)
    reset_process_bot()  # the owner's process again
    assert set_bot_status("helper", "disabled").status == "disabled"
    assert set_bot_status("helper", "revoked").revoked_at is not None
    with pytest.raises(BotRevokedError):
        set_bot_status("helper", "active")
    with pytest.raises(BotNotFoundError):
        set_bot_status("ghost", "disabled")


# ------------------------------------------------------------------ a real governed turn
def _seeded_bots_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path, status: str = "active"
) -> tuple[bytes, str]:
    """A ``bots.db`` holding ``helper`` for the harness home ``home``, as the owner's process
    writes it before the platform starts the bot (a bot's own process may not)."""
    import sqlite3

    from evarness.kernel.governance.bots import BotIdentityStore

    monkeypatch.setenv("EVARNESS_HOME", str(home))
    db = tmp_path / "seed" / "bots.db"
    store = BotIdentityStore(db_path=db)
    row = store.register("helper", str(home / "bots" / "helper"), SHA)
    if status != "active":
        store.set_status("helper", status)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    return db.read_bytes(), row.bot_id


def test_a_bot_process_stamps_every_row_of_its_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = (tmp_path / "home").resolve()
    seeded, bot_id = _seeded_bots_db(tmp_path, monkeypatch, home)
    with harness(
        plugins=[_plugin()],
        fake_model=_SCRIPT,
        env={BOT_ENV: "helper"},
        home=home,
        seed={"governance/bots.db": seeded},
    ) as h:
        assert "sunny" in h.chat("What is the weather").text
        bot = current_bot()
        assert bot is not None and bot.bot_id == bot_id
        with pytest.raises(PermissionError):
            set_bot_status("helper", "active")  # the bot cannot flip its own switch
        rows = h.audit_rows()
    points = {r.hook_point for r in rows}
    # The turn ran a tool and two model calls: rows from before, during and after both.
    assert {"pre_turn", "pre_llm_call", "pre_tool_use", "post_tool_use"} <= points, points
    missing = sorted({(r.hook_point, r.plugin) for r in rows if r.bot_id != bot_id})
    assert missing == [], missing


def test_a_disabled_bot_runs_no_model_call_and_no_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = (tmp_path / "home").resolve()
    seeded, bot_id = _seeded_bots_db(tmp_path, monkeypatch, home, status="disabled")
    with harness(
        plugins=[_plugin()],
        fake_model=_SCRIPT,
        env={BOT_ENV: "helper"},
        home=home,
        seed={"governance/bots.db": seeded},
    ) as h:
        h.chat("What is the weather")
        calls = h.model_calls()
        rows = h.audit_rows()
    denied = [r for r in rows if r.plugin == "bot_status" and r.decision == "deny"]
    assert calls == ()
    assert denied and not [r for r in rows if r.hook_point == "post_tool_use"]
    assert {r.bot_id for r in rows} == {bot_id}


def test_the_single_assistant_is_unchanged() -> None:
    """No ``EVARNESS_BOT`` and no ``bots.db``: the turn runs as it did before bots, no row
    at any hook point carries a ``bot_id``, and the home gains no ``bots.db``."""
    with harness(plugins=[_plugin()], fake_model=_SCRIPT) as h:
        answer = h.chat("What is the weather").text
        calls = [c.rule for c in h.model_calls()]
        rows = h.audit_rows()
        bots_db = h.home / "governance" / "bots.db"
        made = bots_db.exists()
    assert "sunny" in answer
    assert calls == ["look", "answer"]
    assert rows and all(r.bot_id is None for r in rows)
    assert not [r for r in rows if r.plugin == "bot_status"]  # no hook, no extra row
    assert not made
