"""Changing EVARNESS_* settings over the API: catalog rules, the guard, locks, restart."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from evarness.foundation.settings import SETTINGS_DB_NAME, SettingsStore
from evarness.foundation.settings.env_overrides import ENV_SECTION, _reset_for_tests
from evarness.foundation.settings.restart import RESTART_KEY, SYSTEM_SECTION
from evarness.kernel.governance.devices import DeviceService
from evarness.runtime.plugin_host.manifest import PluginManifest
from evarness.server.harness_api.main import create_app

_TOUCHED = (
    "EVARNESS_LOG_LEVEL",
    "EVARNESS_WEBUI_ALLOW_WRITES",
    "EVARNESS_PLUGINS_DISABLE",
    "EVARNESS_REACT_TOOL_CAP",
    "EVARNESS_SUPERVISED",
)

# A process beside the harness: the app lists its settings and never changes them.
SIDECAR_CATALOG = """
owner: demo_sidecar
settings:
  EVARNESS_DEMO_SIDECAR_BUDGET:
    kind: float
    default: 5
    applies: restart
    label: Demo sidecar budget
    description: What the demo sidecar may spend.
    tab: guards
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    _reset_for_tests()
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path / "home"))
    sidecar = tmp_path / "sidecar.yaml"
    sidecar.write_text(SIDECAR_CATALOG, encoding="utf-8")
    monkeypatch.setenv("EVARNESS_SETTINGS_SIDECAR_CATALOGS", str(sidecar))
    for name in _TOUCHED:
        monkeypatch.delenv(name, raising=False)
    manifests = [
        PluginManifest.model_validate({"name": "web_channel", "locked": "the app runs on it"}),
        PluginManifest.model_validate({"name": "research"}),
    ]
    registry = SimpleNamespace(plugins=lambda: [SimpleNamespace(manifest=m) for m in manifests])
    runtime = SimpleNamespace(plugin_registry=registry, data_dir=tmp_path)
    devices = DeviceService()
    app = create_app(runtime=runtime, auto_start_runtime=False)  # type: ignore[arg-type]
    with TestClient(app, base_url="http://evarness.test") as client:
        yield SimpleNamespace(
            client=client,
            owner=_pair(devices, "control"),
            reader=_pair(devices, "read"),
            store=SettingsStore(db_path=tmp_path / SETTINGS_DB_NAME),
        )
    for name in _TOUCHED:
        os.environ.pop(name, None)
    _reset_for_tests()


def _pair(devices: DeviceService, scope: str) -> dict[str, str]:
    code = devices.start_pairing(scope=scope, actor="service")
    paired = devices.claim(code=code.code, name=f"{scope} phone", kind="browser")
    return {"Authorization": f"Bearer {paired.token}"}


def test_a_control_device_changes_a_setting_and_it_applies_now(world: SimpleNamespace) -> None:
    r = world.client.patch(
        "/settings/EVARNESS_REACT_TOOL_CAP", json={"value": 9}, headers=world.owner
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "9"
    assert r.json()["overridden"] is True
    assert r.json()["restart_required"] is False
    assert os.environ["EVARNESS_REACT_TOOL_CAP"] == "9"
    assert world.store.get(ENV_SECTION, "EVARNESS_REACT_TOOL_CAP") == "9"
    catalog = world.client.get("/settings/catalog", headers=world.owner).json()["settings"]
    row = next(s for s in catalog if s["name"] == "EVARNESS_REACT_TOOL_CAP")
    assert row["overridden"] is True


def test_reset_returns_to_the_deploy_value(world: SimpleNamespace) -> None:
    world.client.patch("/settings/EVARNESS_LOG_LEVEL", json={"value": "DEBUG"}, headers=world.owner)
    r = world.client.delete("/settings/EVARNESS_LOG_LEVEL", headers=world.owner)
    assert r.status_code == 200
    assert r.json()["overridden"] is False
    assert "EVARNESS_LOG_LEVEL" not in os.environ


def test_a_guarded_setting_needs_the_confirm_the_api_checks(world: SimpleNamespace) -> None:
    refused = world.client.patch(
        "/settings/EVARNESS_WEBUI_ALLOW_WRITES", json={"value": True}, headers=world.owner
    )
    assert refused.status_code == 409
    assert "confirm" in refused.json()["detail"]
    assert "EVARNESS_WEBUI_ALLOW_WRITES" not in os.environ

    ok = world.client.patch(
        "/settings/EVARNESS_WEBUI_ALLOW_WRITES",
        json={"value": True, "confirm": True},
        headers=world.owner,
    )
    assert ok.status_code == 200
    assert os.environ["EVARNESS_WEBUI_ALLOW_WRITES"] == "1"


@pytest.mark.parametrize(
    ("name", "body", "status"),
    [
        ("EVARNESS_NOT_A_SETTING", {"value": 1}, 404),
        ("EVARNESS_VAULT_MASTER_KEY", {"value": "x"}, 403),  # a secret: never from the app
        ("EVARNESS_EMBED_BACKEND", {"value": "x"}, 403),  # would strand the stored vectors
        ("EVARNESS_DEMO_SIDECAR_BUDGET", {"value": 9}, 409),  # a sidecar's: its own env
        ("EVARNESS_REACT_TOOL_CAP", {"value": "many"}, 422),
    ],
)
def test_what_the_app_may_not_do(
    world: SimpleNamespace, name: str, body: dict[str, object], status: int
) -> None:
    r = world.client.patch(f"/settings/{name}", json=body, headers=world.owner)
    assert r.status_code == status, r.text


def test_a_read_device_changes_nothing(world: SimpleNamespace) -> None:
    r = world.client.patch(
        "/settings/EVARNESS_REACT_TOOL_CAP", json={"value": 3}, headers=world.reader
    )
    assert r.status_code == 403
    assert "EVARNESS_REACT_TOOL_CAP" not in os.environ


def test_a_locked_plugin_cannot_be_turned_off_from_the_app(world: SimpleNamespace) -> None:
    r = world.client.patch(
        "/settings/EVARNESS_PLUGINS_DISABLE",
        json={"value": "research, web_channel", "confirm": True},
        headers=world.owner,
    )
    assert r.status_code == 409
    assert "web_channel cannot be turned off here" in r.json()["detail"]

    ok = world.client.patch(
        "/settings/EVARNESS_PLUGINS_DISABLE",
        json={"value": "research", "confirm": True},
        headers=world.owner,
    )
    assert ok.status_code == 200
    assert ok.json()["restart_required"] is True


def test_restart_status_lists_what_waits_for_a_restart(world: SimpleNamespace) -> None:
    world.client.patch(
        "/settings/EVARNESS_PLUGINS_DISABLE",
        json={"value": "research", "confirm": True},
        headers=world.owner,
    )
    world.client.patch("/settings/EVARNESS_REACT_TOOL_CAP", json={"value": 5}, headers=world.owner)

    status = world.client.get("/system/restart", headers=world.owner).json()

    assert status["waiting_for_restart"] == ["EVARNESS_PLUGINS_DISABLE"]
    assert status["supervised"] is False


def test_restart_is_refused_without_a_supervisor_or_a_confirm(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsupervised = world.client.post("/system/restart", json={"confirm": True}, headers=world.owner)
    assert unsupervised.status_code == 409
    assert "restart it yourself" in unsupervised.json()["detail"]

    monkeypatch.setenv("EVARNESS_SUPERVISED", "1")
    assert world.client.post("/system/restart", json={}, headers=world.owner).status_code == 409
    assert world.store.get(SYSTEM_SECTION, RESTART_KEY) is None

    ok = world.client.post("/system/restart", json={"confirm": True}, headers=world.owner)
    assert ok.status_code == 200
    assert world.store.get(SYSTEM_SECTION, RESTART_KEY) == ok.json()["requested_at"]
    assert (
        world.client.post(
            "/system/restart", json={"confirm": True}, headers=world.reader
        ).status_code
        == 403
    )


def test_a_live_learning_toggle_is_saved_so_a_restart_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0083 toggles used to revert on restart; the route now saves the setting."""
    _reset_for_tests()
    monkeypatch.setenv("EVARNESS_WEBUI_ALLOW_WRITES", "1")
    monkeypatch.delenv("EVARNESS_BEHAVIOR_MINER", raising=False)
    flipped: list[tuple[str, bool]] = []

    def set_learning_flag(name: str, enabled: bool) -> bool:
        flipped.append((name, enabled))
        return enabled

    runtime = SimpleNamespace(
        learning=SimpleNamespace(set_learning_flag=set_learning_flag), data_dir=tmp_path
    )
    app = create_app(runtime=runtime, auto_start_runtime=False)  # type: ignore[arg-type]
    from evarness.foundation.auth import auth_headers

    with TestClient(app, headers=auth_headers()) as client:
        r = client.post("/learning/flags/behavior_miner", json={"enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["saved_as"] == "EVARNESS_BEHAVIOR_MINER"
        assert client.post("/learning/flags/nope", json={"enabled": True}).status_code == 404

    assert flipped == [("behavior_miner", True)]
    store = SettingsStore(db_path=tmp_path / SETTINGS_DB_NAME)
    assert store.get(ENV_SECTION, "EVARNESS_BEHAVIOR_MINER") == "1"
    os.environ.pop("EVARNESS_BEHAVIOR_MINER", None)
