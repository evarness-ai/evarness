"""BotIdentityStore -- the kernel's one row per bot (ADR-0138, spec section 4.6).

A bot is defined by the platform (a YAML file the kernel never reads). What the kernel
owns is this row: the bot's id, its unique name, its status, its workspace, the vault
namespace and memory scope that keep it apart from every other bot (both ``bot:<name>``,
both unique), the hash of the definition in force, and the timestamps. ``active`` and
``disabled`` are the owner's switch in both directions; ``revoked`` is terminal and stamps
``revoked_at``. One home holds a row per bot (B4): adding a bot adds a row here.

Which bot a process runs is not a row's business: the platform starts a bot's runtime
with ``EVARNESS_BOT=<name>`` (:func:`process_bot`, read once per process). The status hook
(``plugins/bot_status.py``) reads that bot's row on every governed call and denies it when
it is not ``active`` (invariant B1). A process with no ``EVARNESS_BOT`` is the owner's own
assistant and is not a bot, whatever rows the home holds.

One file, ``governance_data_dir()/bots.db``, mode 0600, WAL, like the devices store.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

from evarness.foundation.ids import new_ulid
from evarness.foundation.paths import evarness_home, governance_data_dir
from evarness.foundation.process_state import track_globals

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

BotStatus = Literal["active", "disabled", "revoked"]
#: Every status a row may hold, in the order the schema's CHECK lists them.
BOT_STATUSES: tuple[BotStatus, ...] = get_args(BotStatus)

#: The prefix of a bot's vault namespace and memory scope: ``bot:<name>`` (spec 4.6).
BOT_SCOPE_PREFIX = "bot:"

#: The environment variable that names the bot a process runs (``EVARNESS_BOT=<name>``).
#: Set by the platform when it starts a bot's runtime; unset on the owner's own assistant.
BOT_ENV = "EVARNESS_BOT"

#: A bot's name exactly as the definition's ``name`` (spec 4.1): a lower-case slug of 2 to
#: 32 characters starting with a letter. It is also the tail of two scope strings and a
#: workspace folder name, so no separator, colon, dot or whitespace.
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def default_bots_db_path() -> Path:
    """Beside the devices store. Resolved per call, not at import, so a process that
    sets ``EVARNESS_HOME`` after importing this module still gets its own file."""
    return governance_data_dir() / "bots.db"


class BotNotFoundError(LookupError):
    """No bot row with the given name exists."""


class BotRevokedError(ValueError):
    """The bot is revoked: ``revoked`` is terminal, so nothing changes it again."""


def validate_bot_name(name: str) -> str:
    """The name as the store accepts it, or ``ValueError`` saying why not."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(
            "a bot name is 2 to 32 lower-case letters, digits, '_' or '-', starting with a "
            f"letter; got {name!r}"
        )
    return name


def _read_bot_env() -> tuple[str | None, str | None]:
    raw = os.environ.get(BOT_ENV, "").strip()
    if not raw:
        return None, None
    try:
        return validate_bot_name(raw), None
    except ValueError as exc:
        return None, str(exc)


#: The bot this process runs, as ``(name, error)``: read from ``EVARNESS_BOT`` once and
#: kept, so a value set later in the process (by a plugin, say) changes nothing. ``None``
#: until the first read. Process state: the testing harness puts it back on exit.
_process_bot: tuple[str | None, str | None] | None = None
_process_bot_lock = threading.Lock()


def process_bot() -> tuple[str | None, str | None]:
    """``(name, error)`` of the bot this process runs, pinned at the first call.

    ``(None, None)``: no ``EVARNESS_BOT``, the owner's own assistant. ``(None, error)``: the
    variable is set but is not a valid bot name, which the status hook turns into a deny
    of every call. The status hook and ``current_bot()`` share this one read.
    """
    global _process_bot  # the one pinned read per process
    with _process_bot_lock:
        if _process_bot is None:
            _process_bot = _read_bot_env()
        return _process_bot


def process_bot_name() -> str | None:
    """The pinned bot name, ``None`` for the owner's assistant; ``ValueError`` when
    ``EVARNESS_BOT`` is set but not a valid name."""
    name, error = process_bot()
    if error is not None:
        raise ValueError(error)
    return name


def is_bot_process() -> bool:
    """Whether this process was started as a bot (a valid ``EVARNESS_BOT`` or not)."""
    name, error = process_bot()
    return name is not None or error is not None


def reset_process_bot() -> None:
    """Forget the pinned read, so the next one reads ``EVARNESS_BOT`` again, and the audit
    log's bot source with it. For a test, or the testing harness building a new runtime
    in the same process; never production."""
    global _process_bot  # the one pinned read per process
    from evarness.kernel.governance.audit.log import set_bot_id_source

    with _process_bot_lock:
        _process_bot = None
    set_bot_id_source(None)  # the kernel built next sets it again if it guards a bot


def open_existing_store(db_path: Path | None = None) -> BotIdentityStore | None:
    """The store when its file exists; ``None`` otherwise, and no file is created.

    A read on a home that never registered a bot (the single-assistant install) must
    leave that home exactly as it was, so every reader goes through here.
    """
    path = db_path or default_bots_db_path()
    if not path.exists():
        return None
    return BotIdentityStore(db_path=path)


#: The folder under the Evarness home that holds every bot's workspace (spec 4.6).
BOTS_DIR_NAME = "bots"


def bot_workspace(name: str) -> Path:
    """The one workspace a bot may have: ``<home>/bots/<name>`` (spec 4.6, B3).

    Derived from the name, so two bots never share a folder (names are unique, lower-case
    slugs) and no bot's workspace is ever the kernel's or the owner's folder.
    """
    return evarness_home().expanduser().resolve() / BOTS_DIR_NAME / validate_bot_name(name)


def resolve_workspace(name: str, workspace_path: str | os.PathLike[str]) -> str:
    """``workspace_path`` checked to be exactly :func:`bot_workspace`, returned in that
    canonical spelling; ``ValueError`` otherwise.

    The comparison follows symlinks and ignores case (a case-insensitive file system
    treats ``Bots/Helper`` as ``bots/helper``), and when both paths exist it also asks the
    file system whether they are the same folder. A ``bots`` folder or a workspace that is
    a symlink to anywhere else does not resolve to the expected path and is refused.
    """
    expected = bot_workspace(name)
    raw = os.fspath(workspace_path)
    if not raw:
        raise ValueError("workspace_path must not be empty")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"workspace_path must be absolute; got {raw!r}")
    resolved = path.resolve()
    same = os.path.normcase(str(resolved)).casefold() == os.path.normcase(str(expected)).casefold()
    if same and resolved.exists() and expected.exists():
        same = os.path.samefile(resolved, expected)
    if not same:
        raise ValueError(f"workspace_path must be {expected} (<home>/bots/<name>); got {raw!r}")
    return str(expected)


def validate_definition_sha256(digest: str) -> str:
    """A SHA-256 hex digest, lower-cased, or ``ValueError``."""
    value = digest.lower() if isinstance(digest, str) else ""
    if not _SHA256.fullmatch(value):
        raise ValueError("definition_sha256 must be 64 hexadecimal characters")
    return value


def validate_bot_status(status: str) -> BotStatus:
    if status not in BOT_STATUSES:
        raise ValueError(f"status must be one of {', '.join(BOT_STATUSES)}; got {status!r}")
    return status


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

    def to_dict(self) -> dict[str, str | None]:
        """The row as the API and the CLI show it. Field by field, not ``asdict``: a
        field added to the row later must not reach a response by default."""
        return {
            "bot_id": self.bot_id,
            "name": self.name,
            "status": self.status,
            "workspace_path": self.workspace_path,
            "vault_namespace": self.vault_namespace,
            "memory_scope": self.memory_scope,
            "definition_sha256": self.definition_sha256,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revoked_at": self.revoked_at,
        }

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

        The workspace is ``<home>/bots/<name>`` and nothing else (spec 4.6, B3; see
        :func:`resolve_workspace`), stored in that canonical spelling.
        """
        name = validate_bot_name(name)
        digest = validate_definition_sha256(definition_sha256)
        workspace = resolve_workspace(name, workspace_path)
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
            for other in conn.execute("SELECT name, workspace_path FROM bot_identity"):
                # Unreachable while workspaces are derived from unique names; kept so a
                # row written any other way can never be shared (B3).
                if os.path.normcase(other["workspace_path"]).casefold() == (
                    os.path.normcase(workspace).casefold()
                ):
                    conn.rollback()
                    raise ValueError(
                        f"workspace {workspace!r} is the workspace of bot "
                        f"{other['name']!r}; two bots never share a folder (B3)"
                    )
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


track_globals(__name__, "_process_bot")

__all__ = [
    "BOTS_DIR_NAME",
    "BOT_ENV",
    "BOT_SCOPE_PREFIX",
    "BOT_STATUSES",
    "BotIdentity",
    "BotIdentityStore",
    "BotNotFoundError",
    "BotRevokedError",
    "BotStatus",
    "bot_workspace",
    "default_bots_db_path",
    "is_bot_process",
    "open_existing_store",
    "process_bot",
    "process_bot_name",
    "reset_process_bot",
    "resolve_workspace",
    "validate_bot_name",
    "validate_bot_status",
    "validate_definition_sha256",
]
