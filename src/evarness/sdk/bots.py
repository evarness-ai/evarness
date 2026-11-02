"""Bot identity (ADR-0138): the kernel's one row per bot, for the platform that runs bots.

The platform defines a bot (a YAML file the kernel never reads) and registers it here
once: ``register_bot(name, workspace_path, definition_sha256)`` creates the ``bot_identity``
row (status ``active``, vault namespace and memory scope both ``bot:<name>``) or, for a
name already registered, records the new definition hash and returns the one row. From
then on the kernel denies every governed call of a bot that is not ``active``, at
``pre_tool_use`` and ``pre_llm_call``, mid-run included (invariant B1), and stamps the
bot's id on every audit row of the call.

``set_bot_status(name, "active" | "disabled" | "revoked")`` is the owner's switch;
``revoked`` is terminal (``BotRevokedError`` on any later change). ``current_bot()`` is the
one bot of this home, ``None`` on a plain single-assistant install. Each change leaves an
audit row, whichever surface made it (this module, ``evarness bot``, ``/api/v1/bot``).
"""

from __future__ import annotations

import os

from evarness.kernel.governance.bots import (
    BotIdentity,
    BotIdentityStore,
    BotNotFoundError,
    BotRevokedError,
    BotService,
)


def _actor() -> str:
    return f"sdk:{os.environ.get('USER', 'unknown')}"


def register_bot(name: str, workspace_path: str, definition_sha256: str) -> BotIdentity:
    """Create the bot's identity row, or re-register it with a new definition hash."""
    return BotService().register(name, workspace_path, definition_sha256, actor=_actor())


def current_bot() -> BotIdentity | None:
    """The one bot of this home, or ``None`` on a single-assistant install."""
    return BotService().current()


def set_bot_status(name: str, status: str) -> BotIdentity:
    """``active`` or ``disabled`` (both ways), or ``revoked`` (terminal)."""
    return BotService().set_status(name, status, actor=_actor())


__all__ = [
    "BotIdentity",
    "BotIdentityStore",
    "BotNotFoundError",
    "BotRevokedError",
    "current_bot",
    "register_bot",
    "set_bot_status",
]
