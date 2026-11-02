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

from evarness.kernel.governance.bots import BOT_ENV, default_bots_db_path
from evarness.sdk import PluginAPI
from evarness.sdk.bots import (
    BotNotFoundError,
    BotRevokedError,
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
    row = register_bot("helper", str(tmp_path / "bots" / "helper"), SHA)
    assert row.status == "active"
    assert current_bot() is None  # still the owner's process
    monkeypatch.setenv(BOT_ENV, "helper")
    assert current_bot() == row
    assert set_bot_status("helper", "disabled").status == "disabled"
    assert set_bot_status("helper", "revoked").revoked_at is not None
    with pytest.raises(BotRevokedError):
        set_bot_status("helper", "active")
    with pytest.raises(BotNotFoundError):
        set_bot_status("ghost", "disabled")


# ------------------------------------------------------------------ a real governed turn
def test_a_bot_process_stamps_every_row_of_its_turn() -> None:
    with harness(plugins=[_plugin()], fake_model=_SCRIPT, env={BOT_ENV: "helper"}) as h:
        row = register_bot("helper", str(h.home / "bots" / "helper"), SHA)
        assert h.chat("What is the weather").text
        rows = [r for r in h.audit_rows() if r.hook_point in ("pre_llm_call", "pre_tool_use")]
    assert {r.hook_point for r in rows} == {"pre_llm_call", "pre_tool_use"}
    assert {r.bot_id for r in rows} == {row.bot_id}


def test_a_disabled_bot_runs_no_model_call_and_no_tool() -> None:
    with harness(plugins=[_plugin()], fake_model=_SCRIPT, env={BOT_ENV: "helper"}) as h:
        row = register_bot("helper", str(h.home / "bots" / "helper"), SHA)
        set_bot_status("helper", "disabled")
        h.chat("What is the weather")
        calls = h.model_calls()
        denied = [r for r in h.audit_rows() if r.plugin == "bot_status" and r.decision == "deny"]
        tools = h.audit_rows(hook_point="post_tool_use")
    assert calls == ()
    assert denied and {r.bot_id for r in denied} == {row.bot_id}
    assert tools == []


def test_the_single_assistant_is_unchanged() -> None:
    """No ``EVARNESS_BOT`` and no ``bots.db``: the turn runs as it did before bots, its
    rows carry no ``bot_id``, and the home gains no ``bots.db``."""
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
