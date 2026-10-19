"""Vault handle resolution helper.

Callers store credentials in their existing env vars (``GITHUB_TOKEN``,
``OPENROUTER_API_KEY``, ...). Phase 2 of the unified governance layer
lets operators replace those raw values with ``vault://<handle>``
references, which are resolved to real secrets at the last moment
before the value is consumed (provider construction, tool dispatch).

This module owns the small, side-effect-free resolution path used by
both the coding-agent LLM client and the core tier router. Keeping the
helper here avoids a circular dependency between ``evarness.kernel.governance.vault.store``
and ``evarness.llm``.
"""

from __future__ import annotations

import logging
import threading
from typing import Final

from evarness.foundation.process_state import track_globals
from evarness.kernel.governance.vault.store import VaultStore

logger = logging.getLogger(__name__)

VAULT_PREFIX: Final[str] = "vault://"


class VaultHandleNotFoundError(LookupError):
    """Raised when a ``vault://`` handle does not resolve to a stored secret."""

    def __init__(self, handle: str) -> None:
        super().__init__(
            f"vault handle {handle!r} is not present; "
            f"add it with `evarness vault add` or `evarness vault import-env`."
        )
        self.handle = handle


_singleton_lock = threading.Lock()
_singleton: VaultStore | None = None


def _vault_singleton() -> VaultStore | None:
    """Return the process-wide vault store, or ``None`` if construction fails.

    A missing master key (no ``EVARNESS_VAULT_MASTER_KEY`` and no keyring)
    is a soft failure: callers fall back to the raw env value. A vault
    handle present in the env *will* still raise via
    :func:`resolve_secret_value`.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        try:
            _singleton = VaultStore()
        except Exception as exc:  # noqa: BLE001 - vault is optional in dev envs
            logger.debug("vault singleton unavailable: %s", exc)
            return None
        return _singleton


def reset_vault_singleton_for_tests() -> None:
    """Drop the cached singleton (test fixtures only)."""
    global _singleton
    with _singleton_lock:
        _singleton = None


def is_vault_handle(value: str | None) -> bool:
    """Return ``True`` when ``value`` is a ``vault://...`` reference."""
    return isinstance(value, str) and value.startswith(VAULT_PREFIX)


def resolve_secret_value(
    value: str | None,
    *,
    vault: VaultStore | None = None,
) -> str | None:
    """Resolve a possibly-vault-handle value to its concrete secret.

    Behavior:

    - ``None`` or empty input returns unchanged (env-var was unset).
    - A plain string (no ``vault://`` prefix) returns unchanged — backwards
      compatible with the ``.env``-direct-value path.
    - A ``vault://handle`` string is resolved against ``vault`` (or the
      process singleton). On miss, ``VaultHandleNotFoundError`` is raised
      — early, with a clear actionable message.
    - On a vault construction failure (no master key, no DB), a
      ``vault://`` handle raises ``RuntimeError`` rather than silently
      returning the literal handle string (which would 401 upstream and
      confuse the operator).
    """
    if value is None or not value:
        return value
    if not is_vault_handle(value):
        return value

    store = vault or _vault_singleton()
    if store is None:
        raise RuntimeError(
            f"cannot resolve {value!r}: vault is not available. "
            "Set EVARNESS_VAULT_MASTER_KEY or configure the OS keyring, "
            "then run `evarness vault add` / `evarness vault import-env`."
        )

    resolved = store.get(value)
    if resolved is None:
        raise VaultHandleNotFoundError(value)
    return resolved


# Process-wide state: put back when a harness run ends (foundation/process_state.py).
track_globals(__name__, "_singleton")
