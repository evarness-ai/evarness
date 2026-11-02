"""BotIdentityStore -- the kernel's one row per bot (ADR-0138, spec section 4.6).

A bot is defined by the platform (a YAML file the kernel never reads). What the kernel
owns is this row: the bot's id, its unique name, its status, its workspace, the vault
namespace and memory scope that keep it apart from every other bot (both ``bot:<name>``,
both unique), the hash of the definition in force, and the timestamps. ``active`` and
``disabled`` are the owner's switch in both directions; ``revoked`` is terminal and stamps
``revoked_at``. The status hook (``plugins/bot_status.py``) reads this table on every
governed call and denies a bot that is not ``active`` (invariant B1).

One file, ``governance_data_dir()/bots.db``, mode 0600, WAL, like the devices store.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

from evarness.foundation.ids import new_ulid
from evarness.foundation.paths import governance_data_dir

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

BotStatus = Literal["active", "disabled", "revoked"]
#: Every status a row may hold, in the order the schema's CHECK lists them.
BOT_STATUSES: tuple[BotStatus, ...] = get_args(BotStatus)

#: The prefix of a bot's vault namespace and memory scope: ``bot:<name>`` (spec 4.6).
BOT_SCOPE_PREFIX = "bot:"

#: A bot's name as the definition names it: one to 64 characters, letters, digits, dot,
#: underscore and hyphen, starting with a letter or a digit. It is also the tail of two
#: scope strings and a workspace folder name, so no separator, colon or whitespace.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def default_bots_db_path() -> Path:
    """Beside the devices store. Resolved per call, not at import, so a process that
    sets ``EVARNESS_HOME`` after importing this module still gets its own file."""
    return governance_data_dir() / "bots.db"


class BotNotFoundError(LookupError):
    """No bot row with the given name exists."""


class BotRevokedError(ValueError):
    """The bot is revoked: ``revoked`` is terminal, so nothing changes it again."""


class MultipleBotsError(RuntimeError):
    """More than one identity row in a home that this release allows one in."""


def validate_bot_name(name: str) -> str:
    """The name as the store accepts it, or ``ValueError`` saying why not."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(
            "a bot name is 1 to 64 letters, digits, '.', '_' or '-', starting with a "
            f"letter or a digit; got {name!r}"
        )
    return name


def validate_definition_sha256(digest: str) -> str:
    """A SHA-256 hex digest, lower-cased, or ``ValueError``."""
    value = digest.lower() if isinstance(digest, str) else ""
    if not _SHA256.fullmatch(value):
        raise ValueError("definition_sha256 must be 64 hexadecimal characters")
    return value


def validate_bot_status(status: str) -> BotStatus:
    if status not in BOT_STATUSES:
        raise ValueError(f"status must be one of {', '.join(BOT_STATUSES)}; got {status!r}")
    return status  # type: ignore[return-value]


@dataclass(frozen=True)
class BotIdentity:
    """One ``bot_identity`` row."""

    bot_id: str
    name: str
    status: BotStatus
    workspace_path: str
    vault_namespace: str
    memory_scope: str
    definition_sha256: str
    created_at: str
    updated_at: str
    revoked_at: str | None

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def revoked(self) -> bool:
        return self.status == "revoked"


class BotIdentityStore:
    """Open-or-create the bots DB (``bot_identity``)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_bots_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.exists():
            fd = os.open(str(self.db_path), os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        else:
            try:
                os.chmod(str(self.db_path), 0o600)
            except OSError as exc:  # pragma: no cover - non-POSIX or perms issue
                logger.warning("could not chmod %s to 0o600: %s", self.db_path, exc)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema)

    @staticmethod
    def _row(r: sqlite3.Row) -> BotIdentity:
        return BotIdentity(
            bot_id=r["bot_id"],
            name=r["name"],
            status=r["status"],
            workspace_path=r["workspace_path"],
            vault_namespace=r["vault_namespace"],
            memory_scope=r["memory_scope"],
            definition_sha256=r["definition_sha256"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            revoked_at=r["revoked_at"],
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def register(self, name: str, workspace_path: str, definition_sha256: str) -> BotIdentity:
        """The row for ``name``: created on the first call, kept on the next.

        A second register of the same name is the platform re-validating its definition:
        it updates ``definition_sha256`` and ``updated_at`` and returns the one row, it
        never creates a second. A different ``workspace_path`` on that second call is
        refused: the workspace is part of what keeps two bots apart (B3) and never moves
        under a running bot. A revoked name is refused too: ``revoked`` is terminal, and
        whether the name may be reused is an open point of the specification.
        """
        name = validate_bot_name(name)
        digest = validate_definition_sha256(definition_sha256)
        workspace = str(workspace_path)
        if not workspace:
            raise ValueError("workspace_path must not be empty")
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM bot_identity WHERE name = ?", (name,)).fetchone()
            if existing is not None:
                row = self._row(existing)
                if row.revoked:
                    conn.rollback()
                    raise BotRevokedError(f"bot {name!r} is revoked and cannot be registered")
                if row.workspace_path != workspace:
                    conn.rollback()
                    raise ValueError(
                        f"bot {name!r} is registered with workspace {row.workspace_path!r}; "
                        f"a register with {workspace!r} is refused (the workspace never moves)"
                    )
                conn.execute(
                    "UPDATE bot_identity SET definition_sha256 = ?, updated_at = ? "
                    "WHERE bot_id = ?",
                    (digest, now, row.bot_id),
                )
                conn.commit()
                fresh = conn.execute(
                    "SELECT * FROM bot_identity WHERE bot_id = ?", (row.bot_id,)
                ).fetchone()
                return self._row(fresh)
            scope = f"{BOT_SCOPE_PREFIX}{name}"
            bot_id = new_ulid()
            conn.execute(
                "INSERT INTO bot_identity(bot_id, name, status, workspace_path, "
                "vault_namespace, memory_scope, definition_sha256, created_at, updated_at) "
                "VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                (bot_id, name, workspace, scope, scope, digest, now, now),
            )
            conn.commit()
            created = conn.execute(
                "SELECT * FROM bot_identity WHERE bot_id = ?", (bot_id,)
            ).fetchone()
        return self._row(created)

    def get(self, name: str) -> BotIdentity | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM bot_identity WHERE name = ?", (name,)).fetchone()
        return self._row(row) if row is not None else None

    def current(self) -> BotIdentity | None:
        """The one bot of this home, or ``None`` on a plain single-assistant install.

        The platform runs one Evarness process per bot, with the bot's workspace as the
        home, so a home holds at most one row in this release. More than one is a
        misconfiguration the caller must not guess its way through: ``MultipleBotsError``
        (the status hook turns it into a deny of every call).
        """
        rows = self.list()
        if not rows:
            return None
        if len(rows) > 1:
            names = ", ".join(sorted(r.name for r in rows))
            raise MultipleBotsError(
                f"{len(rows)} bot identities in one home ({names}); this release allows one"
            )
        return rows[0]

    def list(self) -> list[BotIdentity]:
        """Every row, revoked ones included (the list is also the history)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM bot_identity ORDER BY created_at, bot_id").fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, name: str, status: str) -> BotIdentity:
        """Flip the owner's switch, or revoke for good.

        ``active`` and ``disabled`` go both ways. ``revoked`` sets ``revoked_at`` and is
        terminal: a later call on a revoked row raises ``BotRevokedError`` whatever it
        asks for, a repeat ``revoked`` included (the first revoke is the event).
        """
        target = validate_bot_status(status)
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM bot_identity WHERE name = ?", (name,)).fetchone()
            if existing is None:
                conn.rollback()
                raise BotNotFoundError(name)
            row = self._row(existing)
            if row.revoked:
                conn.rollback()
                raise BotRevokedError(f"bot {name!r} is revoked; revoked is terminal")
            if target == "revoked":
                conn.execute(
                    "UPDATE bot_identity SET status = 'revoked', revoked_at = ?, updated_at = ? "
                    "WHERE bot_id = ?",
                    (now, now, row.bot_id),
                )
            else:
                conn.execute(
                    "UPDATE bot_identity SET status = ?, updated_at = ? WHERE bot_id = ?",
                    (target, now, row.bot_id),
                )
            conn.commit()
            fresh = conn.execute(
                "SELECT * FROM bot_identity WHERE bot_id = ?", (row.bot_id,)
            ).fetchone()
        return self._row(fresh)


__all__ = [
    "BOT_SCOPE_PREFIX",
    "BOT_STATUSES",
    "BotIdentity",
    "BotIdentityStore",
    "BotNotFoundError",
    "BotRevokedError",
    "BotStatus",
    "MultipleBotsError",
    "default_bots_db_path",
    "validate_bot_name",
    "validate_bot_status",
    "validate_definition_sha256",
]
