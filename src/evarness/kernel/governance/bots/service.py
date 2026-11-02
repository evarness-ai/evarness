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
    open_existing_store,
    selected_bot_name,
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


class BotService:
    """``BotIdentityStore`` plus the audit row each change leaves."""

    def __init__(
        self, store: BotIdentityStore | None = None, *, ledger: Ledger | None = None
    ) -> None:
        self._store = store if store is not None else BotIdentityStore()
        self._ledger = ledger if ledger is not None else _default_ledger()

    @property
    def store(self) -> BotIdentityStore:
        return self._store

    def register(
        self, name: str, workspace_path: str, definition_sha256: str, *, actor: str
    ) -> BotIdentity:
        """Register (or re-register) a bot; one ``bot.register`` row either way."""
        before = self._store.get(name)
        try:
            row = self._store.register(name, workspace_path, definition_sha256)
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
        try:
            row = self._store.set_status(name, status)
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
        return self._store.get(name)

    def current(self) -> BotIdentity | None:
        """The bot this process runs (``EVARNESS_BOT``), or ``None`` for the owner's own
        assistant. ``BotNotFoundError`` when the variable names a bot with no row."""
        return current_bot_identity(self._store)

    def list(self) -> list[BotIdentity]:
        return self._store.list()

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
        self._ledger.record(
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
                "bot_id": row.bot_id,
                "bot_name": row.name,
                "status": row.status,
                "definition_sha256": row.definition_sha256,
                "actor": actor,
            },
        )


def current_bot_identity(store: BotIdentityStore | None = None) -> BotIdentity | None:
    """The row of the bot this process runs, read without creating ``bots.db``.

    ``None`` when ``EVARNESS_BOT`` is unset. ``BotNotFoundError`` when it names a bot the
    home has no row for; ``ValueError`` when it is not a valid name.
    """
    name = selected_bot_name()
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


__all__ = ["BOT_IDENTITY_HOOK", "BotService", "Ledger", "current_bot_identity"]
