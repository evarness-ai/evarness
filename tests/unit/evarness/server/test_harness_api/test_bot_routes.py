"""Bot identity on the real harness_api app: ``/api/v1/bots`` (ADR-0138).

Through ``create_app`` so the middleware is part of what is tested: the bearer check, and
the write guard that keeps register and status changes a console write (refused for a
read-only device, and for the shared secret unless ``EVARNESS_WEBUI_ALLOW_WRITES`` is on).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from evarness.foundation.auth import auth_headers
from evarness.kernel.governance.bots import (
    BOT_ENV,
    BotIdentityStore,
    BotService,
    default_bots_db_path,
)
from evarness.kernel.governance.devices import DeviceService, DeviceStore
from evarness.server.harness_api.main import _is_gated_write, create_app

BASE = "/api/v1/bots"
SHA = "d" * 64
FIELDS = {
    "bot_id",
    "name",
    "status",
    "workspace_path",
    "vault_namespace",
    "memory_scope",
    "definition_sha256",
    "created_at",
    "updated_at",
    "revoked_at",
}


class _Ledger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, **row: Any) -> int:
        self.rows.append(row)
        return len(self.rows)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    monkeypatch.delenv(BOT_ENV, raising=False)
    monkeypatch.delenv("EVARNESS_WEBUI_ALLOW_WRITES", raising=False)
    return tmp_path


@pytest.fixture
def ledger() -> _Ledger:
    return _Ledger()


def _client(home: Path, bot_service: BotService | None) -> Iterator[TestClient]:
    devices = DeviceService(store=DeviceStore(db_path=home / "devices.db"), ledger=_Ledger())
    app = create_app(
        runtime=SimpleNamespace(),
        auto_start_runtime=False,
        device_service=devices,
        bot_service=bot_service,
    )
    with TestClient(app, base_url="http://evarness.test") as c:
        yield c


@pytest.fixture
def client(home: Path, ledger: _Ledger) -> Iterator[TestClient]:
    yield from _client(home, BotService(BotIdentityStore(), ledger=ledger))


@pytest.fixture
def lazy_client(home: Path) -> Iterator[TestClient]:
    yield from _client(home, None)


def _device(client: TestClient, scope: str) -> dict[str, str]:
    code = client.post(
        "/api/v1/devices/pair/start", json={"scope": scope}, headers=auth_headers()
    ).json()["code"]
    body = client.post(
        "/api/v1/devices/pair/claim", json={"code": code, "name": "phone", "kind": "app"}
    ).json()
    return {"Authorization": f"Bearer {body['token']}"}


def _register(client: TestClient, home: Path, name: str = "helper", **kw: Any) -> Any:
    body = {"name": name, "workspace_path": str(home / "bots" / name), "definition_sha256": SHA}
    return client.post(BASE, json=body, headers=kw.pop("headers", auth_headers()), **kw)


def test_the_writes_are_gated_writes() -> None:
    assert _is_gated_write("POST", BASE)
    assert _is_gated_write("POST", f"{BASE}/helper/status")
    assert not _is_gated_write("GET", BASE)


def test_the_routes_need_a_credential(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/helper").status_code == 401


def test_the_shared_secret_writes_only_with_the_switch(
    client: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _register(client, home).status_code == 403
    monkeypatch.setenv("EVARNESS_WEBUI_ALLOW_WRITES", "1")
    resp = _register(client, home)
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["bot"]) == FIELDS


def test_a_control_device_registers_and_switches(
    client: TestClient, home: Path, ledger: _Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="evarness.server.harness_api.bot_routes")
    control = _device(client, "control")
    bot = _register(client, home, headers=control).json()["bot"]
    assert bot["status"] == "active" and bot["vault_namespace"] == "bot:helper"
    resp = client.post(f"{BASE}/helper/status", json={"status": "disabled"}, headers=control)
    assert resp.status_code == 200 and resp.json()["bot"]["status"] == "disabled"
    actors = {r["payload"]["actor"] for r in ledger.rows}
    assert len(actors) == 1 and next(iter(actors)).startswith("device:")
    assert [r["payload"]["event"] for r in ledger.rows] == ["bot.register", "bot.status"]
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("bot.api.register") for m in messages)
    assert any(m.startswith("bot.api.status") and "status=disabled" in m for m in messages)


def test_a_read_device_reads_and_never_writes(
    client: TestClient, home: Path, ledger: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVARNESS_WEBUI_ALLOW_WRITES", "1")  # does not open writes to it
    assert _register(client, home).status_code == 200
    reader = _device(client, "read")
    assert _register(client, home, "scout", headers=reader).status_code == 403
    resp = client.post(f"{BASE}/helper/status", json={"status": "revoked"}, headers=reader)
    assert resp.status_code == 403
    assert [b["name"] for b in client.get(BASE, headers=reader).json()["bots"]] == ["helper"]
    assert client.get(f"{BASE}/helper", headers=reader).json()["bot"]["status"] == "active"
    assert len(ledger.rows) == 1


def test_show_list_and_the_errors(
    client: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVARNESS_WEBUI_ALLOW_WRITES", "1")
    headers = auth_headers()
    assert client.get(BASE, headers=headers).json() == {"bots": []}
    assert client.get(f"{BASE}/helper", headers=headers).status_code == 404
    assert client.get(f"{BASE}/Not_Valid", headers=headers).status_code == 422
    assert _register(client, home).status_code == 200
    assert _register(client, home, "scout").status_code == 200
    assert [b["name"] for b in client.get(BASE, headers=headers).json()["bots"]] == [
        "helper",
        "scout",
    ]
    # The rules live in the service; HTTP maps them.
    outside = {"name": "rogue", "workspace_path": "/elsewhere", "definition_sha256": SHA}
    assert client.post(BASE, json=outside, headers=headers).status_code == 422
    bad_hash = {**outside, "workspace_path": str(home / "bots" / "x"), "definition_sha256": "1"}
    assert client.post(BASE, json=bad_hash, headers=headers).status_code == 422
    path = f"{BASE}/ghost/status"
    assert client.post(path, json={"status": "disabled"}, headers=headers).status_code == 404
    assert client.post(path, json={"status": "paused"}, headers=headers).status_code == 422
    revoke = client.post(f"{BASE}/helper/status", json={"status": "revoked"}, headers=headers)
    assert revoke.status_code == 200 and revoke.json()["bot"]["revoked_at"]
    again = client.post(f"{BASE}/helper/status", json={"status": "active"}, headers=headers)
    assert again.status_code == 409
    assert _register(client, home).status_code == 409


def test_reads_on_a_home_without_bots_create_no_file(lazy_client: TestClient) -> None:
    headers = auth_headers()
    assert lazy_client.get(BASE, headers=headers).json() == {"bots": []}
    assert lazy_client.get(f"{BASE}/helper", headers=headers).status_code == 404
    assert not default_bots_db_path().exists()


def test_the_lazy_service_writes_to_the_home(
    lazy_client: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVARNESS_WEBUI_ALLOW_WRITES", "1")
    monkeypatch.setenv("EVARNESS_GOVERNANCE_AUDIT_DB_PATH", str(home / "audit.db"))
    assert _register(lazy_client, home).status_code == 200
    assert default_bots_db_path().exists()
    assert [b["name"] for b in lazy_client.get(BASE, headers=auth_headers()).json()["bots"]] == [
        "helper"
    ]
