"""Bot identity package (ADR-0138): the kernel's one row per bot."""

from evarness.kernel.governance.bots.service import BOT_IDENTITY_HOOK, BotService
from evarness.kernel.governance.bots.store import (
    BOT_SCOPE_PREFIX,
    BOT_STATUSES,
    BotIdentity,
    BotIdentityStore,
    BotNotFoundError,
    BotRevokedError,
    BotStatus,
    MultipleBotsError,
    default_bots_db_path,
    validate_bot_name,
    validate_bot_status,
    validate_definition_sha256,
)

__all__ = [
    "BOT_IDENTITY_HOOK",
    "BOT_SCOPE_PREFIX",
    "BOT_STATUSES",
    "BotIdentity",
    "BotIdentityStore",
    "BotNotFoundError",
    "BotRevokedError",
    "BotService",
    "BotStatus",
    "MultipleBotsError",
    "default_bots_db_path",
    "validate_bot_name",
    "validate_bot_status",
    "validate_definition_sha256",
]
