"""Bot identity over HTTP: ``/api/v1/bots`` (ADR-0138, spec section 4.6).

Thin callers of ``kernel.governance.bots.BotService``: the row, its rules (revoked is
terminal, the workspace never moves, two bots never share a folder) and the audit row of
each change all live there. What is decided *here* is only what HTTP adds: which
principal may call which route.

- Reads (``GET``) are open to any authenticated principal, like the device list. They
  never create ``bots.db``: a home that never registered a bot answers with no bots.
- Writes (register, status change) are console writes. They are NOT exempt from the write
  guard the way device administration is: a ``read`` device is refused there, and the
  shared secret writes only with ``EVARNESS_WEBUI_ALLOW_WRITES``. Each route checks
  ``can_control`` again, so the rule holds even if the guard's wiring ever changes.

The handlers are plain ``def``: FastAPI runs them in its threadpool, so the SQLite calls
underneath never block the event loop.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from evarness.foundation.auth import Principal

if TYPE_CHECKING:
    from fastapi import FastAPI

    from evarness.kernel.governance.bots import BotIdentity, BotService

__all__ = ["BOTS_PATH", "LazyBotService", "install_bot_routes"]

logger = logging.getLogger(__name__)

BOTS_PATH = "/api/v1/bots"

_READ_ONLY_DETAIL = "this device is paired read-only"
_WRITES_OFF_DETAIL = (
    "control writes are disabled: pair a control device, or set EVARNESS_WEBUI_ALLOW_WRITES=1"
)


class LazyBotService:
    """The app's one ``BotService``, built on first use.

    ``BotService`` opens its store and its audit log only when it needs them: a read on a
    home that never registered a bot, or a refused change, creates no ``bots.db`` and
    opens no audit log. Only a register creates the file.
    """

    def __init__(self, service: BotService | None = None) -> None:
        self._service = service
        self._lock = threading.Lock()

    def get(self) -> BotService:
        if self._service is None:
            with self._lock:
                if self._service is None:
                    from evarness.kernel.governance.bots import BotService

                    self._service = BotService()
        return self._service


class BotRegisterRequest(BaseModel):
    """Body for ``POST /api/v1/bots``."""

    name: str
    workspace_path: str
    definition_sha256: str


class BotStatusRequest(BaseModel):
    """Body for ``POST /api/v1/bots/{name}/status``."""

    status: Literal["active", "disabled", "revoked"]


def bot_payload(row: BotIdentity) -> dict[str, Any]:
    """The row as a response carries it (``BotIdentity.to_dict``, field by field)."""
    return dict(row.to_dict())


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        # Unreachable while the bearer middleware is installed; if it ever is not, these
        # routes fail closed instead of treating "nobody" as the service.
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    return principal


def _actor(principal: Principal) -> str:
    return "service" if principal.kind == "service" else f"device:{principal.device_id}"


def _writer(request: Request) -> str:
    """The actor of a write, after the same rule the write guard applies (``_may_write``):
    a paired device by its own scope; the shared secret only with
    ``EVARNESS_WEBUI_ALLOW_WRITES``. Checked here again so the rule holds even if the
    guard's wiring ever changes."""
    from evarness.server.harness_api.write_guard import _may_write

    principal = _principal(request)
    if principal.kind == "device" and not principal.can_control:
        raise HTTPException(status_code=403, detail=_READ_ONLY_DETAIL)
    if not _may_write(principal):
        raise HTTPException(status_code=403, detail=_WRITES_OFF_DETAIL)
    return _actor(principal)


def _valid_name(name: str) -> str:
    from evarness.kernel.governance.bots import validate_bot_name

    try:
        return validate_bot_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def install_bot_routes(app: FastAPI, bots: LazyBotService) -> None:
    """Register the four bot routes on ``app``."""
    from evarness.kernel.governance.bots import (
        BotNotFoundError,
        BotRevokedError,
        BotWriteRefusedError,
    )

    @app.get(BOTS_PATH)
    def bots_list(http_request: Request) -> dict[str, Any]:
        """Every bot, oldest first, revoked ones included (the list is the history)."""
        _principal(http_request)
        rows = bots.get().list()
        return {"bots": [bot_payload(r) for r in rows]}

    @app.get(f"{BOTS_PATH}/{{name}}")
    def bots_show(name: str, http_request: Request) -> dict[str, Any]:
        """One bot by name."""
        _principal(http_request)
        name = _valid_name(name)
        row = bots.get().get(name)
        if row is None:
            raise HTTPException(status_code=404, detail="no such bot")
        return {"bot": bot_payload(row)}

    @app.post(BOTS_PATH)
    def bots_register(body: BotRegisterRequest, http_request: Request) -> dict[str, Any]:
        """Register a bot, or re-register it with a new definition hash."""
        actor = _writer(http_request)
        name = _valid_name(body.name)
        try:
            row = bots.get().register(
                name, body.workspace_path, body.definition_sha256, actor=actor
            )
        except BotWriteRefusedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except BotRevokedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        logger.info("bot.api.register name=%s bot_id=%s actor=%s", row.name, row.bot_id, actor)
        return {"bot": bot_payload(row)}

    @app.post(f"{BOTS_PATH}/{{name}}/status")
    def bots_set_status(name: str, body: BotStatusRequest, http_request: Request) -> dict[str, Any]:
        """``active`` and ``disabled`` both ways; ``revoked`` for good."""
        actor = _writer(http_request)
        name = _valid_name(name)
        try:
            row = bots.get().set_status(name, body.status, actor=actor)
        except BotWriteRefusedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except BotNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such bot") from exc
        except BotRevokedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info(
            "bot.api.status name=%s bot_id=%s status=%s actor=%s",
            row.name,
            row.bot_id,
            row.status,
            actor,
        )
        return {"bot": bot_payload(row)}
