# ruff: noqa: S603
"""Disabled-by-default MCP bridge for local skill export and governed server calls."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import select
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iris_harness import __version__
from iris_harness.foundation.ids import new_ulid
from iris_harness.foundation.observability.logging_setup import log_egress
from iris_harness.kernel.governance.call_context import register_call
from iris_harness.kernel.governance.hooks.tool_payload import TOOL_REPORTED_ERROR
from iris_harness.kernel.governance.mcp_signing import (
    MCPSigningConfig,
    ServerSpec,
    SignatureVerdict,
    SigningAction,
    TrustStore,
    file_sha256,
    resolve_signing_action,
    verify_server_signature,
)
from iris_harness.kernel.governance.mcp_signing.launcher import python_module, unpinned_launcher
from iris_harness.kernel.governance.mcp_signing.spec import tree_sha256
from iris_harness.kernel.governance.plugins.mcp_allowlist import MCPServerGovernance
from iris_harness.kernel.governor import GovernorGuardDecision, IRISGovernorService
from iris_harness.tools.skills import SkillRegistry

if TYPE_CHECKING:
    from iris_harness.kernel.governance.hooks.types import HookContext
    from iris_harness.kernel.governance.kernel import GovernanceKernel

logger = logging.getLogger(__name__)


class MCPSignatureError(PermissionError):
    """Raised when MCP signing is in ``enforce`` mode and a server's signature
    does not verify (and ``unsigned_policy`` is ``deny``) — the launch/connect
    is refused before any process starts."""


class MCPApprovalRequired(PermissionError):
    """Raised when the governor refuses an MCP action because nobody approved it.

    A subclass of :exc:`PermissionError` so existing callers keep working; the HTTP
    API tells it apart to say "approval required" instead of echoing the governor's
    wording, which names a metadata flag the client is not allowed to set."""


class MCPUndeclaredToolError(PermissionError):
    """Raised when a call names a tool the server is not known to have.

    Neither the operator's ``governance.tools`` nor the server's last ``tools/list``
    names it, so the call is refused before any hook runs and the server is never
    reached."""


# Metadata keys the governor reads as consent (``kernel/governor/policy.py``,
# ``_has_approval_flag``). Only the bridge's own ``approval_granted`` argument may set one.
_APPROVAL_KEYS = frozenset({"approval_granted", "approved"})


# What a stdio server is started with from the host's environment, besides its own ``env:``.
# Anything else (every ``IRIS_*`` setting, ``IRIS_VAULT_MASTER_KEY``, provider keys, tokens
# in the owner's shell) stays behind unless the entry names it in ``inherit_env``.
_CHILD_ENV_BASE = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "TZ"})
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_CREDENTIAL_WORDS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "DSN",
    "BEARER",
    "COOKIE",
    "PRIVATE",
)


def child_environment(
    server: MCPServerConfig, host: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment a stdio server starts with: the base names, the locale (``LC_*``), the
    variables its entry lists in ``inherit_env``, then its own ``env:`` on top."""
    host = os.environ if host is None else host
    allowed = _CHILD_ENV_BASE | set(server.inherit_env)
    environment = {k: v for k, v in host.items() if k in allowed or k.startswith("LC_")}
    environment.update(server.env)
    return environment


class MCPServerConfig(BaseModel):
    """Configuration for one external MCP server integration."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(..., min_length=1)
    description: str = Field(default="", min_length=0)
    enabled: bool = Field(default=False)
    transport: str = Field(default="stdio", min_length=1)
    command: str | None = Field(default=None)
    args: tuple[str, ...] = Field(default_factory=tuple)
    url: str | None = Field(default=None)
    env: dict[str, str] = Field(default_factory=dict)
    # Names of host environment variables this stdio server may see, beyond the base set in
    # ``child_environment``. Never an ``IRIS_*`` name: pass a value in ``env:`` for that.
    inherit_env: tuple[str, ...] = Field(default_factory=tuple)
    # Names under ``env`` whose *values* are signed with the entry, for a non-secret value
    # such as an upstream URL: re-pointing it after signing no longer verifies. Never a
    # credential (those belong in the vault); the other ``env`` values stay unsigned.
    pinned_env: tuple[str, ...] = Field(default_factory=tuple)
    timeout_seconds: float = Field(default=10.0, gt=0)
    governance: MCPServerGovernance | None = Field(default=None)
    # Phase 6 MCP signing (6b): Ed25519 signature over the canonical launch spec
    # and the trust-store key id that produced it. Both optional — an unsigned
    # entry is allowed/warned/denied per config/governance/mcp-signing.yaml.
    signature: str | None = Field(default=None)
    signed_by: str | None = Field(default=None)

    @field_validator("inherit_env")
    @classmethod
    def validate_inherit_env(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        """Plain variable names only, and none of IRIS's own settings or vault key."""
        for name in names:
            if not _ENV_NAME.match(name):
                raise ValueError(f"inherit_env: {name!r} is not an environment variable name")
            if name.upper().startswith("IRIS_"):
                raise ValueError(
                    f"inherit_env: {name} is an IRIS setting and is never passed on; "
                    "set a value for it under env: if the server needs one"
                )
        return tuple(dict.fromkeys(names))

    @field_validator("pinned_env")
    @classmethod
    def validate_pinned_env(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        """Plain variable names, none that reads as a credential."""
        for name in names:
            if not _ENV_NAME.match(name):
                raise ValueError(f"pinned_env: {name!r} is not an environment variable name")
            if any(word in name.upper() for word in _CREDENTIAL_WORDS):
                raise ValueError(
                    f"pinned_env: refusing to pin {name!r}: it looks like a credential. "
                    "pinned_env is for non-secret runtime invariants (an upstream URL, a log "
                    "level); its values are hashed into the signed spec. Move credentials to "
                    "the vault (a vault:// handle under env:)."
                )
        return tuple(dict.fromkeys(names))

    @model_validator(mode="after")
    def validate_pinned_env_is_set(self) -> MCPServerConfig:
        """A pinned name has a value under ``env`` to pin."""
        missing = [name for name in self.pinned_env if name not in self.env]
        if missing:
            raise ValueError(f"pinned_env: {', '.join(missing)} not set under env:")
        return self

    @model_validator(mode="after")
    def validate_enabled_transport(self) -> MCPServerConfig:
        """Require the minimum connection details only when a server is enabled."""
        if not self.enabled:
            return self

        if self.transport == "stdio" and not self.command:
            raise ValueError("enabled stdio MCP servers require a command")
        if self.transport in {"http", "sse"} and not self.url:
            raise ValueError("enabled HTTP/SSE MCP servers require a url")
        return self


class MCPBridgeConfig(BaseModel):
    """Top-level MCP bridge configuration."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    version: str = Field(default="1", min_length=1)
    enabled: bool = Field(default=False)
    auto_export_local_tools: bool = Field(default=True)
    servers: tuple[MCPServerConfig, ...] = Field(default_factory=tuple)


class MCPToolDefinition(BaseModel):
    """MCP-shaped representation of one local or external tool."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    governor_route: str = Field(..., min_length=1)
    source_kind: str = Field(..., min_length=1)
    source_name: str = Field(..., min_length=1)


class MCPInvocationResult(BaseModel):
    """Structured record of a governed MCP tool invocation."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    server_name: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    governor_decision: GovernorGuardDecision = Field(...)
    result: Any = Field(...)


def load_mcp_bridge_config(
    repo_root: Path,
    *,
    config_path: Path | None = None,
) -> MCPBridgeConfig:
    """Load the MCP bridge config, defaulting to a disabled bridge when absent."""
    resolved_path = (
        config_path
        if config_path is not None
        else repo_root / "config" / "coding-agent" / "mcp-servers.yaml"
    ).resolve()
    if not resolved_path.exists():
        return MCPBridgeConfig()

    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("mcp bridge configuration must decode to a mapping")
    return MCPBridgeConfig.model_validate(payload)


def _refusal(decision: GovernorGuardDecision, *, approval_granted: bool) -> PermissionError:
    """The error for a refused MCP action: approval missing, or any other refusal."""
    if decision.requires_approval and not approval_granted:
        return MCPApprovalRequired(decision.reason)
    return PermissionError(decision.reason)


def _signature_refusal(server: MCPServerConfig, verdict: SignatureVerdict) -> str:
    """The message of a launch refused for its signature, logged as the policy's DENY."""
    logger.warning(
        "mcp-signing[enforce] DENY server=%s status=%s reason=%s",
        server.name,
        verdict.status,
        verdict.reason,
    )
    return (
        f"MCP server {server.name!r} failed signature verification: "
        f"{verdict.status} ({verdict.reason})"
    )


def _entrypoint_script(args: tuple[str, ...], base_dir: Path | None = None) -> Path | None:
    """The first argument that names a local file: the script an interpreter is asked to run.

    ``python server.py`` and ``node server.js`` have the interpreter as ``command``, so hashing
    ``command`` alone leaves the server's own code outside the signature. ``-m pkg`` and a
    launcher like ``npx pkg`` name no file and return ``None``."""
    for arg in args:
        if arg.startswith("-"):
            continue
        candidate = Path(arg)
        if base_dir is not None and not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.is_file():
            return candidate
    return None


def _local_module(command: str | None, args: tuple[str, ...], base_dir: Path | None) -> Path | None:
    """The package directory or module file a ``python -m <module>`` launcher runs, if local."""
    module = python_module(command, args)
    if module is None:
        return None
    relative = Path(*module.split("."))
    base = base_dir or Path()
    for candidate in (base / relative, base / relative.with_suffix(".py")):
        if candidate.is_dir() and any(candidate.rglob("*.py")):
            return candidate
        if candidate.is_file():
            return candidate
    return None


def launcher_gap(server: MCPServerConfig, spec: ServerSpec) -> str | None:
    """Why the code a stdio server launches is not bound by its signature, or ``None``.

    Covered when a script or local package was hashed into ``spec``, or when ``args`` pin the
    release a package launcher fetches. Shared by ``iris mcp sign``, ``verify``, ``list`` and
    the launch gate, so they cannot disagree about what a signature holds."""
    if server.transport != "stdio" or spec.entrypoint_sha256:
        return None
    return unpinned_launcher(server.command, server.args)


def verify_server(
    server: MCPServerConfig, trust_store: TrustStore, *, base_dir: Path | None = None
) -> tuple[ServerSpec, SignatureVerdict]:
    """The server's signature verdict, with an unpinned launcher counted against it.

    A signature that verifies but binds no code is not trusted: the verdict becomes
    ``invalid`` with the reason, so the signing policy treats it like a changed server
    (recorded in shadow, ``unsigned_policy`` under enforce)."""
    spec = build_server_spec(server, base_dir=base_dir)
    verdict = verify_server_signature(
        spec=spec,
        signature=server.signature,
        signed_by=server.signed_by,
        trust_store=trust_store,
    )
    gap = launcher_gap(server, spec)
    if verdict.is_trusted and gap:
        verdict = SignatureVerdict(
            "invalid", f"signature does not bind the server's code: {gap}", key_id=verdict.key_id
        )
    return spec, verdict


def build_server_spec(server: MCPServerConfig, *, base_dir: Path | None = None) -> ServerSpec:
    """Build the canonical signable spec for ``server``.

    The single source of truth shared by the bridge gate and the ``iris mcp
    sign``/``verify`` CLI, so the CLI signs exactly what the gate checks. Hashes
    the binary of a local-path ``stdio`` command and the script it is asked to run, or the
    files of a local ``-m`` package. A launcher that fetches its code (``npx <pkg>``) is not a
    file: :func:`launcher_gap` says whether its ``args`` pin the release instead."""
    package_sha256: str | None = None
    entrypoint_sha256: str | None = None
    if server.transport == "stdio" and server.command:
        candidate = Path(server.command)
        if candidate.is_file():
            package_sha256 = file_sha256(candidate)
        script = _entrypoint_script(server.args, base_dir)
        if script is not None:
            entrypoint_sha256 = file_sha256(script)
        else:
            package = _local_module(server.command, server.args, base_dir)
            if package is not None:
                entrypoint_sha256 = tree_sha256(package)
    return ServerSpec(
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=server.args,
        url=server.url,
        env_keys=tuple(server.env.keys()),
        inherit_env=server.inherit_env,
        package_sha256=package_sha256,
        entrypoint_sha256=entrypoint_sha256,
        pinned_env=tuple(sorted((name, server.env[name]) for name in server.pinned_env)),
    )


def _load_signing_config(repo_root: Path) -> MCPSigningConfig:
    """Load the MCP-signing policy.

    An absent file is the default policy (shadow: verify and record, never block). An
    unreadable one is an error: a typo in a policy that says ``enforce`` must not turn
    signing off, so it stops the bridge and names the file.
    """
    path = repo_root / "config" / "governance" / "mcp-signing.yaml"
    try:
        return MCPSigningConfig.from_yaml(path)
    except Exception as exc:
        raise ValueError(f"mcp-signing policy {path} is unreadable: {exc}") from exc


def _load_trust_store(repo_root: Path, config: MCPSigningConfig) -> TrustStore:
    """Load the trust store referenced by ``config``; absent/disabled → empty."""
    if not config.enabled:
        return TrustStore.empty()
    try:
        path = Path(config.trust_store)
        if not path.is_absolute():
            path = repo_root / path
        return TrustStore.from_yaml(path)
    except Exception:  # degrade to empty (every signed server untrusted)
        logger.warning(
            "mcp-signing trust store %s is unreadable; no key is trusted",
            config.trust_store,
            exc_info=True,
        )
        return TrustStore.empty()


def declared_effect(server: MCPServerConfig, tool_name: str) -> str | None:
    """The effect the operator declared for ``tool_name`` on ``server``, else None."""
    if server.governance is None:
        return None
    declared = server.governance.tools.get(tool_name)
    return declared.effect if declared is not None else None


def effective_effect(server: MCPServerConfig, tool_name: str) -> str:
    """What the bridge treats ``tool_name`` as: the declared effect, else the server's default.

    An undeclared tool fails closed to ``destructive`` (issue #180); the operator's
    ``governance.undeclared_tools: read`` is the per-server opt-out.
    """
    declared = declared_effect(server, tool_name)
    if declared is not None:
        return declared
    if server.governance is None:
        return "destructive"
    return server.governance.undeclared_tools


def undeclared_tool_names(server: MCPServerConfig, tool_names: Iterable[str]) -> list[str]:
    """The names in ``tool_names`` the operator has not declared on ``server``."""
    return [name for name in tool_names if declared_effect(server, name) is None]


def is_error_result(result: Any) -> bool:
    """Whether a ``tools/call`` result is the server saying the call failed (``isError``)."""
    return isinstance(result, dict) and bool(result.get("isError"))


def _undeclared_tool_refusal(server: MCPServerConfig, tool_name: str) -> str:
    return (
        f"MCP tool '{server.name}/{tool_name}' refused: the server did not list it in "
        "tools/list and it is not declared under governance.tools"
    )


def _listing_failed_refusal(server: MCPServerConfig, tool_name: str, exc: Exception) -> str:
    # The class only: a transport error can carry the server's stderr, which is not ledger text.
    return (
        f"MCP tool '{server.name}/{tool_name}' refused: it is not declared under "
        f"governance.tools and tools/list failed ({type(exc).__name__}), so the server's "
        "tools are not known"
    )


@dataclass(frozen=True)
class SignatureCheck:
    """One server's signature verdict, what the policy does about it, and what the ledger records.

    ``enabled`` is false when no signing policy is in force; the rows then say
    ``not_checked`` rather than a verdict that nothing acted on.
    """

    verdict: SignatureVerdict
    action: SigningAction
    enabled: bool
    spec_sha256: str

    @property
    def row_fields(self) -> dict[str, str]:
        """The keys a ``PRE``/``POST_TOOL_USE`` payload carries so the row names the verdict."""
        if not self.enabled:
            return {"signature_status": "not_checked"}
        fields = {
            "signature_status": self.verdict.status,
            "signature_spec_sha256": self.spec_sha256,
        }
        if self.verdict.key_id:
            fields["signature_key_id"] = self.verdict.key_id
        return fields


class MCPTransport:
    """One MCP call over a server's transport, behind the launch-signature gate.

    The wire half of the bridge, with no governance of its own: a caller that already runs
    inside the governance kernel (the plugin host, whose tools the governed runner wraps in
    one PRE/POST pair) calls this, so a call is governed once. ``MCPBridge`` keeps its own
    governor and kernel around the same transport for the coding agent's route.
    """

    def __init__(
        self,
        working_dir: Path,
        *,
        signing_config: MCPSigningConfig,
        trust_store: TrustStore,
        http_requester: Callable[[MCPServerConfig, dict[str, Any]], Any] | None = None,
        sse_exchange_runner: (
            Callable[
                [MCPServerConfig, tuple[dict[str, Any], ...], tuple[str, ...]],
                dict[str, Any],
            ]
            | None
        ) = None,
    ) -> None:
        self.working_dir = working_dir
        self._signing_config = signing_config
        self._trust_store = trust_store
        self._http_requester = http_requester or _default_http_requester
        self._sse_exchange_runner = sse_exchange_runner or _default_sse_exchange_runner

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> MCPTransport:
        """A transport under the repository's MCP-signing policy and trust store."""
        signing = _load_signing_config(repo_root)
        return cls(
            repo_root, signing_config=signing, trust_store=_load_trust_store(repo_root, signing)
        )

    def invoke(
        self,
        server: MCPServerConfig,
        *,
        method: str,
        params: dict[str, Any],
        working_dir: Path | None = None,
    ) -> Any:
        """Invoke one MCP method over the configured transport."""
        # Phase 6 G (signing): verify the server's signature before any process
        # is launched or any endpoint is contacted. Shadow → warn + proceed;
        # enforce + deny → raise (refuse) before the request is built.
        self.enforce_signature(server, working_dir)
        request_payload = _build_jsonrpc_request(
            request_id=f"iris-{server.name}-{method.replace('/', '-')}",
            method=method,
            params=params,
        )
        if server.transport == "http":
            response_payload = self._http_requester(server, request_payload)
            return _extract_mcp_result(response_payload)
        if server.transport == "stdio":
            return self._invoke_stdio_transport(server, request_payload, working_dir)
        if server.transport == "sse":
            return self._invoke_sse_transport(server, request_payload)

        raise ValueError(
            f"MCP transport '{server.transport}' is not implemented yet; supported transports: http, stdio, sse"
        )

    def verify_signature(
        self, server: MCPServerConfig, base_dir: Path | None = None
    ) -> SignatureVerdict:
        return verify_server(server, self._trust_store, base_dir=base_dir)[1]

    def check_signature(
        self, server: MCPServerConfig, base_dir: Path | None = None
    ) -> SignatureCheck:
        """The server's verdict and what the policy does about it; logs nothing, refuses nothing."""
        spec, verdict = verify_server(server, self._trust_store, base_dir=base_dir)
        return SignatureCheck(
            verdict=verdict,
            action=resolve_signing_action(verdict, self._signing_config),
            enabled=self._signing_config.enabled,
            spec_sha256=hashlib.sha256(spec.canonical_bytes()).hexdigest(),
        )

    def enforce_signature(self, server: MCPServerConfig, base_dir: Path | None = None) -> None:
        """Verify the server signature and apply the configured policy.

        No-op when signing is disabled. In ``shadow`` mode every non-verified
        verdict logs a warning but proceeds; in ``enforce`` mode the configured
        ``unsigned_policy`` applies and ``deny`` raises ``MCPSignatureError``."""
        if not self._signing_config.enabled:
            return
        verdict = self.verify_signature(server, base_dir)
        action = resolve_signing_action(verdict, self._signing_config)
        if action == "deny":
            raise MCPSignatureError(_signature_refusal(server, verdict))
        if not verdict.is_trusted:
            logger.warning(
                "mcp-signing[%s] server=%s status=%s reason=%s key_id=%s (proceeding)",
                self._signing_config.mode,
                server.name,
                verdict.status,
                verdict.reason,
                verdict.key_id,
            )

    def _invoke_stdio_transport(
        self,
        server: MCPServerConfig,
        request_payload: dict[str, Any],
        working_dir: Path | None = None,
    ) -> Any:
        """Invoke one MCP method over a one-shot stdio session with initialize handshake."""
        initialize_payload = _build_jsonrpc_request(
            request_id=f"iris-{server.name}-initialize",
            method="initialize",
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "iris", "version": __version__},
            },
        )
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        responses = _run_stdio_exchange(
            repo_root=working_dir or self.working_dir,
            server=server,
            outgoing_messages=(initialize_payload, initialized_notification, request_payload),
            expected_response_ids=(
                str(initialize_payload["id"]),
                str(request_payload["id"]),
            ),
        )
        _extract_mcp_result(responses[str(initialize_payload["id"])])
        return _extract_mcp_result(responses[str(request_payload["id"])])

    def _invoke_sse_transport(
        self,
        server: MCPServerConfig,
        request_payload: dict[str, Any],
    ) -> Any:
        """Invoke one MCP method over an SSE session with initialize handshake."""
        initialize_payload = _build_jsonrpc_request(
            request_id=f"iris-{server.name}-initialize",
            method="initialize",
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "iris", "version": __version__},
            },
        )
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        responses = self._sse_exchange_runner(
            server,
            (initialize_payload, initialized_notification, request_payload),
            (str(initialize_payload["id"]), str(request_payload["id"])),
        )
        _extract_mcp_result(responses[str(initialize_payload["id"])])
        return _extract_mcp_result(responses[str(request_payload["id"])])


class MCPBridge:
    """Bridge local skill tools into MCP definitions and guard external tool calls."""

    def __init__(
        self,
        repo_root: Path,
        *,
        config: MCPBridgeConfig | None = None,
        skill_registry: SkillRegistry | None = None,
        governor_service: IRISGovernorService | None = None,
        http_requester: Callable[[MCPServerConfig, dict[str, Any]], Any] | None = None,
        sse_exchange_runner: (
            Callable[
                [MCPServerConfig, tuple[dict[str, Any], ...], tuple[str, ...]],
                dict[str, Any],
            ]
            | None
        ) = None,
        governance_kernel: GovernanceKernel | None = None,
        signing_config: MCPSigningConfig | None = None,
        trust_store: TrustStore | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.config = config or load_mcp_bridge_config(self.repo_root)
        self.skill_registry = skill_registry or SkillRegistry(self.repo_root)
        self.governor_service = governor_service or IRISGovernorService.from_repo_root(
            self.repo_root
        )
        self._server_index = {server.name: server for server in self.config.servers}
        self._governance_kernel = governance_kernel
        # The tool names each server last answered ``tools/list`` with, through this bridge.
        # A call naming a tool outside them (and outside ``governance.tools``) is refused.
        self._listed_tools: dict[str, frozenset[str]] = {}
        # Phase 6 MCP signing (6b.2): verify each server's signature before
        # launch/connect. Absent config → disabled (no-op); shadow → warn-only.
        resolved_signing = signing_config or _load_signing_config(self.repo_root)
        self._transport = MCPTransport(
            self.repo_root,
            signing_config=resolved_signing,
            trust_store=(
                trust_store
                if trust_store is not None
                else _load_trust_store(self.repo_root, resolved_signing)
            ),
            http_requester=http_requester,
            sse_exchange_runner=sse_exchange_runner,
        )

    @classmethod
    def from_repo_root(
        cls,
        repo_root: Path,
        *,
        config_path: Path | None = None,
        governance_kernel: GovernanceKernel | None = None,
    ) -> MCPBridge:
        """Construct an MCP bridge from repository configuration."""
        return cls(
            repo_root,
            config=load_mcp_bridge_config(repo_root, config_path=config_path),
            governance_kernel=governance_kernel,
        )

    def list_enabled_servers(self) -> tuple[MCPServerConfig, ...]:
        """Return configured external MCP servers that are explicitly enabled."""
        return tuple(server for server in self.config.servers if server.enabled)

    def export_local_tools(self, *, agent_name: str | None = None) -> tuple[MCPToolDefinition, ...]:
        """Expose loadable local skill tools as MCP-style tool definitions."""
        if not self.config.auto_export_local_tools:
            return ()

        self.skill_registry.discover()
        packages = self.skill_registry.list_packages(agent_name=agent_name, only_loadable=True)
        definitions: list[MCPToolDefinition] = []
        for package in packages:
            for manifest_tool, tool_class in zip(
                package.manifest.tools,
                package.tool_classes,
                strict=False,
            ):
                definitions.append(
                    MCPToolDefinition(
                        name=manifest_tool.name,
                        description=manifest_tool.description,
                        input_schema=_build_tool_input_schema(tool_class),
                        governor_route=manifest_tool.governor_route,
                        source_kind="skill",
                        source_name=package.manifest.name,
                    )
                )
        if agent_name is None:
            definitions.extend(self._export_persona_tools())
        return tuple(definitions)

    def _export_persona_tools(self) -> tuple[MCPToolDefinition, ...]:
        """Expose a generic persona invoker plus auto-registered per-persona shims."""
        # Personas are a coding-agent feature. The import is lazy so skill-only
        # callers pay no cold-start cost, and guarded because the coding agent is
        # not part of the harness release (OSS plan decision 2): without it there
        # is nothing to invoke, so no persona tool is exported at all.
        try:
            # absent from the public tree: mypy must pass there as well as here
            from iris_code.resource_paths import (  # type: ignore[import-not-found,unused-ignore]
                iter_persona_contract_paths,
            )
            from iris_code.sub_agents import (  # type: ignore[import-not-found,unused-ignore]
                load_persona_profile,
            )
        except ImportError:
            return ()

        definitions: list[MCPToolDefinition] = [_build_generic_persona_tool_definition()]
        for path in iter_persona_contract_paths(self.repo_root):
            try:
                profile = load_persona_profile(path, repo_root=self.repo_root)
            # a single malformed persona file must not crash the whole bridge
            except Exception:  # noqa: BLE001, S112
                continue
            for command in profile.commands:
                try:
                    definitions.append(_build_persona_command_tool_definition(profile, command))
                except Exception:  # noqa: BLE001, S112 - defensive against schema drift
                    continue
        return tuple(definitions)

    def authorize_server_session(
        self,
        server_name: str,
        *,
        approval_granted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> GovernorGuardDecision:
        """Authorize opening a session with one configured MCP server."""
        server = self._require_enabled_server(server_name)
        payload_metadata = {"server_name": server.name, "approval_granted": approval_granted}
        if metadata:
            payload_metadata.update(metadata)
        return self._guard_mcp_action(
            "session_open",
            server_name=server.name,
            approval_granted=approval_granted,
            metadata=payload_metadata,
        )

    def list_external_tools(
        self,
        server_name: str,
        *,
        approval_granted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[MCPToolDefinition, ...]:
        """Discover tool definitions from one configured external MCP server."""
        server = self._require_enabled_server(server_name)
        check = self._transport.check_signature(server)
        if check.action == "deny":
            # Before the governor's guard, so no ``allow`` is recorded for a call that stops.
            raise MCPSignatureError(_signature_refusal(server, check.verdict))
        payload_metadata = {"method": "tools/list"}
        if metadata:
            payload_metadata.update(metadata)
        decision = self._guard_mcp_action(
            "invoke_server",
            server_name=server.name,
            approval_granted=approval_granted,
            metadata=payload_metadata,
        )
        if not decision.allowed:
            raise _refusal(decision, approval_granted=approval_granted)

        result = self._invoke_transport(server, method="tools/list", params={})
        tool_payloads = _coerce_mcp_tools_payload(result)
        definitions = tuple(
            _coerce_external_tool_definition(server, payload) for payload in tool_payloads
        )
        self._listed_tools[server.name] = frozenset(d.name for d in definitions)
        undeclared = undeclared_tool_names(server, (d.name for d in definitions))
        if undeclared:
            default = server.governance.undeclared_tools if server.governance else "destructive"
            logger.warning(
                "MCP server %r has tools with no declared effect: %s; they are treated as %s "
                "(declare them under governance.tools, or set governance.undeclared_tools)",
                server.name,
                ", ".join(sorted(undeclared)),
                default,
            )
        return definitions

    def invoke_external_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approval_granted: bool = False,
        executor: Callable[[MCPServerConfig, str, dict[str, Any]], Any] | None = None,
        persona: str | None = None,
        run_id: str | None = None,
        approved_by: str | None = None,
    ) -> MCPInvocationResult:
        """Guard an outbound MCP tool call, then delegate to the supplied executor.

        When a ``governance_kernel`` was supplied at construction, fires
        ``PreToolUse`` (route ``mcp/{server}/{tool}``) before the legacy
        governor check. ``persona`` and ``run_id`` are forwarded into the
        :class:`~iris_harness.kernel.governance.hooks.types.HookContext` so
        ``MCPAllowlistHook`` can enforce the ``(persona, server, tool)``
        tuple. A ``deny`` or ``require_approval`` outcome raises
        :exc:`PermissionError` with the governance reason (the bridge has no resume
        path, so a call that needs approval does not run); the pre step matches the
        post step. The server is called with the arguments as
        ``PreToolUse`` left them (a ``vault://`` handle resolved by the
        credential broker); everything recorded -- the governor's audit, the
        returned ``arguments`` -- keeps the arguments as the caller wrote them.

        Its result then passes ``PostToolUse``: an external server's output is
        third-party text (``content: external``), so the external-content floor
        marks and scans it (and the opt-in retrieved-content injection guard, when
        enabled). A ``deny`` or ``require_approval`` raises
        :exc:`PermissionError`; a
        ``transform`` is what the caller receives.

        A tool the operator declared in the server's ``governance.tools`` carries that
        effect through both steps (issue #102). A ``destructive`` one is refused before the
        server is reached unless ``approved_by`` names an approved queue row that pinned
        exactly this call, and it leaves a pending write-ahead ledger row before it runs,
        settled after. A tool nobody declared is treated as ``destructive`` (the server's
        ``governance.undeclared_tools``, default ``destructive``; ``read`` opts a server out),
        so it fails closed (issue #180).

        A tool the operator did not declare must be one the server lists: it is refused
        before any hook runs when the server's ``tools/list`` does not name it,
        :exc:`MCPUndeclaredToolError` and one ``deny`` row, and the server is never called.
        A server this bridge has not listed yet is listed first, once per bridge, through
        :meth:`list_external_tools` (its signature check and governor guard, with this call's
        ``approval_granted``); a listing that fails refuses the call the same way
        (:exc:`MCPApprovalRequired` when it was refused for want of approval). A declared
        tool needs no listing. The listing goes through the transport even when ``executor``
        stands in for ``tools/call``.

        A result with ``isError: true`` is the server saying the call failed: it is still
        returned, and its ``post_tool_use`` rows carry ``tool_error``
        (``TOOL_REPORTED_ERROR``), as a call whose tool raised carries its class.
        """
        server = self._require_enabled_server(server_name)
        effect = effective_effect(server, tool_name)
        call_arguments = dict(arguments)
        # One id for this outbound call, minted here and nowhere else: its PRE and POST rows
        # both carry it (#134). Not the caller's to supply.
        call_id = new_ulid()
        # Where this call sits (the governed call it runs inside, if any), for the kernel's
        # rows of the call (#134 stage 2). The HTTP route runs inside none, so it has no parent.
        register_call(call_id)

        # A server whose launch signature fails is refused before any hook runs, so the ledger
        # holds one deny row rather than a row of allow for each hook on the way to the stop.
        check = self._transport.check_signature(server)
        if check.action == "deny":
            self._refuse_signature(
                server,
                tool_name,
                check,
                arguments=arguments,
                persona=persona,
                run_id=run_id,
                call_id=call_id,
                effect=effect,
                approved_by=approved_by,
            )

        def refuse_unknown_tool(
            message: str, error: type[PermissionError] = MCPUndeclaredToolError
        ) -> NoReturn:
            self._refuse_before_hooks(
                server,
                tool_name,
                check,
                plugin="mcp_undeclared_tool",
                message=message,
                error=error,
                arguments=arguments,
                persona=persona,
                run_id=run_id,
                call_id=call_id,
                effect=effect,
                approved_by=approved_by,
            )

        if declared_effect(server, tool_name) is None and server.name not in self._listed_tools:
            # An undeclared name is checked against the server's own list, so a server this
            # bridge has not listed is listed first (#296), through the governed path: its
            # signature check, the governor's guard. Once per bridge; a failure is not kept.
            try:
                self.list_external_tools(server.name, approval_granted=approval_granted)
            except Exception as exc:  # noqa: BLE001 - any failed listing refuses the call
                # A listing refused for want of approval is the call's own missing approval:
                # the caller (the HTTP route's 403) is told that, not that a listing failed.
                refuse_unknown_tool(
                    _listing_failed_refusal(server, tool_name, exc),
                    (
                        MCPApprovalRequired
                        if isinstance(exc, MCPApprovalRequired)
                        else MCPUndeclaredToolError
                    ),
                )
        if not self._is_known_tool(server, tool_name):
            refuse_unknown_tool(_undeclared_tool_refusal(server, tool_name))

        # Governance kernel pre-check (Phase 4 — story 12.gov-4.6).
        if self._governance_kernel is not None:
            call_arguments = self._fire_governance_pre_tool(
                signature=check,
                server_name=server.name,
                tool_name=tool_name,
                arguments=arguments,
                persona=persona,
                run_id=run_id,
                call_id=call_id,
                effect=effect,
                approved_by=approved_by,
            )

        decision = self._guard_mcp_action(
            "call_tool",
            server_name=server.name,
            approval_granted=approval_granted,
            run_id=run_id or f"mcp-{server.name}",
            call_id=call_id,
            metadata={
                "tool_name": tool_name,
                "arguments": arguments,
            },
        )
        if not decision.allowed:
            raise _refusal(decision, approval_granted=approval_granted)

        if executor is not None:
            # The transport path checks the launch signature itself; an executor stands in
            # for the transport, so it gets the same gate before it runs anything (#113 M4c).
            self._enforce_signature(server)
            result = executor(server, tool_name, call_arguments)
        else:
            result = self._invoke_transport(
                server,
                method="tools/call",
                params={"name": tool_name, "arguments": call_arguments},
            )
        if self._governance_kernel is not None:
            result = self._fire_governance_post_tool(
                signature=check,
                server_name=server.name,
                tool_name=tool_name,
                result=result,
                persona=persona,
                run_id=run_id,
                call_id=call_id,
                effect=effect,
                error=TOOL_REPORTED_ERROR if is_error_result(result) else None,
            )
        return MCPInvocationResult(
            server_name=server.name,
            tool_name=tool_name,
            arguments=dict(arguments),
            governor_decision=decision,
            result=result,
        )

    def _refuse_signature(
        self,
        server: MCPServerConfig,
        tool_name: str,
        check: SignatureCheck,
        *,
        arguments: dict[str, Any],
        persona: str | None,
        run_id: str | None,
        call_id: str,
        effect: str | None,
        approved_by: str | None,
    ) -> NoReturn:
        """Refuse a call to a server whose signature fails: one deny row, then the error."""
        self._refuse_before_hooks(
            server,
            tool_name,
            check,
            plugin="mcp_signature",
            message=_signature_refusal(server, check.verdict),
            error=MCPSignatureError,
            arguments=arguments,
            persona=persona,
            run_id=run_id,
            call_id=call_id,
            effect=effect,
            approved_by=approved_by,
        )

    def _is_known_tool(self, server: MCPServerConfig, tool_name: str) -> bool:
        """Whether ``tool_name`` may be called on ``server``.

        The operator's ``governance.tools`` and the server's last ``tools/list`` through this
        bridge are the declared lists; no list is fetched here (``invoke_external_tool``
        lists a server first). With neither naming it, the tool is unknown.
        """
        if declared_effect(server, tool_name) is not None:
            return True
        listed = self._listed_tools.get(server.name)
        return listed is not None and tool_name in listed

    def _refuse_before_hooks(
        self,
        server: MCPServerConfig,
        tool_name: str,
        check: SignatureCheck,
        *,
        plugin: str,
        message: str,
        error: type[PermissionError],
        arguments: dict[str, Any],
        persona: str | None,
        run_id: str | None,
        call_id: str,
        effect: str | None,
        approved_by: str | None,
    ) -> NoReturn:
        """Refuse a call before any hook runs: one ``deny`` row from ``plugin``, then ``error``.

        The ledger then holds one refusal naming why, not a row of ``allow`` for each hook
        the call passed on its way to a stop.
        """
        if self._governance_kernel is not None:
            from iris_harness.kernel.governance.hooks.types import HookPoint

            ctx = self._pre_tool_context(
                signature=check,
                server_name=server.name,
                tool_name=tool_name,
                arguments=arguments,
                persona=persona,
                run_id=run_id,
                call_id=call_id,
                effect=effect,
                approved_by=approved_by,
            )
            self._governance_kernel.record_denial(
                HookPoint.PRE_TOOL_USE,
                plugin,
                message,
                ctx,
                audit_metadata={
                    "mcp_server": server.name,
                    "mcp_tool": tool_name,
                },
            )
        raise error(message)

    def _pre_tool_context(
        self,
        *,
        signature: SignatureCheck,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        persona: str | None,
        run_id: str | None,
        call_id: str,
        effect: str | None = None,
        approved_by: str | None = None,
    ) -> HookContext:
        """The ``PreToolUse`` context of one outbound MCP call (route ``mcp/{server}/{tool}``).

        With no audit key the call is refused here: :exc:`PermissionError` with the no-key
        message.
        """
        from iris_harness.kernel.governance.audit.digest import (
            AuditKeyUnavailable,
            audit_digester,
        )
        from iris_harness.kernel.governance.hooks.tool_payload import (
            CALL_ID,
            TOOL_CALL_ID,
            TOOL_EFFECT,
            pre_tool_payload,
        )
        from iris_harness.kernel.governance.hooks.types import (
            HookContext,
            HookPoint,
        )

        try:
            digester = audit_digester()
        except AuditKeyUnavailable as exc:
            raise PermissionError(f"MCP tool '{server_name}/{tool_name}' refused: {exc}") from exc
        route = f"mcp/{server_name}/{tool_name}"
        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            run_id=run_id or f"mcp-{server_name}",
            agent_type="mcp",
            persona=persona,
            route=route,
            payload=pre_tool_payload(
                route,
                dict(arguments),
                mcp_server=server_name,
                mcp_tool=tool_name,
                tool_plugin=f"mcp:{server_name}",
                **signature.row_fields,
                **digester.args_fields(arguments),
            ),
            metadata={
                CALL_ID: call_id,
                TOOL_CALL_ID: call_id,
                # What the operator declared for this tool, or None: an MCP server declares
                # no effect of its own. A destructive declaration makes the call wait for an
                # approval (``approved_by``) and gives it a write-ahead ledger row.
                TOOL_EFFECT: effect,
                **({"approved_by": approved_by} if approved_by else {}),
            },
        )
        return ctx

    def _fire_governance_pre_tool(
        self,
        *,
        signature: SignatureCheck,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        persona: str | None,
        run_id: str | None,
        call_id: str,
        effect: str | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        """Fire ``PreToolUse`` through the governance kernel for an MCP dispatch.

        Route format: ``mcp/{server_name}/{tool_name}`` (AC-5). The
        ``mcp_server`` and ``mcp_tool`` payload fields allow
        ``MCPAllowlistHook`` to identify the dispatch without parsing the
        route string. A ``deny`` or ``require_approval`` decision raises
        :exc:`PermissionError` (the bridge has no resume path), as the post step does.
        Returns the arguments to call the server with: the final context's
        ``args`` (``tool_payload.args_of``).

        The row carries a keyed ``args_digest`` of the arguments as written
        (``kernel/governance/audit/digest.py``). With no audit key the call is
        refused here, before ``PRE_TOOL_USE`` and before the server is reached:
        :exc:`PermissionError` with the no-key message.
        """
        from iris_harness.kernel.governance.hooks.tool_payload import args_of
        from iris_harness.kernel.governance.hooks.types import HookPoint

        assert self._governance_kernel is not None  # caller guards
        ctx = self._pre_tool_context(
            signature=signature,
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
            persona=persona,
            run_id=run_id,
            call_id=call_id,
            effect=effect,
            approved_by=approved_by,
        )
        decision, final_ctx = self._governance_kernel.fire_sync(HookPoint.PRE_TOOL_USE, ctx)
        if decision.outcome == "deny":
            raise PermissionError(
                f"MCP tool '{server_name}/{tool_name}' blocked by governance: " f"{decision.reason}"
            )
        if decision.outcome == "require_approval":
            # The bridge has no resume path of its own, so a hook asking for approval
            # stops the call here, the same as the POST step withholds the result.
            raise PermissionError(
                f"MCP tool '{server_name}/{tool_name}' needs approval before it runs: "
                f"{decision.reason}"
            )
        final_args = args_of(final_ctx.payload)
        return dict(final_args) if final_args is not None else dict(arguments)

    def _fire_governance_post_tool(
        self,
        *,
        server_name: str,
        tool_name: str,
        result: Any,
        persona: str | None,
        run_id: str | None,
        call_id: str,
        effect: str | None = None,
        signature: SignatureCheck | None = None,
        error: str | None = None,
    ) -> Any:
        """Fire ``PostToolUse`` over an MCP server's result; what the caller may receive.

        An external server's output is third-party text, so it is declared
        ``content: external``; its effect is the one the operator declared for the tool, or
        the server's ``undeclared_tools`` default (an MCP server declares none itself).
        ``error`` is ``TOOL_REPORTED_ERROR`` for an ``isError`` result: the call failed, and
        its rows and its side-effect ledger row say so.
        ``deny`` / ``require_approval`` raise :exc:`PermissionError` (the result is
        withheld); a ``transform`` is returned.
        """
        from iris_harness.kernel.governance.audit.digest import audit_digester
        from iris_harness.kernel.governance.hooks.tool_payload import (
            post_tool_payload,
            result_of,
            tool_post_metadata,
        )
        from iris_harness.kernel.governance.hooks.types import (
            HookContext,
            HookPoint,
        )

        assert self._governance_kernel is not None  # caller guards
        route = f"mcp/{server_name}/{tool_name}"
        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            run_id=run_id or f"mcp-{server_name}",
            agent_type="mcp",
            persona=persona,
            route=route,
            # The key was resolved (and cached) by the PRE step that let this call run.
            payload=post_tool_payload(
                route,
                result,
                mcp_server=server_name,
                mcp_tool=tool_name,
                tool_plugin=f"mcp:{server_name}",
                **(signature.row_fields if signature is not None else {}),
                **audit_digester().result_fields(result),
            ),
            metadata=tool_post_metadata(
                effect=effect, content="external", verify=None, tool_call_id=call_id, error=error
            ),
        )
        decision, final_ctx = self._governance_kernel.fire_sync(HookPoint.POST_TOOL_USE, ctx)
        if decision.outcome in ("deny", "require_approval"):
            raise PermissionError(
                f"MCP tool '{server_name}/{tool_name}' result withheld by governance: "
                f"{decision.reason}"
            )
        return result_of(final_ctx.payload)

    def _require_enabled_server(self, server_name: str) -> MCPServerConfig:
        """Return one enabled server, or raise a clear config error."""
        if not self.config.enabled:
            raise ValueError("MCP bridge is disabled in the effective coding MCP bridge config")

        server = self._server_index.get(server_name)
        if server is None:
            raise ValueError(f"unknown MCP server: {server_name}")
        if not server.enabled:
            raise ValueError(f"MCP server '{server_name}' is disabled")
        return server

    def _guard_mcp_action(
        self,
        action: str,
        *,
        server_name: str,
        approval_granted: bool,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        call_id: str | None = None,
    ) -> GovernorGuardDecision:
        """Evaluate one outbound MCP action through the local governor service.

        Approval comes from the ``approval_granted`` argument alone. ``metadata`` is
        free-form and can arrive from an HTTP body, and the governor treats an
        ``approved`` or ``approval_granted`` key in it as consent, so both keys are
        dropped from it and the argument is written last. Before this, a caller could
        send ``{"approved": true}`` in metadata and approve its own call.
        """
        payload_metadata: dict[str, Any] = {"server_name": server_name}
        if metadata:
            payload_metadata.update(
                {key: value for key, value in metadata.items() if key not in _APPROVAL_KEYS}
            )
        payload_metadata["approval_granted"] = approval_granted
        from iris_harness.foundation.observability.session_log import current_session_id

        return self.governor_service.guard(
            "coding/mcp",
            {
                "action": action,
                "metadata": payload_metadata,
            },
            # What the harness knows of the call (#134 stage 3), beside the decision, never
            # from the request: the run, the minted call id, the chat session.
            identity={"run_id": run_id, "call_id": call_id, "session_id": current_session_id()},
        )

    def _invoke_transport(
        self,
        server: MCPServerConfig,
        *,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        """Invoke one MCP method over the configured transport."""
        return self._transport.invoke(server, method=method, params=params)

    def _verify_signature(self, server: MCPServerConfig) -> SignatureVerdict:
        return self._transport.verify_signature(server)

    def _enforce_signature(self, server: MCPServerConfig) -> None:
        self._transport.enforce_signature(server)


def _build_jsonrpc_request(
    *,
    request_id: str,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build one JSON-RPC request payload."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def _default_http_requester(server: MCPServerConfig, payload: dict[str, Any]) -> Any:
    """Execute one JSON-RPC request against an HTTP MCP endpoint."""
    if server.url is None:
        raise ValueError(f"MCP server '{server.name}' is missing an HTTP URL")

    log_egress(
        destination=urlparse(server.url).netloc,
        method="POST",
        kind="mcp",
        purpose="mcp.http",
    )
    response = httpx.post(server.url, json=payload, timeout=server.timeout_seconds)
    response.raise_for_status()
    return response.json()


def _default_sse_exchange_runner(
    server: MCPServerConfig,
    outgoing_messages: tuple[dict[str, Any], ...],
    expected_response_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Run one MCP session over SSE and collect the expected JSON-RPC responses."""
    if server.url is None:
        raise ValueError(f"MCP server '{server.name}' is missing an SSE URL")

    try:
        with httpx.Client(timeout=server.timeout_seconds) as client:
            log_egress(
                destination=urlparse(server.url).netloc,
                method="GET",
                kind="mcp",
                purpose="mcp.sse",
            )
            with client.stream(
                "GET",
                server.url,
                headers={"Accept": "text/event-stream"},
            ) as response:
                response.raise_for_status()
                event_stream = _iter_sse_events(response.iter_lines())
                message_endpoint = _resolve_sse_message_endpoint(server.url, event_stream)

                for message in outgoing_messages:
                    log_egress(
                        destination=urlparse(message_endpoint).netloc,
                        method="POST",
                        kind="mcp",
                        purpose="mcp.sse",
                    )
                    post_response = client.post(message_endpoint, json=message)
                    post_response.raise_for_status()

                responses: dict[str, Any] = {}
                expected_ids = set(expected_response_ids)
                for _event_name, data in event_stream:
                    if not data.strip():
                        continue
                    payload = json.loads(data)
                    if not isinstance(payload, dict):
                        continue
                    payload_id = payload.get("id")
                    if payload_id is None:
                        continue
                    normalized_id = str(payload_id)
                    if normalized_id in expected_ids:
                        responses[normalized_id] = payload
                    if len(responses) == len(expected_ids):
                        return responses
    except httpx.TimeoutException as exc:
        raise ValueError(
            f"timed out waiting for MCP SSE responses from server '{server.name}'"
        ) from exc

    raise ValueError(f"incomplete MCP SSE exchange from server '{server.name}'")


def _resolve_sse_message_endpoint(
    base_url: str,
    event_stream: Any,
) -> str:
    """Read the initial SSE endpoint event and resolve the message POST URL."""
    for event_name, data in event_stream:
        if event_name == "endpoint":
            endpoint = data.strip()
            if not endpoint:
                break
            return cast(str, urljoin(base_url, endpoint))
    raise ValueError("MCP SSE stream did not publish an endpoint event")


def _iter_sse_events(lines: Any) -> Any:
    """Yield parsed SSE events as `(event_name, data)` tuples."""
    event_name = "message"
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
        stripped = line.rstrip("\r\n")
        if not stripped:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
            continue
        if stripped.startswith(":"):
            continue
        if stripped.startswith("event:"):
            event_name = stripped.partition(":")[2].strip() or "message"
            continue
        if stripped.startswith("data:"):
            data_lines.append(stripped.partition(":")[2].lstrip())

    if data_lines:
        yield event_name, "\n".join(data_lines)


class MCPTransportError(ValueError):
    """A stdio session failed on the wire: no answer in time, the server exited, or it sent
    something the exchange cannot use. A ``ValueError`` so existing callers keep catching it."""


#: The newest this many bytes of a server's stderr are kept for an error message.
_STDERR_TAIL_BYTES = 16_384
#: A server that sends this much without a newline is not speaking the protocol.
_MAX_STDOUT_BUFFER = 32 * 1024 * 1024
_READ_CHUNK = 65_536


class _StderrTail:
    """Drains a child's stderr on a thread so a chatty server never blocks on a full pipe,
    keeping only the newest bytes for the error message of a failed call."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, name="mcp-stderr", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        try:
            while chunk := self._stream.read(4096):
                with self._lock:
                    self._tail += chunk
                    del self._tail[:-_STDERR_TAIL_BYTES]
        except (OSError, ValueError):  # closed under us when the call ends
            return

    def text(self, wait: float = 0.2) -> str:
        """The stderr seen so far; waits briefly for a process that just exited to flush."""
        self._thread.join(timeout=wait)
        with self._lock:
            return bytes(self._tail).decode("utf-8", errors="replace").strip()


def _describe_failure(server: MCPServerConfig, what: str, stderr: str, code: int | None) -> str:
    message = f"MCP stdio server '{server.name}' {what}"
    if code is not None:
        message = f"{message} (exit code {code})"
    return f"{message}: {stderr[-2000:]}" if stderr else message


def _run_stdio_exchange(
    *,
    repo_root: Path,
    server: MCPServerConfig,
    outgoing_messages: tuple[dict[str, Any], ...],
    expected_response_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Run a one-shot stdio MCP session and collect the expected JSON-RPC responses.

    The whole session is bounded by ``server.timeout_seconds``: every wait, including the
    wait for the rest of a half-written line, runs against one deadline. Bytes are read into
    the exchange's own buffer, so nothing sits in a reader the deadline cannot see. A line that
    is not a JSON-RPC message (a banner on stdout) is skipped; stderr is drained as the server
    writes it. A failure says why: timeout, exit code and the end of stderr, or the id that
    arrived when another was expected."""
    if server.command is None:
        raise ValueError(f"MCP server '{server.name}' is missing a stdio command")

    environment = child_environment(server)
    process = subprocess.Popen(
        [server.command, *server.args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(repo_root),
        env=environment,
        bufsize=0,
    )
    stderr = _StderrTail(process.stderr) if process.stderr is not None else None

    try:
        if process.stdin is None or process.stdout is None:
            raise ValueError(f"unable to open stdio pipes for MCP server '{server.name}'")

        for message in outgoing_messages:
            _write_all(process.stdin, _encode_stdio_message(message))

        deadline = time.monotonic() + server.timeout_seconds
        expected = set(expected_response_ids)
        responses: dict[str, Any] = {}
        buffer = bytearray()
        stdout_fd = process.stdout.fileno()
        while not expected <= responses.keys():
            payload = _next_stdio_message(buffer)
            if payload is not None:
                _accept_stdio_message(server, payload, expected, responses, process.stdin)
                continue

            remaining = deadline - time.monotonic()
            ready = select.select([stdout_fd], [], [], remaining)[0] if remaining > 0 else []
            if not ready:
                raise MCPTransportError(
                    _describe_failure(
                        server,
                        f"gave no answer within {server.timeout_seconds:g}s",
                        stderr.text(0) if stderr else "",
                        None,
                    )
                )
            chunk = os.read(stdout_fd, _READ_CHUNK)
            if not chunk:
                code = _exit_code(process)
                raise MCPTransportError(
                    _describe_failure(
                        server,
                        "closed its output before answering",
                        stderr.text() if stderr else "",
                        code,
                    )
                )
            buffer += chunk
            if len(buffer) > _MAX_STDOUT_BUFFER:
                raise MCPTransportError(
                    f"MCP stdio server '{server.name}' sent more than "
                    f"{_MAX_STDOUT_BUFFER // (1024 * 1024)} MiB without a complete message"
                )

        return responses
    finally:
        _terminate_process(process)


def _exit_code(process: subprocess.Popen[bytes]) -> int | None:
    try:
        return process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return None


def _accept_stdio_message(
    server: MCPServerConfig,
    payload: dict[str, Any],
    expected: set[str],
    responses: dict[str, Any],
    stdin: Any,
) -> None:
    """File one message from the server: an expected response, a request to answer, or noise."""
    message_id = payload.get("id")
    if "method" in payload:
        # A server-initiated request or notification. A request is answered so the server is
        # not left waiting; nothing here acts on it.
        if message_id is not None:
            if payload["method"] == "ping":
                reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id, "result": {}}
            else:
                reply = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": "method not supported by this client"},
                }
            try:
                _write_all(stdin, _encode_stdio_message(reply))
            except (OSError, ValueError):
                pass  # the server went away; the read side reports it
        return
    if message_id is None:
        return
    if str(message_id) not in expected:
        raise MCPTransportError(
            f"MCP stdio server '{server.name}' answered request id {message_id!r}, "
            f"which this session did not send (expected {', '.join(sorted(expected))})"
        )
    responses[str(message_id)] = payload


def _next_stdio_message(buffer: bytearray) -> dict[str, Any] | None:
    """Take one complete JSON-RPC message off the front of ``buffer``, or return ``None``.

    ``None`` means more bytes are needed. Newline-delimited JSON is the format; a legacy
    ``Content-Length`` framed message is read too. Any other complete line (a startup banner,
    a log line) is dropped, and so is a line that looks like JSON and is not."""
    while True:
        newline = buffer.find(b"\n")
        if newline < 0:
            return None
        line = bytes(buffer[:newline]).decode("utf-8", errors="replace").strip()

        if line.lower().startswith("content-length:"):
            framed = _take_framed_message(buffer)
            if framed is None:
                return None
            if framed:
                return framed
            continue

        del buffer[: newline + 1]
        if not line.startswith("{"):
            if line:
                logger.debug("mcp stdio: skipped a %d byte non-JSON line", len(line))
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            logger.debug("mcp stdio: skipped a %d byte line that is not JSON", len(line))
            continue
        if isinstance(payload, dict):
            return payload
        logger.debug("mcp stdio: skipped a JSON line that is not an object")


def _take_framed_message(buffer: bytearray) -> dict[str, Any] | None:
    """Take a ``Content-Length`` framed message, ``None`` if incomplete, ``{}`` if unusable."""
    for separator in (b"\r\n\r\n", b"\n\n"):
        end = buffer.find(separator)
        if end >= 0:
            break
    else:
        return None
    header_text = bytes(buffer[:end]).decode("ascii", errors="ignore")
    body_start = end + len(separator)
    try:
        length = _parse_content_length(header_text.splitlines())
    except ValueError:
        del buffer[:body_start]
        return {}
    if len(buffer) < body_start + length:
        return None
    body = bytes(buffer[body_start : body_start + length])
    del buffer[: body_start + length]
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_all(stream: Any, data: bytes) -> None:
    """Write every byte of ``data``: an unbuffered pipe may take only part of it."""
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if written is None:
            raise ValueError("MCP stdio server did not accept the request")
        view = view[written:]


def _encode_stdio_message(payload: dict[str, Any]) -> bytes:
    """Encode one MCP stdio message using the current SDK newline-delimited format."""
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _parse_content_length(headers: list[str]) -> int:
    """Extract Content-Length from MCP stdio headers."""
    for header in headers:
        name, separator, value = header.partition(":")
        if separator and name.lower() == "content-length":
            return int(value.strip())
    raise ValueError("MCP stdio message is missing Content-Length")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a spawned stdio MCP process safely."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _extract_mcp_result(payload: Any) -> Any:
    """Extract the result payload from a JSON-RPC response."""
    if isinstance(payload, dict):
        error = payload.get("error")
        if error is not None:
            raise ValueError(_format_mcp_error(error))
        if "result" in payload:
            return payload["result"]
    return payload


def _format_mcp_error(error: Any) -> str:
    """Format a JSON-RPC error into a readable message."""
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message", "Unknown MCP error")
        return f"MCP error {code}: {message}" if code is not None else str(message)
    return str(error)


def _coerce_mcp_tools_payload(payload: Any) -> list[dict[str, Any]]:
    """Validate that an MCP tools/list result contains a tool array."""
    if not isinstance(payload, dict):
        raise ValueError("MCP tools/list response must be a JSON object")
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError("MCP tools/list response must include a tools array")
    if not all(isinstance(tool, dict) for tool in tools):
        raise ValueError("MCP tools/list entries must be JSON objects")
    return list(tools)


def _coerce_external_tool_definition(
    server: MCPServerConfig,
    payload: dict[str, Any],
) -> MCPToolDefinition:
    """Convert an MCP tool payload into the shared tool definition model."""
    input_schema = payload.get("inputSchema") or payload.get("input_schema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    return MCPToolDefinition(
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "Tool exposed by external MCP server")),
        input_schema=input_schema,
        governor_route="coding/mcp",
        source_kind="mcp_server",
        source_name=server.name,
    )


def _build_tool_input_schema(tool_class: type[BaseTool]) -> dict[str, Any]:
    """Convert a LangChain tool args schema into an MCP-style JSON schema."""
    schema: dict[str, Any] | None = None
    try:
        tool_instance = tool_class()
        input_schema = tool_instance.get_input_schema()
        # LangChain 1.x types this as a pydantic v2 or v1 model; only v2 has
        # model_json_schema.
        runtime_schema = (
            input_schema.model_json_schema()
            if issubclass(input_schema, BaseModel)
            else input_schema.schema()
        )
        if isinstance(runtime_schema, dict):
            return runtime_schema
    except Exception:  # noqa: BLE001 — no runtime schema falls back to the declared one
        schema = None

    model_field = getattr(tool_class, "model_fields", {}).get("args_schema")
    args_schema = getattr(model_field, "default", None)
    if isinstance(args_schema, type) and issubclass(args_schema, BaseModel):
        declared_schema = args_schema.model_json_schema()
        if isinstance(declared_schema, dict):
            schema = declared_schema
    if schema is not None:
        return schema
    return {"type": "object", "properties": {}, "additionalProperties": True}


_PERSONA_BATCH_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "for_each_story": {"type": "boolean", "default": False},
        "scope": {
            "type": "string",
            "enum": ["platform", "project"],
            "default": "platform",
        },
        "project_slug": {"type": ["string", "null"], "default": None},
        "for_each_section": {"type": ["string", "null"], "default": None},
        "for_each_glob": {"type": ["string", "null"], "default": None},
        "items": {
            "type": "array",
            "items": {"type": "object"},
            "default": [],
        },
    },
    "additionalProperties": False,
}


def _build_generic_persona_tool_definition() -> MCPToolDefinition:
    """Build the generic ``iris_invoke_persona`` tool definition."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "persona": {
                "type": ["string", "null"],
                "description": "Persona to invoke (sm, analyst, architect, developer, tester).",
            },
            "command": {
                "type": ["string", "null"],
                "description": "Command id owned by the persona.",
            },
            "batch": _PERSONA_BATCH_SPEC_SCHEMA,
            "dry_run": {"type": "boolean", "default": False},
            "allow_writes": {"type": "boolean", "default": False},
            "list_available": {
                "type": "boolean",
                "default": False,
                "description": "Return the persona → command mapping instead of invoking.",
            },
        },
        "additionalProperties": False,
    }
    return MCPToolDefinition(
        name="iris_invoke_persona",
        description=(
            "Invoke one persona + command ad-hoc over a batch of items "
            "(story/section/glob/explicit). Bypasses the bootstrap pipeline."
        ),
        input_schema=schema,
        governor_route="coding/tool/persona-invoke",
        source_kind="persona",
        source_name="generic",
    )


def _build_persona_command_tool_definition(
    profile: Any,
    command: Any,
) -> MCPToolDefinition:
    """Build a per-persona-per-command shim tool definition."""
    safe_persona = profile.name.replace("-", "_")
    safe_command = command.id.replace("-", "_")
    tool_name = f"iris_{safe_persona}_{safe_command}"
    description_lines = profile.description.splitlines()
    head = description_lines[0].strip() if description_lines else profile.name
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "batch": _PERSONA_BATCH_SPEC_SCHEMA,
            "dry_run": {"type": "boolean", "default": False},
            "allow_writes": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }
    return MCPToolDefinition(
        name=tool_name,
        description=f"{head} — {command.id}",
        input_schema=schema,
        governor_route=f"coding/tool/persona-{safe_persona}-{safe_command}",
        source_kind="persona",
        source_name=profile.name,
    )
