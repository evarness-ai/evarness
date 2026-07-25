"""Conversation and long-term memory: buffers, working/episodic/semantic/
procedural stores, consolidation."""

from __future__ import annotations

from __future__ import annotations
from typing import ClassVar, Literal
from pydantic import BaseModel
from evarness.domains.agents.sim import (
    SimVectorStore,
    approx_tokens,
    redact_pii,
)
from evarness.core.registry import register_node as register

from evarness.domains.agents.nodes.base import (  # noqa: F401
    DEFAULT_AGENT_SYSTEM,
    DEFAULT_LLM_SYSTEM,
    NODE_PRESENTATION,
    REGISTRY,
    NodeSpec,
    _doc_previews,
    _egress_gate,
    _provider_locality,
    _tool_destination,
    as_text,
    presentation,
)


@register
class ConversationBufferNode(NodeSpec):
    type_name = "conversation_buffer"
    group = "memory"
    doc = "Short-term memory: recent dialogue turns under a token cap. Scope: session."
    inputs: ClassVar[dict] = {}
    outputs: ClassVar[dict] = {"out": "messages"}

    class Config(BaseModel):
        window: int = 12
        token_cap: int = 2000

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        turns = ctx.fixture.memory[-cfg.window :]
        total = 0
        kept: list[dict] = []
        for t in reversed(turns):
            tok = approx_tokens(t.get("text", ""))
            if total + tok > cfg.token_cap:
                break
            kept.insert(0, t)
            total += tok
        ctx.emit("memory_read", node_id, turns=len(kept), tokens=total)
        return kept


@register
class WorkingMemoryNode(NodeSpec):
    type_name = "working_memory"
    group = "memory"
    doc = (
        "Run-scoped scratchpad the harness reads/writes mid-execution — plans, "
        "intermediate results, flags. Cleared when the run ends."
    )
    inputs: ClassVar[dict] = {"in": "any"}
    outputs: ClassVar[dict] = {"out": "messages"}

    class Config(BaseModel):
        key: str = "notes"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        value = inputs.get("in")
        if value is not None:
            ctx.scratch.setdefault(cfg.key, []).append(as_text(value))
            ctx.emit(
                "memory_write",
                node_id,
                store="working",
                key=cfg.key,
                entries=len(ctx.scratch[cfg.key]),
            )
        entries = ctx.scratch.get(cfg.key, [])
        ctx.emit("memory_read", node_id, store="working", key=cfg.key, turns=len(entries))
        return [{"role": "working", "text": t} for t in entries]


@register
class EpisodicMemoryNode(NodeSpec):
    type_name = "episodic_memory"
    group = "memory"
    doc = (
        "Long-term record of past interactions (fixture `episodic` section). "
        "Retrieval scores recency + relevance deterministically. Poisonable — "
        "attacks that write here resurface later; see red-team fixtures."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "messages"}

    class Config(BaseModel):
        top_k: int = 3
        min_salience: float = 0.0
        write_policy: Literal["salience", "off"] = "salience"
        write_salience: float = 0.6

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        query = as_text(inputs.get("in", ctx.user_input))
        entries = [
            e for e in ctx.fixture.episodic if float(e.get("salience", 1.0)) >= cfg.min_salience
        ]
        n = len(entries) or 1
        scored = sorted(
            (
                {
                    **e,
                    "_score": round(
                        SimVectorStore.score(query, {"text": e.get("text", "")})
                        + (i + 1) / n * 0.1,
                        4,
                    ),
                }  # recency bonus, newest last
                for i, e in enumerate(entries)
            ),
            key=lambda e: (-e["_score"], str(e.get("ts", ""))),
        )[: cfg.top_k]
        ctx.emit(
            "memory_read",
            node_id,
            store="episodic",
            candidates=len(entries),
            returned=len(scored),
            scores=[e["_score"] for e in scored],
        )
        if cfg.write_policy != "off":
            salience = round(min(1.0, approx_tokens(query) / 50), 4)
            ctx.emit(
                "memory_write",
                node_id,
                store="episodic",
                accepted=salience >= cfg.write_salience,
                salience=salience,
                persisted=False,
            )  # sim: the write path is traced, not persisted
        return [{"role": "episodic", "text": e.get("text", ""), "ts": e.get("ts")} for e in scored]


@register
class SemanticMemoryNode(NodeSpec):
    type_name = "semantic_memory"
    group = "memory"
    doc = (
        "Distilled facts about the user (fixture `facts` section) — the profile "
        "store. The write path is governed: redact before persistence, never "
        "memorize raw PII."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "messages"}

    class Config(BaseModel):
        conflict: Literal["latest_wins", "first_wins"] = "latest_wins"
        redact_before_write: bool = True

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        # a fixture fact is a string, or {fact, key?} — entries sharing a key conflict
        facts: dict[str, str] = {}
        conflicts = 0
        for f in ctx.fixture.facts:
            text = f if isinstance(f, str) else str(f.get("fact", ""))
            key = (text if isinstance(f, str) else str(f.get("key") or text)).lower()
            if key in facts:
                conflicts += 1
                if cfg.conflict == "first_wins":
                    continue
            facts[key] = text
        ctx.emit(
            "memory_read",
            node_id,
            store="semantic",
            facts=len(facts),
            **({"conflicts": conflicts} if conflicts else {}),
        )
        value = inputs.get("in")
        if value is not None:
            raw = as_text(value)
            written, redactions = redact_pii(raw) if cfg.redact_before_write else (raw, 0)
            ctx.emit(
                "memory_write",
                node_id,
                store="semantic",
                redactions=redactions,
                preview=written[:80],
                persisted=False,
            )
        return [{"role": "profile", "text": t} for t in facts.values()]


@register
class ProceduralMemoryNode(NodeSpec):
    type_name = "procedural_memory"
    group = "memory"
    doc = (
        "Standing instructions & learned behaviors (fixture `instructions` section). "
        "Highest-risk memory type — whoever writes here reprograms the agent, so "
        "unapproved entries stay pending unless approval is set to auto."
    )
    inputs: ClassVar[dict] = {}
    outputs: ClassVar[dict] = {"out": "messages"}

    class Config(BaseModel):
        write_approval: Literal["human", "auto"] = "human"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        approved, pending = [], []
        for item in ctx.fixture.instructions:
            text = item if isinstance(item, str) else str(item.get("text", ""))
            ok = True if isinstance(item, str) else bool(item.get("approved", False))
            (approved if ok else pending).append(text)
        if pending and cfg.write_approval == "auto":
            ctx.emit(
                "memory_write",
                node_id,
                store="procedural",
                auto_approved=len(pending),
                risk="auto-approved instructions reprogram the agent unreviewed",
            )
            approved += pending
            pending = []
        for text in pending:
            ctx.emit("memory_write_pending", node_id, store="procedural", preview=text[:80])
        ctx.emit(
            "memory_read",
            node_id,
            store="procedural",
            instructions=len(approved),
            pending=len(pending),
        )
        return [{"role": "instruction", "text": t} for t in approved]


@register
class SummaryConsolidatorNode(NodeSpec):
    type_name = "summary_consolidator"
    group = "memory"
    doc = (
        "The short-term -> long-term bridge: compresses messages into a summary "
        "(deterministic extractive: first sentence of each). Lossy by design — "
        "the compression ratio in the trace shows what consolidation forgets."
    )
    inputs: ClassVar[dict] = {"in": "messages"}
    outputs: ClassVar[dict] = {"out": "messages"}

    class Config(BaseModel):
        max_tokens: int = 60
        target: Literal["episodic", "semantic"] = "episodic"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        messages = inputs.get("in") or []
        if not isinstance(messages, list):
            messages = [messages]
        in_tokens = sum(
            approx_tokens(m.get("text", "") if isinstance(m, dict) else m) for m in messages
        )
        firsts = [
            (m.get("text", "") if isinstance(m, dict) else str(m)).split(".")[0].strip()
            for m in messages
        ]
        summary = ". ".join(f for f in firsts if f)[: cfg.max_tokens * 4]
        out_tokens = approx_tokens(summary)
        ctx.emit(
            "memory_consolidated",
            node_id,
            target=cfg.target,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            ratio=round(in_tokens / out_tokens, 2) if out_tokens else 0.0,
            lossy=True,
        )
        return [{"role": "summary", "text": summary}]
