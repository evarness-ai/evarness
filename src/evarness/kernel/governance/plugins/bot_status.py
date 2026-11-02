"""BotStatusHook -- ``PreToolUse`` and ``PreLLMCall``: a bot that is not ``active`` runs nothing.

Invariant B1 (ADR-0138, spec section 4.6): a call whose bot identity is not ``active`` is
denied at both points, mid-run included. Which bot a call belongs to is the bot this
process runs, named by ``EVARNESS_BOT`` when the platform starts the bot's runtime and
read once, when the hook is built. Never from a call's payload, its arguments or its
caller string:

- no bot (``EVARNESS_BOT`` unset): the owner's own assistant. ``build_default_kernel``
  does not register the hook at all, so the process behaves exactly as it did before
  bots existed, whatever rows the home holds; called anyway, the hook allows, stamps
  nothing and never opens ``bots.db``;
- the bot's row has status ``active``: allow, and stamp ``bot_id`` / ``bot_name`` into the
  decision's ``audit_metadata``; the kernel copies ``bot_id`` onto the call's context so
  every later hook's row of the same call carries it too;
- any other status (``disabled``, ``revoked``): deny, naming the bot and the status;
- the bot has no row, ``EVARNESS_BOT`` is not a valid name, or the store cannot be read:
  deny every call, with a reason that says which. A process that says it is a bot and
  cannot prove which one runs nothing.

Priority 5: first at ``PreToolUse`` (before the caller policy at 12, so a revoked bot is
refused for being revoked, not for what it asked) and at ``PreLLMCall`` (before redaction
at 20). The row is read through a lazy opener and cached for ``ttl`` seconds (2 s), so a
revoke is seen at the bot's next call without a query per call, and a home with no
``bots.db`` never gets one. Fail closed: nothing stale is served after a failed read.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from evarness.kernel.governance.bots.store import (
    BotIdentityStore,
    open_existing_store,
    selected_bot_name,
)
from evarness.kernel.governance.hooks.tool_payload import BOT_ID, BOT_NAME, BOT_STATUS_HOOK
from evarness.kernel.governance.hooks.types import HookContext, HookDecision, HookPoint

if TYPE_CHECKING:
    from evarness.kernel.governance.bots.store import BotIdentity

logger = logging.getLogger(__name__)

#: How long one read of the identity row answers for (seconds).
BOT_STATUS_CACHE_TTL = 2.0

StoreOpener = Callable[[], BotIdentityStore | None]

_UNSET: object = object()


def open_default_store() -> BotIdentityStore | None:
    """The home's store when its file exists; ``None`` (no file created) otherwise."""
    return open_existing_store()


class BotStatusHook:
    """Deny every call of a bot whose identity is not ``active``."""

    name: str = BOT_STATUS_HOOK
    hook_point: HookPoint = HookPoint.PRE_TOOL_USE
    priority: int = 5

    def __init__(
        self,
        store: StoreOpener | None = None,
        *,
        bot_name: str | None | object = _UNSET,
        ttl: float = BOT_STATUS_CACHE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``bot_name`` defaults to ``EVARNESS_BOT``, read here, once: a value set later
        in the process (by a plugin, say) does not change which bot this kernel guards."""
        self._open = store if store is not None else open_default_store
        self._ttl = ttl
        self._clock = clock
        self._name_error: str | None = None
        self._bot_name: str | None = None
        if bot_name is _UNSET:
            try:
                self._bot_name = selected_bot_name()
            except ValueError as exc:
                self._name_error = str(exc)
        else:
            self._bot_name = bot_name if isinstance(bot_name, str) and bot_name else None
        self._store: BotIdentityStore | None = None
        self._cached: BotIdentity | None = None
        self._cached_at: float | None = None
        self._lock = threading.Lock()

    @property
    def bot_name(self) -> str | None:
        """The bot this hook guards, ``None`` on the owner's own assistant."""
        return self._bot_name

    @property
    def guards_a_bot(self) -> bool:
        """Whether this process said it is a bot: a valid ``EVARNESS_BOT``, or one that is
        set but not a valid name (which denies every call)."""
        return self._bot_name is not None or self._name_error is not None

    def _read(self, name: str) -> BotIdentity | None:
        """The bot's row, from the cache while it is fresh, else from the store."""
        with self._lock:
            now = self._clock()
            if self._cached_at is not None and now - self._cached_at < self._ttl:
                return self._cached
            if self._store is None:
                self._store = self._open()
            row = self._store.get(name) if self._store is not None else None
            self._cached, self._cached_at = row, now
            return row

    def invalidate(self) -> None:
        """Drop the cached row, so the next call reads the store (for a caller that just
        changed the row in this process and does not want to wait out the TTL)."""
        with self._lock:
            self._cached, self._cached_at = None, None

    def _deny(self, ctx: HookContext, reason: str, **metadata: str) -> HookDecision:
        logger.warning(
            "bot.deny hook_point=%s run=%s bot=%s %s",
            ctx.hook_point.value,
            ctx.run_id,
            self._bot_name,
            " ".join(f"{k}={v}" for k, v in sorted(metadata.items())),
        )
        return HookDecision(
            outcome="deny",
            reason=f"bot_status: {reason}",
            severity="error" if "error" in metadata else "warn",
            audit_metadata=dict(metadata),
        )

    async def __call__(self, ctx: HookContext) -> HookDecision:
        if self._name_error is not None:
            return self._deny(
                ctx,
                f"EVARNESS_BOT is not a valid bot name ({self._name_error}); "
                "every call is denied",
                error="InvalidBotName",
            )
        name = self._bot_name
        if name is None:
            return HookDecision(outcome="allow", reason="bot_status: not a bot process")
        try:
            row = self._read(name)
        except Exception as exc:  # noqa: BLE001 - fail closed, whatever the store raised
            return self._deny(
                ctx,
                "the bot identity store could not be read "
                f"({exc.__class__.__name__}); every call is denied until it can",
                bot_name=name,
                error=exc.__class__.__name__,
            )
        if row is None:
            return self._deny(
                ctx,
                f"bot {name!r} has no identity row; register it before it runs",
                bot_name=name,
                status="unregistered",
            )
        if row.status == "active":
            return HookDecision(
                outcome="allow",
                reason=f"bot_status: bot {row.name!r} is active",
                audit_metadata={BOT_ID: row.bot_id, BOT_NAME: row.name},
            )
        return self._deny(
            ctx,
            f"bot {row.name!r} is {row.status}; its calls are denied",
            **{BOT_ID: row.bot_id, BOT_NAME: row.name, "status": row.status},
        )


__all__ = ["BOT_STATUS_CACHE_TTL", "BotStatusHook", "open_default_store"]
