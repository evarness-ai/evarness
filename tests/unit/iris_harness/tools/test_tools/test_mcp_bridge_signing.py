"""MCP bridge signature gate — Phase 6 sub-phase 6b.2.

The gate runs in ``MCPBridge._invoke_transport`` before any transport dispatch, and before an
``executor=`` that stands in for the transport.
Shadow verifies + warns but proceeds; enforce + deny refuses the launch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_harness.kernel.governance.mcp_signing import (
    MCPSigningConfig,
    ServerSpec,
    TrustedKey,
    TrustStore,
    generate_keypair,
    sign,
)
from iris_harness.tools.mcp_bridge import (
    MCPBridge,
    MCPBridgeConfig,
    MCPServerConfig,
    MCPSignatureError,
    build_server_spec,
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


def _http_server(**overrides: object) -> MCPServerConfig:
    base: dict[str, object] = {
        "name": "remote",
        "enabled": True,
        "transport": "http",
        "url": "http://127.0.0.1:8787/mcp",
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
