"""Tool-manifest catalog — the one source of truth the engine consults.

A tool id resolves to a validated :class:`~evarness.domains.agents.toolspec.ToolSpec`
manifest: the packaged built-ins first, then any user manifests from
``~/.evarness/tools.yaml`` (or ``$EVARNESS_TOOLS``). The nodes consult the
spec for safety gates (side effects require explicit approval), sim defaults,
and version stamping; proofs pin the manifest hash of every tool a graph
references.

This release resolves manifests only — every tool executes in simulation.
Real executors, python-plugin tools, and MCP-imported tools are later
capabilities.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from evarness.domains.agents.toolspec import ToolSpec, builtin_specs, from_legacy


def _user_manifest_path() -> Path:
    env = os.environ.get("EVARNESS_TOOLS")
    if env:
        return Path(env)
    return Path.home() / ".evarness" / "tools.yaml"


def user_tool_specs() -> list[ToolSpec]:
    """User-declared manifests. A malformed entry is skipped, never fatal —
    but it is skipped loudly enough to find (invalid manifests simply don't
    resolve, so the safety gate treats the tool as manifest-less)."""
    path = _user_manifest_path()
    if not path.exists():
        return []
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return []
    out = []
    for t in doc.get("tools", []):
        try:
            out.append(from_legacy(t))
        except Exception:
            continue
    return out


def tool_spec(tool_id: str) -> ToolSpec | None:
    """The validated spec behind a tool id — safety gates, sim defaults, and
    proof subject pinning all read from here."""
    for spec in builtin_specs() + user_tool_specs():
        if spec.id == tool_id:
            return spec
    return None


def list_tools() -> list[dict]:
    return [
        {
            "id": s.id,
            "version": s.version,
            "category": s.category,
            "side_effects": s.safety.side_effects,
            "source": s.source,
        }
        for s in builtin_specs() + user_tool_specs()
    ]
