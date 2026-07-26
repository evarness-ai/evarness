"""Agent-domain graph lint rules.

The kernel's ``lint()`` checks structure — ids, edges, cycles, configs.
Rules that need domain vocabulary register here instead, through
``core.registry.register_lint_rule``: the kernel never learns what an
``llm`` is (E18; the E10 boundary applied to linting).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


from evarness.core.registry import register_lint_rule


@register_lint_rule
def unguarded_llm(graph: Any, registry: Mapping[str, Any]) -> list[dict]:
    """Policy: every llm should have a validator interceptor downstream."""
    issues: list[dict] = []
    types = {n.id: n.type for n in graph.nodes}
    for lid in (n.id for n in graph.nodes if n.type == "llm"):
        downstream, frontier = set(), [lid]
        while frontier:
            cur = frontier.pop()
            for e in graph.edges:
                if e.from_ == cur and e.to not in downstream:
                    downstream.add(e.to)
                    frontier.append(e.to)
        if not any(types.get(d) == "interceptor" for d in downstream):
            issues.append(
                {
                    "level": "warning",
                    "code": "policy_unguarded_llm",
                    "message": f"Policy: LLM node {lid} reaches output without a validator interceptor",
                }
            )
    return issues
