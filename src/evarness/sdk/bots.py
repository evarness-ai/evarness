"""Bot identity (ADR-0138): the kernel's one row per bot, for the platform that runs bots.

The platform defines a bot (a YAML file the kernel never reads) and registers it here
once: ``register_bot(name, workspace_path, definition_sha256)`` creates the ``bot_identity``
row (status ``active``, vault namespace and memory scope both ``bot:<name>``; the workspace
must be ``<home>/bots/<name>``, see ``bot_workspace``) or, for a
name already registered, records the new definition hash and returns the one row. From
then on the kernel denies every governed call of a bot that is not ``active``, at
``pre_tool_use`` and ``pre_llm_call``, mid-run included (invariant B1), and stamps the
bot's id on every audit row the bot's process writes.

``set_bot_status(name, "active" | "disabled" | "revoked")`` is the owner's switch;
``revoked`` is terminal (``BotRevokedError`` on any later change). A home holds one row per
bot. The platform starts a bot's runtime with ``EVARNESS_BOT=<name>``; ``current_bot()`` is
that bot's row, ``None`` in a process with no ``EVARNESS_BOT`` (the owner's own assistant),
and ``BotNotFoundError`` when the variable names a bot that was never registered. Each
change leaves an audit row, whichever surface made it (this module, ``evarness bot``,
``/api/v1/bots``).
"""

from __future__ import annotations

import os

from evarness.kernel.governance.bots import (
    BotIdentity,
    BotNotFoundError,
    BotRevokedError,
    BotService,
    bot_workspace,
    current_bot_identity,
)


def _actor() -> str:
    return f"sdk:{os.environ.get('USER', 'unknown')}"


def register_bot(name: str, workspace_path: str, definition_sha256: str) -> BotIdentity:
    """Create the bot's identity row, or re-register it with a new definition hash."""
    return BotService().register(name, workspace_path, definition_sha256, actor=_actor())


def current_bot() -> BotIdentity | None:
    """The bot this process runs, or ``None`` for the owner's assistant.

    The name is ``EVARNESS_BOT`` as the process read it once (the same read the kernel's
    status check uses). A read: it never creates ``bots.db`` and writes no audit row.
    """
    return current_bot_identity()


def set_bot_status(name: str, status: str) -> BotIdentity:
    """``active`` or ``disabled`` (both ways), or ``revoked`` (terminal).

    Refused with ``PermissionError`` in a bot's own process (``EVARNESS_BOT`` set), as is
    ``register_bot``: the switch is the owner's.
    """
    return BotService().set_status(name, status, actor=_actor())


__all__ = [
    "BotIdentity",
    "BotNotFoundError",
    "BotRevokedError",
    "bot_workspace",
    "current_bot",
    "register_bot",
    "set_bot_status",
]
