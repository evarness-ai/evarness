"""Bot identity package (ADR-0138): the kernel's one row per bot."""

from evarness.kernel.governance.bots.service import (
    BOT_IDENTITY_HOOK,
    BotService,
    current_bot_identity,
)
from evarness.kernel.governance.bots.store import (
    BOT_ENV,
    BOT_SCOPE_PREFIX,
    BOT_STATUSES,
    BotIdentity,
    BotIdentityStore,
    BotNotFoundError,
    BotRevokedError,
    BotStatus,
    default_bots_db_path,
    open_existing_store,
    resolve_workspace,
    selected_bot_name,
    validate_bot_name,
    validate_bot_status,
    validate_definition_sha256,
)

__all__ = [
    "BOT_ENV",
    "BOT_IDENTITY_HOOK",
    "BOT_SCOPE_PREFIX",
    "BOT_STATUSES",
    "BotIdentity",
    "BotIdentityStore",
    "BotNotFoundError",
    "BotRevokedError",
    "BotService",
    "BotStatus",
    "current_bot_identity",
    "default_bots_db_path",
    "open_existing_store",
    "resolve_workspace",
    "selected_bot_name",
    "validate_bot_name",
    "validate_bot_status",
    "validate_definition_sha256",
]
