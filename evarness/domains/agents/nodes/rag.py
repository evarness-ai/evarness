"""Retrieval and context assembly."""

from __future__ import annotations

from __future__ import annotations
from typing import ClassVar, Literal
from pydantic import BaseModel
from evarness.domains.agents.prompts import DEFAULTS
from evarness.domains.agents.sim import (
    SimVectorStore,
    approx_tokens,
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
class RetrieverNode(NodeSpec):
    type_name = "retriever"
    group = "rag"
    doc = (
        "Scores and filters candidate documents (SimVectorStore in v1; "
        "sqlite-vec and adapters behind the same interface later)."
    )
    inputs: ClassVar[dict] = {"in": "documents"}
    outputs: ClassVar[dict] = {"out": "documents"}

    class Config(BaseModel):
        top_k: int = 5
        min_score: float = 0.35

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        docs = inputs.get("in") or []
        if not isinstance(docs, list):
            docs = [docs]
        chunks = SimVectorStore.query(ctx.user_input, docs, cfg.top_k, cfg.min_score)
        ctx.emit(
            "retrieval_performed",
            node_id,
            top_k=cfg.top_k,
            candidates=len(docs),
            returned=len(chunks),
            scores=[d["_score"] for d in chunks],
        )
        return chunks


@register
class ContextAssemblerNode(NodeSpec):
    type_name = "context_assembler"
    group = "context"
    doc = (
        "Token-budget-aware prompt assembly. Its context_snapshot event powers "
        "the Context Window Inspector."
    )
    inputs: ClassVar[dict] = {"question": "text", "documents": "documents", "memory": "messages"}
    outputs: ClassVar[dict] = {"out": "context"}

    class Config(BaseModel):
        system: str = DEFAULTS["assembler_system"]
        overflow: Literal[
            "truncate_retrieved", "summarize_retrieved", "drop_lowest_score", "error"
        ] = "truncate_retrieved"
        reserve_for_response: int = 512

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        question = as_text(inputs.get("question", ctx.user_input))
        documents = inputs.get("documents") or []
        memory = inputs.get("memory") or []
        if not isinstance(documents, list):
            documents = [documents]
        if not isinstance(memory, list):
            memory = [memory]

        budget = ctx.params.context_budget_tokens - cfg.reserve_for_response

        def doc_tokens(docs):
            return sum(
                approx_tokens(f"{d.get('subject','')} {d.get('snippet', d.get('text',''))}")
                for d in docs
            )

        def seg(kind, tok):
            return {"kind": kind, "tokens": tok}

        segments = [
            seg("system", approx_tokens(cfg.system)),
            seg("memory", sum(approx_tokens(t.get("text", "")) for t in memory)),
            seg("retrieved", doc_tokens(documents)),
            seg("user", approx_tokens(question)),
        ]
        total = sum(s["tokens"] for s in segments)
        truncated = 0
        while total > budget and documents and cfg.overflow == "truncate_retrieved":
            documents = documents[:-1]
            truncated += 1
            segments[2] = seg("retrieved", doc_tokens(documents))
            total = sum(s["tokens"] for s in segments)
        ctx.emit(
            "context_snapshot",
            node_id,
            segments=segments,
            total_tokens=total,
            budget=budget,
            truncated_docs=truncated,
        )
        if total > budget:
            # even with all retrieved docs dropped the fixed segments exceed budget —
            # never silent: emit budget_breached so the trace shows the overflow
            ctx.emit(
                "budget_breached",
                node_id,
                total_tokens=total,
                budget=budget,
                over_by=total - budget,
            )
        return {
            "system": cfg.system,
            "question": question,
            "documents": documents,
            "memory": memory,
            "segments": segments,
        }
