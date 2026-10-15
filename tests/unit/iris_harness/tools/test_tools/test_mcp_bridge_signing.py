"""MCP bridge signature gate — Phase 6 sub-phase 6b.2.

The gate runs in ``MCPBridge._invoke_transport`` before any transport dispatch, and before an
``executor=`` that stands in for the transport.
Shadow verifies + warns but proceeds; enforce + deny refuses the launch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iris_harness.kernel.governance.mcp_signing import (
    MCPSigningConfig,
    ServerSpec,
    TrustedKey,
    TrustStore,
    generate_keypair,
    sign,
)
from iris_harness.kernel.governance.plugins.mcp_allowlist import (
    MCPServerGovernance,
    MCPToolGovernance,
)
from iris_harness.tools.mcp_bridge import (
    MCPBridge,
    MCPBridgeConfig,
    MCPServerConfig,
    MCPSignatureError,
    build_server_spec,
    verify_server,
)


def _write_governor_policy(repo_root: Path) -> None:
    policy_dir = repo_root / "config" / "governor"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.yaml").write_text(
        "version: '1'\n"
        "routes:\n"
        "  - route: coding/mcp\n"
        "    allowed_actions: [session_open, call_tool, invoke_server]\n"
        "    requires_approval: true\n"
        "    rate_limit: {requests: 10, window_seconds: 3600}\n",
        encoding="utf-8",
    )


def _bridge(
    server: MCPServerConfig,
    *,
    mode: str = "shadow",
    unsigned_policy: str = "warn",
    trust_store: TrustStore | None = None,
    tmp_path: Path,
) -> MCPBridge:
    _write_governor_policy(tmp_path)
    captured: list[str] = []

    def _http(_server: MCPServerConfig, _payload: dict[str, object]) -> dict[str, object]:
        captured.append(_server.name)
        return {"result": {"ok": True}}

    bridge = MCPBridge(
        tmp_path,
        config=MCPBridgeConfig(enabled=True, servers=(server,)),
        signing_config=MCPSigningConfig(enabled=True, mode=mode, unsigned_policy=unsigned_policy),  # type: ignore[arg-type]
        trust_store=trust_store or TrustStore.empty(),
        http_requester=_http,
    )
    bridge._http_calls = captured  # type: ignore[attr-defined]
    return bridge


# The tools these tests call are declared, so no call has the bridge list the server first
# (#296); the listing has its own tests in test_mcp_bridge_results.py.
_DECLARED = {name: MCPToolGovernance(effect="read") for name in ("echo", "read_x")}


def _http_server(**overrides: object) -> MCPServerConfig:
    base: dict[str, object] = {
        "name": "remote",
        "enabled": True,
        "transport": "http",
        "url": "http://127.0.0.1:8787/mcp",
        "governance": MCPServerGovernance(tools=_DECLARED),
    }
    base.update(overrides)
    return MCPServerConfig(**base)  # type: ignore[arg-type]


def _sign_server(
    server: MCPServerConfig, key_id: str = "ops"
) -> tuple[MCPServerConfig, TrustStore]:
    # The signed spec must mirror what the bridge recomputes (env_keys, etc).
    spec = ServerSpec(
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=server.args,
        url=server.url,
        env_keys=tuple(server.env.keys()),
    )
    private, public = generate_keypair()
    signature = sign(private, spec.canonical_bytes())
    signed = server.model_copy(update={"signature": signature, "signed_by": key_id})
    store = TrustStore(keys={key_id: TrustedKey(key_id=key_id, public_key=public)})
    return signed, store


def test_disabled_signing_is_noop(tmp_path: Path) -> None:
    _write_governor_policy(tmp_path)
    bridge = MCPBridge(
        tmp_path,
        config=MCPBridgeConfig(enabled=True, servers=(_http_server(),)),
        signing_config=MCPSigningConfig.disabled(),
        http_requester=lambda _s, _p: {"result": {"ok": True}},
    )
    # Unsigned server, signing disabled → proceeds.
    assert bridge._invoke_transport(_http_server(), method="tools/list", params={}) == {"ok": True}


def test_shadow_unsigned_proceeds_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = _http_server()
    bridge = _bridge(server, mode="shadow", tmp_path=tmp_path)
    with caplog.at_level("WARNING", logger="iris_harness.tools.mcp_bridge"):
        result = bridge._invoke_transport(server, method="tools/list", params={})
    assert result == {"ok": True}  # proceeded
    assert any("mcp-signing[shadow]" in r.message for r in caplog.records)


def test_enforce_deny_blocks_unsigned(tmp_path: Path) -> None:
    server = _http_server()
    bridge = _bridge(server, mode="enforce", unsigned_policy="deny", tmp_path=tmp_path)
    with pytest.raises(MCPSignatureError):
        bridge._invoke_transport(server, method="tools/list", params={})
    assert bridge._http_calls == []  # never dispatched  # type: ignore[attr-defined]


def test_enforce_allows_verified_server(tmp_path: Path) -> None:
    signed, store = _sign_server(_http_server())
    bridge = _bridge(
        signed, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )
    result = bridge._invoke_transport(signed, method="tools/list", params={})
    assert result == {"ok": True}
    assert bridge._http_calls == ["remote"]  # type: ignore[attr-defined]


def test_enforce_deny_blocks_tampered_signed_server(tmp_path: Path) -> None:
    signed, store = _sign_server(_http_server())
    # Attacker swaps the URL after signing — signature no longer matches.
    tampered = signed.model_copy(update={"url": "http://evil.test/mcp"})
    bridge = _bridge(
        tampered, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )
    with pytest.raises(MCPSignatureError):
        bridge._invoke_transport(tampered, method="tools/list", params={})


def test_enforce_warn_policy_proceeds(tmp_path: Path) -> None:
    server = _http_server()
    bridge = _bridge(server, mode="enforce", unsigned_policy="warn", tmp_path=tmp_path)
    result = bridge._invoke_transport(server, method="tools/list", params={})
    assert result == {"ok": True}  # warn proceeds even under enforce


def _call_with_executor(bridge: MCPBridge, server: MCPServerConfig) -> list[str]:
    ran: list[str] = []
    bridge.invoke_external_tool(
        server.name,
        "echo",
        {},
        approval_granted=True,
        executor=lambda _server, tool, _args: ran.append(tool) or {"ok": True},
    )
    return ran


def test_the_executor_path_does_not_run_an_unsigned_server_under_enforce(tmp_path: Path) -> None:
    server = _http_server()
    bridge = _bridge(server, mode="enforce", unsigned_policy="deny", tmp_path=tmp_path)
    ran: list[str] = []
    with pytest.raises(MCPSignatureError):
        bridge.invoke_external_tool(
            server.name,
            "echo",
            {},
            approval_granted=True,
            executor=lambda _s, tool, _a: ran.append(tool) or {},
        )
    assert ran == []


def test_the_executor_path_does_not_run_a_tampered_server_under_enforce(tmp_path: Path) -> None:
    signed, store = _sign_server(_http_server())
    tampered = signed.model_copy(update={"url": "http://evil.test/mcp"})
    bridge = _bridge(
        tampered, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )
    with pytest.raises(MCPSignatureError):
        _call_with_executor(bridge, tampered)


def test_the_executor_path_runs_a_verified_server_under_enforce(tmp_path: Path) -> None:
    signed, store = _sign_server(_http_server())
    bridge = _bridge(
        signed, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )
    assert _call_with_executor(bridge, signed) == ["echo"]


def test_the_executor_path_warns_in_shadow_like_the_transport_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = _http_server()
    bridge = _bridge(server, mode="shadow", tmp_path=tmp_path)
    with caplog.at_level("WARNING", logger="iris_harness.tools.mcp_bridge"):
        assert _call_with_executor(bridge, server) == ["echo"]
    assert sum("mcp-signing[shadow]" in r.message for r in caplog.records) == 1


def _signed_with_pinned_env(
    url: str = "https://api.example/v1",
) -> tuple[MCPServerConfig, TrustStore]:
    server = _http_server(
        env={"UPSTREAM_URL": url, "LOG_LEVEL": "info"}, pinned_env=("UPSTREAM_URL",)
    )
    private, public = generate_keypair()
    signature = sign(private, build_server_spec(server).canonical_bytes())
    signed = server.model_copy(update={"signature": signature, "signed_by": "ops"})
    return signed, TrustStore(keys={"ops": TrustedKey(key_id="ops", public_key=public)})


def test_a_repointed_pinned_env_value_no_longer_verifies_and_is_refused(tmp_path: Path) -> None:
    signed, store = _signed_with_pinned_env()
    moved = signed.model_copy(update={"env": {**signed.env, "UPSTREAM_URL": "https://evil.test"}})
    bridge = _bridge(
        moved, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )

    with pytest.raises(MCPSignatureError):
        bridge._invoke_transport(moved, method="tools/list", params={})
    assert bridge._http_calls == []  # type: ignore[attr-defined]


def test_an_unpinned_env_value_may_change_after_signing(tmp_path: Path) -> None:
    signed, store = _signed_with_pinned_env()
    changed = signed.model_copy(update={"env": {**signed.env, "LOG_LEVEL": "debug"}})
    bridge = _bridge(
        changed, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )

    assert bridge._invoke_transport(changed, method="tools/list", params={}) == {"ok": True}


def test_the_pinned_env_value_is_in_the_spec_and_an_entry_that_pins_nothing_is_unchanged() -> None:
    pinned = build_server_spec(_http_server(env={"A": "1"}, pinned_env=("A",)))
    plain = build_server_spec(_http_server(env={"A": "1"}))

    assert pinned.canonical_bytes() != plain.canonical_bytes()
    assert b"pinned_env" not in plain.canonical_bytes()


@pytest.mark.parametrize(
    ("pinned", "message"),
    [
        (("API_KEY",), "looks like a credential"),
        (("GITHUB_TOKEN",), "looks like a credential"),
        (("OAUTH_CLIENT",), "refusing to pin 'OAUTH_CLIENT'"),
        (("DATABASE_DSN",), "non-secret runtime invariants"),
        (("BEARER_VALUE",), "Move credentials to the vault"),
        (("SESSION_COOKIE",), "looks like a credential"),
        (("SIGNING_PRIVATE",), "looks like a credential"),
        (("1BAD",), "not an environment variable name"),
        (("MISSING",), "not set under env"),
    ],
)
def test_pinned_env_refuses_a_credential_a_bad_name_and_an_unset_name(
    pinned: tuple[str, ...], message: str
) -> None:
    env = {
        "API_KEY": "x",
        "GITHUB_TOKEN": "y",
        "OAUTH_CLIENT": "a",
        "DATABASE_DSN": "b",
        "BEARER_VALUE": "c",
        "SESSION_COOKIE": "d",
        "SIGNING_PRIVATE": "e",
        "1BAD": "z",
    }
    with pytest.raises(ValueError, match=message):
        _http_server(env=env, pinned_env=pinned)


# --- a refusal is a PermissionError with one deny row -----------------------------------


def _kernel_bridge(tmp_path: Path, server: MCPServerConfig, *, store: TrustStore | None = None):  # type: ignore[no-untyped-def]
    from iris_harness.kernel.governance import build_default_kernel
    from iris_harness.kernel.governance.approvals import ApprovalQueue
    from iris_harness.kernel.governance.audit import AuditLog

    _write_governor_policy(tmp_path)
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
        signing_config=MCPSigningConfig(enabled=True, mode="enforce", unsigned_policy="deny"),
        trust_store=store or TrustStore.empty(),
        http_requester=lambda _s, _p: {"result": {"ok": True}},
    )
    return bridge, audit


def _read_server(**overrides: object) -> MCPServerConfig:
    return _http_server(
        governance=MCPServerGovernance(undeclared_tools="read", tools=_DECLARED), **overrides
    )


def test_a_signature_refusal_is_a_permission_error() -> None:
    assert issubclass(MCPSignatureError, PermissionError)


def test_a_refused_call_leaves_one_deny_row_and_no_allow_rows(tmp_path: Path) -> None:
    server = _read_server()
    bridge, audit = _kernel_bridge(tmp_path, server)

    with pytest.raises(PermissionError, match="failed signature verification"):
        bridge.invoke_external_tool(server.name, "read_x", {}, approval_granted=True)

    rows = audit.query()
    assert [(r.hook_point, r.plugin, r.decision) for r in rows] == [
        ("pre_tool_use", "mcp_signature", "deny")
    ]
    payload = json.loads(rows[0].payload_json)
    assert payload["signature_status"] == "unsigned"
    assert payload["mcp_server"] == "remote" and payload["mcp_tool"] == "read_x"


def test_a_refused_call_does_not_reach_the_transport_or_the_governor(tmp_path: Path) -> None:
    server = _read_server()
    bridge, _audit = _kernel_bridge(tmp_path, server)

    with pytest.raises(MCPSignatureError):
        bridge.invoke_external_tool(server.name, "read_x", {}, approval_granted=True)

    assert not (tmp_path / "data" / "audit.db").exists()  # the governor never saw the call


def test_a_verified_call_still_runs_through_every_hook(tmp_path: Path) -> None:
    signed, store = _sign_server(_read_server())
    bridge, audit = _kernel_bridge(tmp_path, signed, store=store)

    result = bridge.invoke_external_tool(signed.name, "read_x", {}, approval_granted=True)

    assert result.result == {"ok": True}
    plugins = [r.plugin for r in audit.query() if r.hook_point == "pre_tool_use"]
    assert "mcp_signature" not in plugins and len(plugins) > 1
    assert all(r.decision == "allow" for r in audit.query() if r.hook_point == "pre_tool_use")


def test_a_refused_tools_list_raises_before_the_governor_records_an_allow(tmp_path: Path) -> None:
    server = _read_server()
    bridge, _audit = _kernel_bridge(tmp_path, server)

    with pytest.raises(PermissionError, match="failed signature verification"):
        bridge.list_external_tools(server.name, approval_granted=True)

    assert not (tmp_path / "data" / "audit.db").exists()


def test_the_http_route_answers_403_for_a_signature_refusal() -> None:
    from iris_harness.server.iris_api.mcp_server import _as_http_exception

    error = _as_http_exception(MCPSignatureError("MCP server 'x' failed signature verification"))

    assert error.status_code == 403 and "signature" in str(error.detail)


# --- the verdict is on the row of an allowed call ----------------------------------------


def _row_payloads(audit: Any, hook_point: str) -> list[dict[str, Any]]:
    return [json.loads(r.payload_json) for r in audit.query() if r.hook_point == hook_point]


def test_a_verified_call_carries_the_verdict_signer_and_spec_hash_on_every_row(
    tmp_path: Path,
) -> None:
    signed, store = _sign_server(_read_server())
    bridge, audit = _kernel_bridge(tmp_path, signed, store=store)

    bridge.invoke_external_tool(signed.name, "read_x", {}, approval_granted=True)

    for hook_point in ("pre_tool_use", "post_tool_use"):
        payloads = _row_payloads(audit, hook_point)
        assert payloads, hook_point
        for payload in payloads:
            assert payload["signature_status"] == "verified"
            assert payload["signature_key_id"] == "ops"
            assert len(payload["signature_spec_sha256"]) == 64


def test_the_spec_hash_on_the_row_is_the_hash_of_the_signed_spec(tmp_path: Path) -> None:
    import hashlib

    signed, store = _sign_server(_read_server())
    bridge, audit = _kernel_bridge(tmp_path, signed, store=store)

    bridge.invoke_external_tool(signed.name, "read_x", {}, approval_granted=True)

    expected = hashlib.sha256(build_server_spec(signed).canonical_bytes()).hexdigest()
    assert {p["signature_spec_sha256"] for p in _row_payloads(audit, "pre_tool_use")} == {expected}


def test_a_shadow_call_to_an_unsigned_server_records_the_unsigned_verdict(tmp_path: Path) -> None:
    server = _read_server()
    bridge, audit = _kernel_bridge(tmp_path, server)
    bridge._transport._signing_config = MCPSigningConfig(enabled=True, mode="shadow")

    bridge.invoke_external_tool(server.name, "read_x", {}, approval_granted=True)

    payloads = _row_payloads(audit, "pre_tool_use")
    assert {p["signature_status"] for p in payloads} == {"unsigned"}
    assert not any("signature_key_id" in p for p in payloads)


def test_a_call_with_signing_off_says_it_was_not_checked(tmp_path: Path) -> None:
    server = _read_server()
    bridge, audit = _kernel_bridge(tmp_path, server)
    bridge._transport._signing_config = MCPSigningConfig.disabled()

    bridge.invoke_external_tool(server.name, "read_x", {}, approval_granted=True)

    payloads = _row_payloads(audit, "pre_tool_use")
    assert {p["signature_status"] for p in payloads} == {"not_checked"}
    assert not any("signature_spec_sha256" in p for p in payloads)


def test_the_deny_row_carries_the_verdict_too(tmp_path: Path) -> None:
    server = _read_server()
    bridge, audit = _kernel_bridge(tmp_path, server)

    with pytest.raises(MCPSignatureError):
        bridge.invoke_external_tool(server.name, "read_x", {}, approval_granted=True)

    (payload,) = _row_payloads(audit, "pre_tool_use")
    assert payload["signature_status"] == "unsigned"
    assert len(payload["signature_spec_sha256"]) == 64


# --- no policy file: the default, not off ---------------------------------------------------


def _policy_dir(tmp_path: Path, text: str | None) -> Path:
    if text is not None:
        policy = tmp_path / "config" / "governance"
        policy.mkdir(parents=True)
        (policy / "mcp-signing.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def _unsigned_call_rows(tmp_path: Path) -> list[dict[str, object]]:
    server = _read_server()
    from iris_harness.kernel.governance import build_default_kernel
    from iris_harness.kernel.governance.approvals import ApprovalQueue
    from iris_harness.kernel.governance.audit import AuditLog

    _write_governor_policy(tmp_path)
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
        http_requester=lambda _s, _p: {"result": {"ok": True}},
    )
    bridge.invoke_external_tool(server.name, "read_x", {}, approval_granted=True)
    return [
        json.loads(r.payload_json) for r in audit.query() if "signature_status" in r.payload_json
    ]


def test_a_repo_with_no_signing_policy_still_verifies_in_shadow(tmp_path: Path) -> None:
    payloads = _unsigned_call_rows(_policy_dir(tmp_path, None))

    assert payloads
    assert {p["signature_status"] for p in payloads} == {"unsigned"}  # checked, and let through


def test_a_policy_that_says_enabled_false_is_the_only_way_to_turn_it_off(tmp_path: Path) -> None:
    payloads = _unsigned_call_rows(_policy_dir(tmp_path, "version: 1\nenabled: false\n"))

    assert {p["signature_status"] for p in payloads} == {"not_checked"}


def test_an_unreadable_policy_stops_the_bridge_instead_of_turning_signing_off(
    tmp_path: Path,
) -> None:
    root = _policy_dir(tmp_path, "version: 1\nmode: enfroce\n")
    _write_governor_policy(root)

    with pytest.raises(ValueError, match="mcp-signing.yaml"):
        MCPBridge(root, config=MCPBridgeConfig(enabled=True, servers=(_read_server(),)))


# --- launchers that name no script file (#287) -----------------------------------------------


def _stdio(command: str, *args: str) -> MCPServerConfig:
    return MCPServerConfig(
        name="launched", enabled=True, transport="stdio", command=command, args=tuple(args)
    )


def _signed(server: MCPServerConfig) -> tuple[MCPServerConfig, TrustStore]:
    private, public = generate_keypair()
    signature = sign(private, build_server_spec(server).canonical_bytes())
    signed = server.model_copy(update={"signature": signature, "signed_by": "ops"})
    return signed, TrustStore(keys={"ops": TrustedKey(key_id="ops", public_key=public)})


def test_a_signed_launcher_that_pins_its_release_verifies() -> None:
    signed, store = _signed(_stdio("npx", "-y", "pkg@1.2.3"))

    _, verdict = verify_server(signed, store)

    assert verdict.status == "verified"


def test_a_changed_pin_no_longer_verifies() -> None:
    signed, store = _signed(_stdio("npx", "-y", "pkg@1.2.3"))
    bumped = signed.model_copy(update={"args": ("-y", "pkg@1.2.4")})

    assert verify_server(bumped, store)[1].status == "invalid"


def test_a_valid_signature_over_an_unpinned_launcher_is_not_trusted() -> None:
    # Signed before the rule, or by a hand-built spec: the signature verifies but binds nothing.
    signed, store = _signed(_stdio("npx", "-y", "pkg"))

    _, verdict = verify_server(signed, store)

    assert verdict.status == "invalid"
    assert "exact version" in verdict.reason and verdict.key_id == "ops"


def test_an_unpinned_launcher_is_refused_under_enforce_and_recorded_in_shadow(
    tmp_path: Path,
) -> None:
    signed, store = _signed(_stdio("uvx", "pkg"))

    enforced = _bridge(
        signed, mode="enforce", unsigned_policy="deny", trust_store=store, tmp_path=tmp_path
    )
    shadow = _bridge(signed, mode="shadow", trust_store=store, tmp_path=tmp_path)

    assert enforced._transport.check_signature(signed).action == "deny"
    assert shadow._transport.check_signature(signed).action == "warn"
    with pytest.raises(MCPSignatureError, match="exact version"):
        enforced._transport.enforce_signature(signed)


def test_a_local_module_is_hashed_and_an_edit_after_signing_is_caught(tmp_path: Path) -> None:
    package = tmp_path / "localmcp"
    package.mkdir()
    (package / "__main__.py").write_text("print('v1')\n")
    server = _stdio("python3", "-m", "localmcp")
    private, public = generate_keypair()
    spec = build_server_spec(server, base_dir=tmp_path)
    assert spec.entrypoint_sha256
    signed = server.model_copy(
        update={"signature": sign(private, spec.canonical_bytes()), "signed_by": "ops"}
    )
    store = TrustStore(keys={"ops": TrustedKey(key_id="ops", public_key=public)})
    assert verify_server(signed, store, base_dir=tmp_path)[1].status == "verified"

    (package / "__main__.py").write_text("print('evil')\n")

    assert verify_server(signed, store, base_dir=tmp_path)[1].status == "invalid"


def test_a_module_that_is_not_local_is_unpinned(tmp_path: Path) -> None:
    signed, store = _signed(_stdio("python3", "-m", "installed_somewhere"))

    _, verdict = verify_server(signed, store, base_dir=tmp_path)

    assert verdict.status == "invalid" and "-m installed_somewhere" in verdict.reason
