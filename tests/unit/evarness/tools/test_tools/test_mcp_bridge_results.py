"""What the MCP bridge makes of a failed result and of a tool the server never declared (#112 M9b).

A ``tools/call`` result with ``isError: true`` is the server saying the call failed: it is
still handed back, and its ``post_tool_use`` rows say the call failed (``tool_error``).
A tool name outside the server's ``tools/list`` and the operator's ``governance.tools`` is
refused before any hook runs, with one ``deny`` row, and the server is never reached. A
server this bridge has not listed yet is listed once, through the governed ``tools/list``
path, before an undeclared name is checked (#296); a listing that fails refuses the call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evarness.kernel.governance.hooks.tool_payload import TOOL_ERROR, TOOL_REPORTED_ERROR
from evarness.kernel.governance.mcp_signing import MCPSigningConfig
from evarness.kernel.governance.plugins.mcp_allowlist import (
    MCPServerGovernance,
    MCPToolGovernance,
)
from evarness.tools.mcp_bridge import (
    MCPApprovalRequired,
    MCPBridge,
    MCPBridgeConfig,
    MCPServerConfig,
    MCPUndeclaredToolError,
    is_error_result,
)

_FAILED = {"content": [{"type": "text", "text": "no forecast for 'atlantis'"}], "isError": True}
_OK = {"content": [{"type": "text", "text": "Sunny, 24 C"}], "isError": False}
_TOOLS = {"tools": [{"name": "forecast", "description": "Today's forecast."}]}


def _server(**governance: Any) -> MCPServerConfig:
    return MCPServerConfig(
        name="weather",
        enabled=True,
        transport="http",
        url="http://127.0.0.1:8787/mcp",
        governance=MCPServerGovernance(undeclared_tools="read", **governance),
    )


class _Server:
    """A fake HTTP MCP server: answers ``tools/list`` and each ``tools/call`` with ``reply``."""

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.calls: list[str] = []
        self.methods: list[str] = []
        self.list_error: Exception | None = None

    def __call__(self, _server: MCPServerConfig, payload: dict[str, Any]) -> dict[str, Any]:
        method = payload["method"]
        self.methods.append(method)
        if method == "tools/list":
            if self.list_error is not None:
                raise self.list_error
            return {"result": _TOOLS}
        self.calls.append(payload["params"]["name"])
        return {"result": self.reply}


def _bridge(
    tmp_path: Path, server: MCPServerConfig, fake: _Server, *, requires_approval: bool = True
) -> tuple[MCPBridge, Any]:
    from evarness.kernel.governance import build_default_kernel
    from evarness.kernel.governance.approvals import ApprovalQueue
    from evarness.kernel.governance.audit import AuditLog

    policy = tmp_path / "config" / "governor"
    policy.mkdir(parents=True, exist_ok=True)
    (policy / "policy.yaml").write_text(
        "version: '1'\n"
        "routes:\n"
        "  - route: coding/mcp\n"
        "    allowed_actions: [session_open, call_tool, invoke_server]\n"
        f"    requires_approval: {'true' if requires_approval else 'false'}\n"
        "    rate_limit: {requests: 100, window_seconds: 3600}\n",
        encoding="utf-8",
    )
    audit = AuditLog(tmp_path / "audit.db")
    kernel = build_default_kernel(
        audit_log=audit,
        approval_queue=ApprovalQueue(db_path=tmp_path / "approvals.db"),
        side_effect_ledger_db_path=tmp_path / "ledger.db",
    )
    bridge = MCPBridge(
        tmp_path,
        config=MCPBridgeConfig(enabled=True, servers=(server,)),
        governance_kernel=kernel,
        signing_config=MCPSigningConfig.disabled(),
        http_requester=fake,
    )
    return bridge, audit


def _payloads(audit: Any, hook_point: str) -> list[dict[str, Any]]:
    return [json.loads(r.payload_json) for r in audit.query() if r.hook_point == hook_point]


# --- isError ---------------------------------------------------------------------------


def test_is_error_result_reads_the_flag_of_a_tools_call_result() -> None:
    assert is_error_result(_FAILED)
    assert not is_error_result(_OK)
    assert not is_error_result({"content": []})
    assert not is_error_result("error: text")


def test_an_is_error_result_is_returned_and_its_post_rows_record_the_failure(
    tmp_path: Path,
) -> None:
    fake = _Server(_FAILED)
    bridge, audit = _bridge(tmp_path, _server(), fake)

    invocation = bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)

    # The caller still gets the server's answer.
    assert is_error_result(invocation.result)
    assert "no forecast for 'atlantis'" in json.dumps(invocation.result)
    post = _payloads(audit, "post_tool_use")
    assert post and all(p[TOOL_ERROR] == TOOL_REPORTED_ERROR for p in post)
    # A failure is only stamped where the call reached its result.
    assert all(TOOL_ERROR not in p for p in _payloads(audit, "pre_tool_use"))


def test_a_successful_result_carries_no_tool_error(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, audit = _bridge(tmp_path, _server(), fake)

    bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)

    post = _payloads(audit, "post_tool_use")
    assert post and all(TOOL_ERROR not in p for p in post)


# --- a tool the server never declared --------------------------------------------------


def test_after_tools_list_an_unlisted_tool_is_refused_with_one_deny_row(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, audit = _bridge(tmp_path, _server(), fake)
    assert [t.name for t in bridge.list_external_tools("weather", approval_granted=True)] == [
        "forecast"
    ]

    with pytest.raises(MCPUndeclaredToolError, match="did not list it"):
        bridge.invoke_external_tool("weather", "delete_everything", {}, approval_granted=True)

    assert fake.calls == []  # the transport was never reached
    rows = audit.query()
    assert [(r.hook_point, r.plugin, r.decision) for r in rows] == [
        ("pre_tool_use", "mcp_undeclared_tool", "deny")
    ]
    payload = json.loads(rows[0].payload_json)
    assert payload["mcp_server"] == "weather" and payload["mcp_tool"] == "delete_everything"


def test_an_undeclared_tool_refusal_is_a_permission_error_the_governor_never_sees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert issubclass(MCPUndeclaredToolError, PermissionError)
    fake = _Server(_OK)
    bridge, _audit = _bridge(tmp_path, _server(), fake)
    bridge.list_external_tools("weather", approval_granted=True)
    guarded: list[str] = []
    monkeypatch.setattr(bridge, "_guard_mcp_action", lambda action, **_kw: guarded.append(action))

    with pytest.raises(PermissionError):
        bridge.invoke_external_tool("weather", "nope", {}, approval_granted=True)

    assert guarded == []


def test_a_tool_the_operator_declared_runs_even_if_the_server_did_not_list_it(
    tmp_path: Path,
) -> None:
    fake = _Server(_OK)
    server = _server(tools={"hidden": MCPToolGovernance(effect="read")})
    bridge, _audit = _bridge(tmp_path, server, fake)
    bridge.list_external_tools("weather", approval_granted=True)

    bridge.invoke_external_tool("weather", "hidden", {}, approval_granted=True)

    assert fake.calls == ["hidden"]


def test_a_listed_tool_still_runs(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, _audit = _bridge(tmp_path, _server(), fake)
    bridge.list_external_tools("weather", approval_granted=True)

    bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)

    assert fake.calls == ["forecast"]


def test_without_a_kernel_an_undeclared_tool_is_still_refused(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, _audit = _bridge(tmp_path, _server(), fake)
    bridge._governance_kernel = None
    bridge.list_external_tools("weather", approval_granted=True)

    with pytest.raises(MCPUndeclaredToolError):
        bridge.invoke_external_tool("weather", "nope", {}, approval_granted=True)
    assert fake.calls == []


def test_the_http_route_answers_403_for_an_undeclared_tool() -> None:
    from evarness.server.harness_api.mcp_server import _as_http_exception

    error = _as_http_exception(MCPUndeclaredToolError("MCP tool 'w/x' refused: ..."))

    assert error.status_code == 403


# --- a server this bridge has not listed yet (#296) ------------------------------------


def _undeclared_rows(audit: Any) -> list[tuple[str, str, str]]:
    return [(r.hook_point, r.plugin, r.decision) for r in audit.query()]


def test_before_any_listing_an_unlisted_tool_is_refused_with_one_deny_row(
    tmp_path: Path,
) -> None:
    fake = _Server(_OK)
    bridge, audit = _bridge(tmp_path, _server(), fake)

    with pytest.raises(MCPUndeclaredToolError, match="did not list it"):
        bridge.invoke_external_tool("weather", "delete_everything", {}, approval_granted=True)

    # The bridge listed the server once, and never sent the call.
    assert fake.methods == ["tools/list"]
    assert fake.calls == []
    assert _undeclared_rows(audit) == [("pre_tool_use", "mcp_undeclared_tool", "deny")]
    payload = json.loads(audit.query()[0].payload_json)
    assert payload["mcp_server"] == "weather" and payload["mcp_tool"] == "delete_everything"


def test_before_any_listing_a_declared_tool_runs_without_a_listing(tmp_path: Path) -> None:
    fake = _Server(_OK)
    server = _server(tools={"hidden": MCPToolGovernance(effect="read")})
    bridge, _audit = _bridge(tmp_path, server, fake)

    bridge.invoke_external_tool("weather", "hidden", {}, approval_granted=True)

    assert fake.methods == ["tools/call"]
    assert fake.calls == ["hidden"]


def test_before_any_listing_a_listed_tool_runs_after_one_listing(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, _audit = _bridge(tmp_path, _server(), fake)

    bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)

    assert fake.methods == ["tools/list", "tools/call"]
    assert fake.calls == ["forecast"]


def test_the_server_is_listed_once_per_bridge_not_per_call(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, _audit = _bridge(tmp_path, _server(), fake)

    bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)
    bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)
    with pytest.raises(MCPUndeclaredToolError):
        bridge.invoke_external_tool("weather", "nope", {}, approval_granted=True)

    assert fake.methods.count("tools/list") == 1
    assert fake.calls == ["forecast", "forecast"]


def test_a_failed_listing_refuses_the_call_with_one_deny_row(tmp_path: Path) -> None:
    fake = _Server(_OK)
    fake.list_error = ValueError("connection refused")
    bridge, audit = _bridge(tmp_path, _server(), fake)

    with pytest.raises(MCPUndeclaredToolError, match="tools/list failed") as refused:
        bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)

    assert isinstance(refused.value.__context__, ValueError)
    assert fake.calls == []
    assert _undeclared_rows(audit) == [("pre_tool_use", "mcp_undeclared_tool", "deny")]

    # A failure is not cached: the next call lists again, and runs once the listing works.
    fake.list_error = None
    bridge.invoke_external_tool("weather", "forecast", {}, approval_granted=True)
    assert fake.methods == ["tools/list", "tools/list", "tools/call"]


def test_a_listing_the_governor_refuses_refuses_the_call(tmp_path: Path) -> None:
    fake = _Server(_OK)
    bridge, audit = _bridge(tmp_path, _server(), fake)

    # Without approval the governor refuses the listing, so the transport is never reached,
    # and the caller is told the call needs approval.
    with pytest.raises(MCPApprovalRequired, match=r"tools/list failed \(MCPApprovalRequired\)"):
        bridge.invoke_external_tool("weather", "forecast", {})

    assert fake.methods == []
    assert _undeclared_rows(audit) == [("pre_tool_use", "mcp_undeclared_tool", "deny")]


def test_the_http_route_answers_403_for_an_unlisted_tool_on_a_never_listed_server(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from evarness.server.harness_api.mcp_server import create_mcp_router

    fake = _Server(_OK)
    bridge, audit = _bridge(tmp_path, _server(), fake, requires_approval=False)
    app = FastAPI()
    app.include_router(create_mcp_router(bridge))

    response = TestClient(app).post(
        "/api/v1/mcp/servers/weather/tools/delete_everything/call", json={"arguments": {}}
    )

    assert response.status_code == 403
    assert "did not list it" in response.json()["detail"]
    assert fake.methods == ["tools/list"]
    assert _undeclared_rows(audit) == [("pre_tool_use", "mcp_undeclared_tool", "deny")]
