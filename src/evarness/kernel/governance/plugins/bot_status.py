"""BotStatusHook -- ``PreToolUse`` and ``PreLLMCall``: a bot that is not ``active`` runs nothing.

Invariant B1 (ADR-0138, spec section 4.6): a call whose bot identity is not ``active`` is
denied at both points, mid-run included. The platform runs one Evarness process per bot
with the bot's workspace as the home, so the home's one identity row (``BotIdentityStore``,
``bots.db``) says which bot every call of this process belongs to:

- no row: a plain single-assistant install; the hook allows and stamps nothing, so the
  process behaves exactly as it did before the row existed;
- a row with status ``active``: allow, and stamp ``bot_id`` / ``bot_name`` into the
  decision's ``audit_metadata``; the kernel copies ``bot_id`` onto the call's context so
  every later hook's row of the same call carries it too;
- any other status (``disabled``, ``revoked``): deny, naming the bot and the status.

Priority 5: first at ``PreToolUse`` (before the caller policy at 12, so a revoked bot is
refused for being revoked, not for what it asked) and at ``PreLLMCall`` (before redaction
at 20). The row is read through a lazy opener and cached for ``ttl`` seconds (2 s), so a
revoke is seen at the bot's next call without a query per call, and a home with no
``bots.db`` never creates one. Fail closed: a store that cannot be read is a deny with a
reason that says so; nothing stale is served in its place.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from evarness.kernel.governance.bots.store import BotIdentityStore, default_bots_db_path
from evarness.kernel.governance.hooks.tool_payload import BOT_ID, BOT_NAME
from evarness.kernel.governance.hooks.types import HookContext, HookDecision, HookPoint

if TYPE_CHECKING:
    from evarness.kernel.governance.bots.store import BotIdentity

#: How long one read of the identity row answers for (seconds).
BOT_STATUS_CACHE_TTL = 2.0

StoreOpener = Callable[[], BotIdentityStore | None]


def open_default_store() -> BotIdentityStore | None:
    """The home's store when its file exists; ``None`` (no row, no file created) otherwise."""
    path = default_bots_db_path()
    if not path.exists():
        return None
    return BotIdentityStore(db_path=path)


class BotStatusHook:
    """Deny every call of a bot whose identity is not ``active``."""

    name: str = "bot_status"
    hook_point: HookPoint = HookPoint.PRE_TOOL_USE
    priority: int = 5

    def __init__(
        self,
        store: StoreOpener | None = None,
        *,
        ttl: float = BOT_STATUS_CACHE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._open = store if store is not None else open_default_store
        self._ttl = ttl
        self._clock = clock
        self._store: BotIdentityStore | None = None
        self._cached: BotIdentity | None = None
        self._cached_at: float | None = None
        self._lock = threading.Lock()

    def _read(self) -> BotIdentity | None:
        """The home's row, from the cache while it is fresh, else from the store."""
        with self._lock:
            now = self._clock()
            if self._cached_at is not None and now - self._cached_at < self._ttl:
                return self._cached
            if self._store is None:
                self._store = self._open()
            row = self._store.current() if self._store is not None else None
            self._cached, self._cached_at = row, now
            return row

    def invalidate(self) -> None:
        """Drop the cached row, so the next call reads the store (for a caller that just
        changed the row in this process and does not want to wait out the TTL)."""
        with self._lock:
            self._cached, self._cached_at = None, None

    async def __call__(self, ctx: HookContext) -> HookDecision:
        try:
            row = self._read()
        except Exception as exc:  # noqa: BLE001 - fail closed, whatever the store raised
            return HookDecision(
                outcome="deny",
                reason=(
                    "bot_status: the bot identity store could not be read "
                    f"({exc.__class__.__name__}: {exc}); every call is denied until it can"
                ),
                severity="error",
                audit_metadata={"error": exc.__class__.__name__},
            )
        if row is None:
            return HookDecision(outcome="allow", reason="bot_status: no bot identity in this home")
        if row.status == "active":
            return HookDecision(
                outcome="allow",
                reason=f"bot_status: bot {row.name!r} is active",
                audit_metadata={BOT_ID: row.bot_id, BOT_NAME: row.name},
            )
        return HookDecision(
            outcome="deny",
            reason=f"bot_status: bot {row.name!r} is {row.status}; its calls are denied",
            severity="warn",
            audit_metadata={BOT_ID: row.bot_id, BOT_NAME: row.name, "status": row.status},
        )


__all__ = ["BOT_STATUS_CACHE_TTL", "BotStatusHook", "open_default_store"]
