"""Trace probes, metrics, cost/latency monitoring, audit sinks."""

from __future__ import annotations

from __future__ import annotations
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from evarness.domains.agents.sim import (
    approx_tokens,
)
from evarness.core.errors import NodeBlocked
from evarness.core.registry import register_node as register

from evarness.domains.agents.nodes.base import (
    _GOVERNANCE_EVENTS,
    NodeSpec,
)


@register
class TraceProbeNode(NodeSpec):
    type_name = "trace_probe"
    group = "observability"
    doc = (
        "Taps an edge and snapshots what flows through it. Zero effect on "
        "execution — pure observability."
    )
    inputs: ClassVar[dict] = {"in": "any"}
    outputs: ClassVar[dict] = {"out": "any"}

    class Config(BaseModel):
        capture: Literal["payload", "tokens_only"] = "payload"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        value = inputs.get("in")
        payload = {"value_type": type(value).__name__, "tokens": approx_tokens(value)}
        if cfg.capture == "payload":
            payload["preview"] = str(value)[:140]
        ctx.emit("probe_snapshot", node_id, **payload)
        return value


@register
class MetricsEmitterNode(NodeSpec):
    type_name = "metrics_emitter"
    group = "observability"
    doc = "Turns the trace so far into named metrics that Experiments can aggregate " "and compare."
    inputs: ClassVar[dict] = {"in": "any"}
    outputs: ClassVar[dict] = {"out": "any"}

    class Config(BaseModel):
        metrics: list[str] = Field(default_factory=lambda: ["tokens", "events", "tool_calls"])

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        counts = {
            "tokens": ctx.totals["tokens"],
            "events": len(ctx.emitter.events),
            "tool_calls": sum(1 for e in ctx.emitter.events if e["type"] == "tool_called"),
            "llm_calls": sum(1 for e in ctx.emitter.events if e["type"] == "llm_request"),
        }
        ctx.emit("metrics_emitted", node_id, **{m: counts[m] for m in cfg.metrics if m in counts})
        return inputs.get("in")


@register
class CostLatencyMonitorNode(NodeSpec):
    type_name = "cost_latency_monitor"
    group = "observability"
    doc = (
        "Watches cumulative cost/latency at this point in the run. Sim latency is "
        "a deterministic estimate (events x ms/event); real timing lands with real "
        "providers. Emits budget_breached when over."
    )
    inputs: ClassVar[dict] = {"in": "any"}
    outputs: ClassVar[dict] = {"out": "any"}

    class Config(BaseModel):
        cost_per_1k_tokens_usd: float = 0.003
        cost_budget_usd: float = 0.05
        sim_ms_per_event: int = 20
        latency_budget_ms: int = 2000
        action: Literal["alert", "abort"] = "alert"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        cost = round(ctx.totals["tokens"] / 1000 * cfg.cost_per_1k_tokens_usd, 6)
        latency = len(ctx.emitter.events) * cfg.sim_ms_per_event
        over = cost > cfg.cost_budget_usd or latency > cfg.latency_budget_ms
        ctx.emit("cost_latency", node_id, cost_usd=cost, sim_latency_ms=latency, within=not over)
        if over:
            ctx.emit(
                "budget_breached", node_id, cost_usd=cost, sim_latency_ms=latency, action=cfg.action
            )
            if cfg.action == "abort":
                raise NodeBlocked(
                    node_id, f"cost/latency budget breached " f"(${cost}, {latency}ms simulated)"
                )
        return inputs.get("in")


@register
class AuditLogSinkNode(NodeSpec):
    type_name = "audit_log_sink"
    group = "observability"
    doc = (
        "Compliance export point. The event trace IS the audit log; this node "
        "marks where (and how much of) it ships."
    )
    inputs: ClassVar[dict] = {"in": "any"}
    outputs: ClassVar[dict] = {"out": "any"}

    class Config(BaseModel):
        include: Literal["governance", "full"] = "governance"
        format: str = "jsonl"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        gov = sum(1 for e in ctx.emitter.events if e["type"] in _GOVERNANCE_EVENTS)
        shipped = gov if cfg.include == "governance" else len(ctx.emitter.events)
        ctx.emit(
            "audit_flushed",
            node_id,
            include=cfg.include,
            format=cfg.format,
            events_shipped=shipped,
            governance_events=gov,
        )
        return inputs.get("in")
