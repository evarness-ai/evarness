"""Mount a ``trust: mcp`` plugin: its manifest names a server, its tools go to the loop.

The server runs out of process and is not started at mount; each call starts a one-shot
stdio session through :class:`~iris_harness.tools.mcp_bridge.MCPTransport`, which checks the
launch signature first and starts the child with an allow-listed environment. The loader
registers each tool the manifest declares through ``PluginAPI.register_tool``, exactly as it
does for an in-process plugin, so the call joins the governed tool pool: one PRE/POST pair,
the manifest's effect, content and approval rules, the caller stamped. The transport adds no
governance of its own, so a call is governed once. A result the server marks
``isError: true`` is handed on as text and recorded as a failed call.

Only a declared tool is registered, so a tool the server offers but the manifest does not
declare is never reachable, and an undeclared name is refused by the registry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iris_harness.agent.tool_runner import FailedResult
from iris_harness.tools.mcp_bridge import MCPServerConfig, MCPTransport

from .manifest import PluginManifest, ToolDeclaration

if TYPE_CHECKING:
    from .api import PluginAPI


def server_config(manifest: PluginManifest) -> MCPServerConfig:
    """The bridge's server entry for the manifest's ``server:`` block."""
    block = manifest.server
    if block is None:
        raise ValueError(f"plugin {manifest.name!r} declares no server")
    return MCPServerConfig(
        name=manifest.name,
        enabled=True,
        transport="stdio",
        command=block.command,
        args=block.args,
        env=block.env,
        inherit_env=block.inherit_env,
        pinned_env=block.pinned_env,
        timeout_seconds=block.timeout_seconds,
        max_result_bytes=block.max_result_bytes,
        signature=block.signature,
        signed_by=block.signed_by,
    )


def observation(result: Any) -> str:
    """A ``tools/call`` result as the observation text; ``error: ...`` when the tool failed.

    A failed result (``isError: true``) comes back as a :class:`FailedResult`, so the
    governed runner counts the call as failed and its ``post_tool_use`` rows say so
    (``tool_error``), while the model still reads the server's text.
    """
    if not isinstance(result, dict):
        return result if isinstance(result, str) else json.dumps(result)
    content = result.get("content")
    if isinstance(content, list):
        text = "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        text = json.dumps(result.get("structuredContent", result))
    return FailedResult(f"error: {text}") if result.get("isError") else text


def _describe(name: str, decl: ToolDeclaration) -> str:
    return decl.description.strip() or f"{name} (served by an MCP server)"


def mcp_setup(
    manifest: PluginManifest, *, directory: Path, transport: MCPTransport
) -> Callable[[PluginAPI], None]:
    """The ``setup`` an MCP plugin does not ship: register each declared tool."""
    server = server_config(manifest)

    def bind(tool: str) -> Callable[[dict[str, Any]], str]:
        def call(args: dict[str, Any]) -> str:
            result = transport.invoke(
                server,
                method="tools/call",
                params={"name": tool, "arguments": dict(args)},
                working_dir=directory,
            )
            return observation(result)

        return call

    def setup(api: PluginAPI) -> None:
        for name, decl in manifest.tools.items():
            api.register_tool(name, _describe(name, decl), bind(name))

    setup.__qualname__ = f"mcp_setup[{manifest.name}]"
    return setup


__all__ = ["mcp_setup", "observation", "server_config"]
