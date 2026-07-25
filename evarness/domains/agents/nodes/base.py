"""Shared node plumbing: the NodeSpec contract, text/document helpers, the
classification/egress gates every model and tool boundary consults, and the
palette presentation table."""

from __future__ import annotations

from __future__ import annotations
from typing import Any, ClassVar
from pydantic import BaseModel
from evarness.domains.agents.classification import egress_allowed
from evarness.domains.agents.state import agents_state
from evarness.domains.agents.prompts import DEFAULTS
from evarness.core.errors import NodeBlocked
from evarness.core.registry import NODE_TYPES

REGISTRY = NODE_TYPES

DEFAULT_LLM_SYSTEM = DEFAULTS["llm_system"]

DEFAULT_AGENT_SYSTEM = DEFAULTS["agent_system"]

NODE_PRESENTATION: dict[str, tuple[str, str]] = {
    "input": ("📥", "Input"),
    "output": ("📤", "Output"),
    "prompt_template": ("📝", "Prompt Template"),
    "llm": ("🧠", "LLM"),
    "output_parser": ("✂️", "Output Parser"),
    "loop_controller": ("🔁", "ReAct Loop"),
    "intent_router": ("🚦", "Intent Router"),
    "interceptor": ("🛂", "Interceptor"),
    "data_classifier": ("🏷️", "Data Classifier"),
    "tier_router": ("🎚️", "Tier Router"),
    "approval_gate": ("✋", "Approval Gate"),
    "judge_chain": ("⚖️", "Judge Chain"),
    "llm_guard": ("🛡️", "LLM Guard"),
    "llm_judge": ("⚖️", "LLM Judge"),
    "redaction_rules": ("🧼", "Redaction"),
    "policy_gate": ("📜", "Policy Gate"),
    "rate_budget_limiter": ("💰", "Budget Limiter"),
    "tool": ("🔧", "Tool"),
    "retriever": ("🔎", "Retriever"),
    "conversation_buffer": ("💬", "Conversation Buffer"),
    "working_memory": ("🗒️", "Working Memory"),
    "episodic_memory": ("📚", "Episodic Memory"),
    "semantic_memory": ("👤", "User Profile"),
    "procedural_memory": ("📋", "Standing Instructions"),
    "summary_consolidator": ("🗜️", "Summary Consolidator"),
    "context_assembler": ("🧩", "Context Assembler"),
    "trace_probe": ("👁️", "Trace Probe"),
    "metrics_emitter": ("📈", "Metrics"),
    "cost_latency_monitor": ("⏱️", "Cost & Latency"),
    "audit_log_sink": ("🧾", "Audit Sink"),
}

GROUP_ORDER = ["core", "governance", "tools", "rag", "context", "memory", "observability"]

GROUP_TITLES = {
    "core": "Core",
    "governance": "Governance",
    "tools": "Tools",
    "rag": "Retrieval (RAG)",
    "context": "Context",
    "memory": "Memory",
    "observability": "Observability",
}


def presentation(type_name: str) -> dict:
    icon, label = NODE_PRESENTATION.get(type_name, ("⬡", type_name.replace("_", " ").title()))
    return {"icon": icon, "label": label}


def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or value.get("prompt") or value)
    return str(value)


_EGRESS_MODE_RANK = {"off": 0, "warn": 1, "enforce": 2}


def _provider_locality(ctx) -> str:
    """Where a model call actually goes: sim | local | cloud. When a tier_router
    has armed a tier, its DECLARED locality wins (the egress law reasons about
    the tier the run represents, not the sim twin). Otherwise the provider's own
    locality; anything undeclared fails CLOSED as cloud."""
    tier_loc = agents_state(ctx).tier_locality
    if tier_loc:
        return tier_loc
    return getattr(ctx.provider, "locality", "cloud")


def _tool_destination(spec, mode: str) -> str:
    """Where a tool call actually goes. Sim twins stay in the fixture world; a
    real tool is 'network' when its manifest declares outbound network (or has
    no manifest at all — fail closed), else 'local'."""
    if mode != "real":
        return "sim"
    if spec is None or getattr(spec.safety, "network", "outbound") == "outbound":
        return "network"
    return "local"


def _egress_gate(ctx, node_id: str, destination: str) -> None:
    """egress law, checked at the model/tool boundaries. Inactive until a
    data_classifier node arms the run (egress_mode stays 'off' — existing graphs
    are untouched). warn traces the verdict; enforce blocks BEFORE the boundary
    is crossed, so a denied run never calls the model or tool."""
    st = agents_state(ctx)
    mode = st.egress_mode
    if mode == "off":
        return
    classification = st.classification
    allowed = egress_allowed(classification, destination)
    ctx.emit(
        "egress_checked",
        node_id,
        destination=destination,
        classification=classification,
        verdict="allow" if allowed else "deny",
        mode=mode,
    )
    if not allowed:
        ctx.emit(
            "egress_denied",
            node_id,
            destination=destination,
            classification=classification,
            action="block" if mode == "enforce" else "warn",
        )
        if mode == "enforce":
            raise NodeBlocked(
                node_id,
                f"egress denied: {classification} content "
                f"may not reach {destination} (see classification.yaml)",
            )


def _doc_previews(result: list, cap: int = 12) -> list[dict]:
    """Compact id+title preview of tool results for the tool_result event — WHICH
    sources the model saw must be auditable from the trace alone (found live: a
    digest could not be verified against a past run because real search results
    shift between calls and the trace only recorded a count)."""
    return [
        {"id": str(d.get("id", ""))[:200], "subject": str(d.get("subject", ""))[:100]}
        for d in result[:cap]
    ]


class NodeSpec:
    type_name: ClassVar[str]
    group: ClassVar[str] = "core"
    doc: ClassVar[str] = ""
    inputs: ClassVar[dict[str, str]] = {"in": "any"}
    outputs: ClassVar[dict[str, str]] = {"out": "any"}

    class Config(BaseModel):
        pass

    @classmethod
    def run(cls, node_id: str, inputs: dict, cfg: BaseModel, ctx) -> Any:  # pragma: no cover
        raise NotImplementedError


_GOVERNANCE_EVENTS = {
    "intent_routed",
    "interceptor_applied",
    "policy_violation",
    "redaction_applied",
    "guard_evaluated",
    "guard_triggered",
    "judge_scored",
    "judge_flagged",
    "policy_checked",
    "budget_checked",
    "budget_breached",
    # classification + egress, tier routing, approvals
    "content_classified",
    "egress_checked",
    "egress_denied",
    "tier_selected",
    "tier_downshifted",
    "tier_egress_warning",
    "approval_requested",
    "approval_granted",
    "approval_rejected",
    "approval_skipped",
    "run_paused",
    # judge chain
    "judge_signal",
    "judge_repaired",
    "judge_exhausted",
    "judge_degraded",
    "chain_halted",
    "judge_chain_finished",
}
