"""Event-sourced graph executor.

Every step emits a trace event; the trace is simultaneously the live animation feed,
the replay/scrub source, the run-comparison input, and the audit log.
Determinism contract: (graph, fixture, seed) -> identical event stream in CANONICAL
form — `ts` is wall clock, so raw bytes never reproduce; the published normalization
rules and digest live in trace.py (canonical_trace / trace_digest).
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .invariants import check_invariants
from .nodes import REGISTRY, NodeBlocked, RunPaused
from .providers import make_provider
from .schema import GraphModel, GraphParams, lint, topological_order
from .sim import Fixture


class Emitter:
    def __init__(self, on_event: Callable[[dict], None] | None = None):
        self.events: list[dict] = []
        self.on_event = on_event
        self.seq = 0

    def emit(self, type_: str, node_id: str | None = None, **payload) -> dict:
        ev = {
            "seq": self.seq,
            "ts": round(time.time(), 4),
            "node_id": node_id,
            "type": type_,
            "payload": payload,
        }
        self.seq += 1
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)
        return ev


@dataclass
class RunContext:
    graph: GraphModel
    params: GraphParams
    fixture: Fixture
    emitter: Emitter
    rng: random.Random
    provider: Any
    user_input: str
    totals: dict = field(default_factory=lambda: {"tokens": 0, "cost_usd": 0.0})
    scratch: dict = field(default_factory=dict)  # run-scoped working memory
    output: Any = None
    # data classification: monotonic high-water mark for the run + the egress
    # regime. Both stay inert ("public"/"off") until a data_classifier node arms
    # them — graphs without one are completely unaffected.
    classification: str = "public"
    egress_mode: str = "off"
    # tier routing: a tier_router node arms these; the llm/loop boundaries
    # then run on ctx.provider (swapped) and the egress gate reads tier_locality.
    # Inert (None) until a tier_router node exists.
    tier: str | None = None
    tier_locality: str | None = None
    # human-in-the-loop: {gate_node_id: "approve"|"reject"} decisions supplied
    # on resume. Empty on a first run — an approval_gate with no decision pauses.
    approvals: dict = field(default_factory=dict)

    def emit(self, type_: str, node_id: str | None = None, **payload) -> dict:
        return self.emitter.emit(type_, node_id, **payload)


@dataclass
class RunResult:
    id: str
    status: str  # completed | blocked | failed | paused
    output: Any
    events: list[dict]
    totals: dict
    reason: str | None = None
    # set only when status == "paused": the gate awaiting a human decision
    # ({node_id, prompt, preview}). Resume by calling execute() again with
    # approvals[node_id] set to "approve" or "reject".
    pending: dict | None = None
    # verdicts for graph.params.invariants: {passed, failed, results}.
    # None when the graph declares none or the run paused (partial evidence —
    # resume replays the full run and checks then). Verdicts live OUTSIDE the
    # event stream: checking never changes the canonical digest.
    invariants: dict | None = None


class GraphValidationError(ValueError):
    def __init__(self, issues: list[dict]):
        super().__init__("; ".join(i["message"] for i in issues))
        self.issues = issues


def execute(
    graph: GraphModel,
    fixture: Fixture,
    user_input: str | None = None,
    on_event: Callable[[dict], None] | None = None,
    approvals: dict | None = None,
    invariant_defs: dict | None = None,
) -> RunResult:
    issues = lint(graph, REGISTRY)
    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        raise GraphValidationError(errors)

    run_id = uuid.uuid4().hex[:12]
    emitter = Emitter(on_event)
    ctx = RunContext(
        graph=graph,
        params=graph.params,
        fixture=fixture,
        emitter=emitter,
        rng=random.Random(graph.params.seed),
        provider=make_provider(graph.params.provider, fixture),
        user_input=user_input if user_input is not None else fixture.user_input,
        approvals=dict(approvals or {}),
    )

    # deterministic only if BOTH the provider and every tool are simulated —
    # a single real-mode tool (pipeline node or loop) breaks reproducibility
    # just like a real model
    has_real_tool = any(
        (n.type == "tool" and n.config.get("mode") == "real")
        or (n.type == "loop_controller" and n.config.get("tool_mode") == "real")
        for n in graph.nodes
    )
    # a tier_router that could resolve to a real provider breaks reproducibility
    # too — resolve its reachable tiers against tiers.yaml
    from .tiers import tier_router_uses_real_provider

    has_real_tier = any(
        n.type == "tier_router" and tier_router_uses_real_provider(n.config) for n in graph.nodes
    )
    deterministic = ctx.provider.deterministic and not has_real_tool and not has_real_tier

    emitter.emit(
        "run_started",
        fixture=fixture.scenario,
        seed=graph.params.seed,
        provider=graph.params.provider,
        deterministic=deterministic,
        input=ctx.user_input[:200],
    )

    values: dict[tuple[str, str], Any] = {}
    status, reason, pending = "completed", None, None
    try:
        for node_id in topological_order(graph):
            node = graph.node(node_id)
            assert node is not None  # topological_order only yields existing ids
            spec = REGISTRY[node.type]
            cfg = spec.Config.model_validate(node.config)

            # gather inputs from incoming edges, keyed by target port
            inputs: dict[str, Any] = {}
            for e in sorted(graph.edges, key=lambda x: (x.to_port, x.from_)):
                if e.to == node_id and (e.from_, e.from_port) in values:
                    if e.to_port in inputs:
                        # same-port fan-in merges FLAT: two document lists into one
                        # port must yield one list of documents, not a nested list
                        prev, val = inputs[e.to_port], values[(e.from_, e.from_port)]
                        inputs[e.to_port] = (prev if isinstance(prev, list) else [prev]) + (
                            val if isinstance(val, list) else [val]
                        )
                    else:
                        inputs[e.to_port] = values[(e.from_, e.from_port)]

            emitter.emit("node_started", node_id, type=node.type)
            out = spec.run(node_id, inputs, cfg, ctx)
            values[(node_id, "out")] = out
            emitter.emit("node_finished", node_id, type=node.type)
    except RunPaused as paused:
        # a human decision is needed — pause, don't fail. Resume by replaying
        # execute() with approvals[node_id] set. Deterministic runs replay
        # byte-identically up to here; the trace records the pause as first-class.
        emitter.emit(
            "run_paused",
            paused.node_id,
            prompt=paused.prompt,
            preview=paused.preview,
            total_tokens=ctx.totals["tokens"],
            events=emitter.seq + 1,
        )
        status, reason = "paused", None
        pending = {"node_id": paused.node_id, "prompt": paused.prompt, "preview": paused.preview}
    except NodeBlocked as blocked:
        emitter.emit("policy_violation", blocked.node_id, reason=blocked.reason)
        emitter.emit(
            "run_failed",
            reason=blocked.reason,
            total_tokens=ctx.totals["tokens"],
            events=emitter.seq + 1,
        )
        status, reason = "blocked", blocked.reason
    except Exception as exc:  # engine fault — still traced, nothing goes unnoticed
        emitter.emit("engine_error", reason=f"{type(exc).__name__}: {exc}")
        emitter.emit("run_failed", reason=str(exc))
        status, reason = "failed", str(exc)
    else:
        emitter.emit(
            "run_finished",
            total_tokens=ctx.totals["tokens"],
            events=emitter.seq + 1,
            cost_usd=round(ctx.totals.get("cost_usd", 0.0), 6),
            deterministic=deterministic,
        )

    invariants = None
    if graph.params.invariants and status != "paused":
        invariants = check_invariants(graph.params.invariants, emitter.events, extra=invariant_defs)

    return RunResult(
        id=run_id,
        status=status,
        output=ctx.output,
        events=emitter.events,
        totals=ctx.totals,
        reason=reason,
        pending=pending,
        invariants=invariants,
    )
