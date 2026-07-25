"""The agents domain's per-run state — its slot in RunContext.ext (E10).

The kernel constructs nothing domain-shaped: this dataclass is registered as
a context-extension factory at domain import, the executor builds one fresh
instance per run, and every agents node reaches it through
:func:`agents_state`. Other domains get their own slot the same way; none of
them can collide with, or even see, this one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentsRunState:
    """Governance state armed by agents-domain nodes during a run.

    All fields stay inert at their defaults until a node arms them — graphs
    without a data_classifier or tier_router are completely unaffected.
    """

    # data classification: monotonic high-water mark for the run + the egress
    # regime, armed by a data_classifier node
    classification: str = "public"
    egress_mode: str = "off"
    # tier routing: armed by a tier_router node; the llm/loop boundaries then
    # run on the swapped ctx.provider and the egress gate reads tier_locality
    tier: str | None = None
    tier_locality: str | None = None


def agents_state(ctx) -> AgentsRunState:
    """The agents domain's state slot on a RunContext."""
    try:
        return ctx.ext["agents"]
    except (KeyError, AttributeError) as exc:
        raise RuntimeError(
            'agents_state() requires ctx.ext["agents"] to be present — '
            "make sure the agents domain is imported and RunContext was built "
            "via execute() or with ext=build_context_extensions()."
        ) from exc
