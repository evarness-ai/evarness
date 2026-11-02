"""BotService -- the store, with one audit row per event (ADR-0138).

Registering a bot and flipping its status are governance events: the row that decides
whether every later call runs. Each surface (the SDK, the API, the CLI) goes through
this service so the ledger shows the event whichever way it was made, as the device
service does for pairing. The payload carries ids and the status, never the
definition or anything from the workspace.
"""

from __future__ import annotations

from typing import Any, Protocol

from evarness.kernel.governance.bots.store import BotIdentity, BotIdentityStore

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
        row = self._store.register(name, workspace_path, definition_sha256)
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
        row = self._store.set_status(name, status)
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
        return self._store.current()

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


def _default_ledger() -> Ledger:
    from evarness.kernel.governance.audit import AuditLog

    return AuditLog()


__all__ = ["BOT_IDENTITY_HOOK", "BotService", "Ledger"]
