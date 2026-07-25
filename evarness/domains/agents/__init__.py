"""The agents domain: AI agent harnesses on the Evarness kernel.

Importing this package registers everything the domain contributes:

- **node types** — importing :mod:`.nodes` fills the kernel's ``NODE_TYPES``
  registry (30 types: routing, governance, memory, rag, tools, loops, judges)
- **the provider factory** — how ``graph.params.provider`` specs resolve
  (simulation-only in this release; real specs refuse with remediation)
- **a determinism inspector** — a real-mode tool or a tier that can resolve to
  a real provider breaks the reproducibility claim; the kernel asks, the
  domain answers
- **the contract library** — the packaged invariant definitions this domain's
  vocabulary makes meaningful (``no-model-call-after-block``, …)
"""

from __future__ import annotations

from pathlib import Path

from evarness.core.registry import (
    register_context_extension,
    register_contract_source,
    register_determinism_inspector,
    register_subject_pinner,
    set_environment_loader,
    set_provider_factory,
)
from evarness.domains.agents import nodes as nodes  # registers all node types
from evarness.domains.agents.providers import make_provider
from evarness.domains.agents.sim import load_fixture
from evarness.domains.agents.state import AgentsRunState


@register_determinism_inspector
def _uses_real_execution(graph) -> bool:
    """True when anything in the graph would leave simulation: a real-mode
    tool (pipeline or loop) or a tier_router that can resolve to a real
    provider."""
    if any(
        (n.type == "tool" and n.config.get("mode") == "real")
        or (n.type == "loop_controller" and n.config.get("tool_mode") == "real")
        for n in graph.nodes
    ):
        return True
    from evarness.domains.agents.tiers import tier_router_uses_real_provider

    return any(
        n.type == "tier_router" and tier_router_uses_real_provider(n.config) for n in graph.nodes
    )


@register_subject_pinner
def _pin_tool_manifests(graph) -> dict:
    """Manifest hash for every tool the graph references — the proof names the
    exact tool contracts in force, not just the tool ids. A tool with no
    manifest is recorded as null (visible gap, not a silent omission)."""
    import hashlib
    import json

    from evarness.domains.agents.catalog import tool_spec

    ids: set[str] = set()
    for n in graph.nodes:
        if n.type == "tool" and n.config.get("tool"):
            ids.add(n.config["tool"])
        if n.type == "loop_controller":
            ids.update(t for t in (n.config.get("tools") or []) if t)
    out = {}
    for tid in sorted(ids):
        spec = tool_spec(tid)
        out[tid] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    spec.model_dump(by_alias=True),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    default=str,
                ).encode("ascii")
            ).hexdigest()
            if spec
            else None
        )
    return {"tool_manifests": out}


set_provider_factory(make_provider)
register_context_extension("agents", AgentsRunState)
set_environment_loader(load_fixture)
register_contract_source(Path(__file__).parent / "contracts.yaml")
