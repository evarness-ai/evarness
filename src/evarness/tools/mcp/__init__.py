"""Evarness as an MCP *server* -- the protocol half of ``evarness mcp serve``.

Evarness consumes external MCPs via ``evarness.tools.mcp_bridge``; this package is the
other direction: a zero-dependency stdlib JSON-RPC stdio server. It speaks the protocol
only. How a served tool runs -- through the governed tool runner, as ``mcp:<client>`` --
is ``evarness.runtime.mcp_serve``'s.
"""

from .server import (
    EvarnessMCPServer,
    McpReply,
    McpTool,
    SkillTool,
    load_skill_tools,
    serve_stdio,
)

__all__ = [
    "EvarnessMCPServer",
    "McpReply",
    "McpTool",
    "SkillTool",
    "load_skill_tools",
    "serve_stdio",
]
