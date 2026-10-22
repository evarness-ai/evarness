"""API tests for the /playground endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from evarness.foundation.auth import auth_headers
from evarness.server.harness_api.main import create_app


def _client(runtime: object | None = None) -> Iterator[TestClient]:
    with TestClient(
        create_app(runtime=runtime, auto_start_runtime=False), headers=auth_headers()
    ) as c:
        yield c


@pytest.fixture
def writes_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVARNESS_WEBUI_ALLOW_WRITES", raising=False)


@pytest.fixture
def writes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVARNESS_WEBUI_ALLOW_WRITES", "1")


def _fake_runtime() -> SimpleNamespace:
    registry = SimpleNamespace(list_packages=lambda: ())
    router = SimpleNamespace(_tiers={"tier1": object()})
    gateway = SimpleNamespace(channels=lambda: ["console"])
    return SimpleNamespace(skill_registry=registry, tier_router=router, channels=gateway)


def test_suites_lists_committed_suite() -> None:
    for client in _client():
        body = client.get("/playground/suites").json()
    names = {s["name"] for s in body["suites"]}
    assert "core-deterministic" in names


def test_suites_lists_a_broken_suite_with_the_loaders_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suite that does not load is listed with where it is wrong, in the loader's words,
    not with the parser's own text."""
    (tmp_path / "typo.yaml").write_text(
        "scenarios:\n  - name: a\n    message: hi\n    expect:\n      bogus_field: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EVARNESS_PLAYGROUND_DIR", str(tmp_path))
    for client in _client():
        body = client.get("/playground/suites").json()
    [broken] = body["suites"]
    assert broken["name"] == "typo" and broken["path"] == str(tmp_path / "typo.yaml")
    assert broken["error"] == (
        f"{tmp_path / 'typo.yaml'}: scenarios[0].expect.bogus_field: Extra inputs are not permitted"
    )


def test_drift_reports_surfaces() -> None:
    for client in _client(_fake_runtime()):
        body = client.get("/playground/drift").json()
    assert {s["surface"] for s in body["surfaces"]} == {
        "skills",
        "llm_tiers",
        "channels",
        "intercepts",
        "plugin_tools",  # ADR-0110: manifest-declared tools vs registered ones
        "plugin_uses",  # plugin-capabilities §4: a grant naming no registered tool
        "plugin_capabilities",  # §2: a declared capability never provided
        "capability_uses",  # §2: a capability used that nothing provides
    }


def test_run_is_write_gated_by_default(writes_off: None) -> None:
    # POST /playground/run drives chat, so the deny-by-default gate blocks it
    # unless writes are enabled.
    for client in _client(_fake_runtime()):
        resp = client.post("/playground/run", json={"suite": "core-deterministic"})
    assert resp.status_code == 403


def test_run_unknown_suite_404(writes_on: None) -> None:
    for client in _client(_fake_runtime()):
        resp = client.post("/playground/run", json={"suite": "does-not-exist"})
    assert resp.status_code == 404
