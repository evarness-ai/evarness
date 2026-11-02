"""BotService -- the store, with one audit row per event (ADR-0138).

Registering a bot and flipping its status are governance events: the row that decides
whether every later call runs. Each surface (the SDK, the API, the CLI) goes through
this service so the ledger shows the event whichever way it was made, as the device
service does for pairing. The payload carries ids and the status, never the
definition or anything from the workspace.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from evarness.kernel.governance.bots.store import (
    BotIdentity,
    BotIdentityStore,
    BotNotFoundError,
    is_bot_process,
    open_existing_store,
    process_bot_name,
)

logger = logging.getLogger(__name__)

#: ``hook_point`` of the rows this service writes.
BOT_IDENTITY_HOOK = "bot_identity"


class Ledger(Protocol):
    """The slice of ``AuditLog`` this module writes to."""

    def record(  # AuditLog.record's own signature
        self,
        *,
        run_id: str,
        step_id: int | None,
        agent_type: str,
        hook_point: str,
        plugin: str,
        decision: str,
        severity: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> int: ...


class BotWriteRefusedError(PermissionError):
    """A bot's own process may not register a bot or change a status (its own included)."""


class BotService:
    """``BotIdentityStore`` plus the audit row each change leaves.

    Both are opened on first need: a read or a refused change on a home that never
    registered a bot creates no ``bots.db`` and opens no audit log. Only a register
    creates the file. Writes are refused in a bot's own process (``EVARNESS_BOT`` set):
    the owner's switch belongs to the owner, never to the bot it switches.
    """

    def __init__(
        self, store: BotIdentityStore | None = None, *, ledger: Ledger | None = None
    ) -> None:
        self._store = store
        self._ledger = ledger

    def _existing(self) -> BotIdentityStore | None:
        if self._store is None:
            self._store = open_existing_store()
        return self._store

    def _created(self) -> BotIdentityStore:
        if self._store is None:
            self._store = BotIdentityStore()
        return self._store

    def _audit(self) -> Ledger:
        if self._ledger is None:
            self._ledger = _default_ledger()
        return self._ledger

    @staticmethod
    def _refuse_in_a_bot(event: str, name: str, actor: str) -> None:
        if is_bot_process():
            logger.warning(
                "%s refused name=%r actor=%s error=BotWriteRefusedError", event, name, actor
            )
            raise BotWriteRefusedError(
                f"{event}: a bot's own process (EVARNESS_BOT set) may not change a bot identity"
            )

    def register(
        self, name: str, workspace_path: str, definition_sha256: str, *, actor: str
    ) -> BotIdentity:
        """Register (or re-register) a bot; one ``bot.register`` row either way."""
        self._refuse_in_a_bot("bot.register", name, actor)
        existing = self._existing()
        before = existing.get(name) if existing is not None else None
        try:
            row = self._created().register(name, workspace_path, definition_sha256)
        except (ValueError, BotNotFoundError) as exc:
            logger.warning(
                "bot.register refused name=%r actor=%s error=%s",
                name,
                actor,
                exc.__class__.__name__,
            )
            raise
        logger.info(
            "bot.register bot_id=%s name=%s status=%s created=%s actor=%s",
            row.bot_id,
            row.name,
            row.status,
            before is None,
            actor,
        )
        self._record(
            "allow",
            row,
            actor=actor,
            reason=(
                f"bot {row.name!r} registered"
                if before is None
                else f"bot {row.name!r} re-registered (definition updated)"
            ),
            event="bot.register",
        )
        return row

    def set_status(self, name: str, status: str, *, actor: str) -> BotIdentity:
        """Flip the switch or revoke; one ``bot.status`` row naming the new status."""
        self._refuse_in_a_bot("bot.status", name, actor)
        try:
            store = self._existing()
            if store is None:
                raise BotNotFoundError(name)
            row = store.set_status(name, status)
        except (ValueError, BotNotFoundError) as exc:
            logger.warning(
                "bot.status refused name=%r to=%r actor=%s error=%s",
                name,
                status,
                actor,
                exc.__class__.__name__,
            )
            raise
        logger.info(
            "bot.status bot_id=%s name=%s status=%s actor=%s",
            row.bot_id,
            row.name,
            row.status,
            actor,
        )
        self._record(
            "allow",
            row,
            actor=actor,
            reason=f"bot {row.name!r} set to {row.status}",
            event="bot.status",
            severity="warn" if row.status == "revoked" else "info",
        )
        return row

    def get(self, name: str) -> BotIdentity | None:
        store = self._existing()
        return store.get(name) if store is not None else None

    def current(self) -> BotIdentity | None:
        """The bot this process runs (``EVARNESS_BOT``), or ``None`` for the owner's own
        assistant. ``BotNotFoundError`` when the variable names a bot with no row."""
        return current_bot_identity(self._existing())

    def list(self) -> list[BotIdentity]:
        store = self._existing()
        return store.list() if store is not None else []

    def _record(
        self,
        decision: str,
        row: BotIdentity,
        *,
        actor: str,
        reason: str,
        event: str,
        severity: str = "info",
    ) -> None:
        # The audit log (an append-only row per event, no caller key), not the side-effect
        # ledger: there is no key to collide. Ids and the status only.
        self._audit().record(
            run_id=f"bot:{row.name}",
            step_id=None,
            agent_type="bots",
            hook_point=BOT_IDENTITY_HOOK,
            plugin="BotService",
            decision=decision,
            severity=severity,
            reason=reason,
            payload={
                "event": event,
                # The bot the event is about. Not ``bot_id``: that names the bot whose
                # process wrote the row, and the owner's process is no bot.
                "subject_bot_id": row.bot_id,
                "bot_name": row.name,
                "status": row.status,
                "definition_sha256": row.definition_sha256,
                "actor": actor,
            },
        )


def current_bot_identity(store: BotIdentityStore | None = None) -> BotIdentity | None:
    """The row of the bot this process runs, read without creating ``bots.db``.

    The name is the process's pinned read of ``EVARNESS_BOT`` (``process_bot``), the
    same one the status hook uses. ``None`` when it is unset. ``BotNotFoundError`` when it names a bot the
    home has no row for; ``ValueError`` when it is not a valid name.
    """
    name = process_bot_name()
    if name is None:
        return None
    opened = store if store is not None else open_existing_store()
    row = opened.get(name) if opened is not None else None
    if row is None:
        raise BotNotFoundError(name)
    return row


def _default_ledger() -> Ledger:
    from evarness.kernel.governance.audit import AuditLog

    return AuditLog()


__all__ = [
    "BOT_IDENTITY_HOOK",
    "BotService",
    "BotWriteRefusedError",
    "Ledger",
    "current_bot_identity",
]
