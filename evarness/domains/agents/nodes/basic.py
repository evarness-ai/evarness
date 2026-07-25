"""The minimal pipeline: input, prompt templating, the model call, output
parsing, output."""

from __future__ import annotations

from __future__ import annotations
import json
import re
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from evarness.domains.agents.prompts import DEFAULTS, PROMPT_TEMPLATES
from evarness.domains.agents.sim import (
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
class InputNode(NodeSpec):
    type_name = "input"
    group = "core"
    doc = "Entry point. Defines the input contract of the harness."
    inputs: ClassVar[dict] = {}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        schema_name: str = Field(default="user_question", alias="schema")
        model_config = {"populate_by_name": True}

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        ctx.emit(
            "input_received", node_id, text=ctx.user_input, tokens=approx_tokens(ctx.user_input)
        )
        return ctx.user_input


@register
class OutputNode(NodeSpec):
    type_name = "output"
    group = "core"
    doc = "Exit point. Policy lint requires a validator interceptor upstream of it."
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {}

    class Config(BaseModel):
        schema_name: str = Field(default="answer", alias="schema")
        model_config = {"populate_by_name": True}

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        value = as_text(inputs.get("in", ""))
        ctx.output = value
        return value


@register
class PromptTemplateNode(NodeSpec):
    type_name = "prompt_template"
    group = "core"
    doc = (
        "Renders the final prompt text from the assembled context via an editable "
        "layout — placeholders {system} {memory} {documents} {question}. `template` "
        "is a preset name (answer_with_context, plain_qa) or a custom layout string."
    )
    inputs: ClassVar[dict] = {"in": "context"}
    outputs: ClassVar[dict] = {"out": "prompt"}

    class Config(BaseModel):
        template: str = "answer_with_context"

    @classmethod
    def render(cls, template: str, assembled: dict) -> str:
        """Fill the layout from the assembled segments (shared with codegen)."""
        memory = assembled.get("memory") or []
        docs = assembled.get("documents") or []
        values = {
            "system": assembled.get("system", DEFAULTS["fallback_system"]),
            "question": assembled.get("question", ""),
            "memory": (
                "\n".join(
                    ["Conversation so far:"]
                    + [f"- {t.get('role','user')}: {t.get('text','')}" for t in memory]
                )
                if memory
                else ""
            ),
            "documents": (
                "\n".join(
                    ["Context documents:"]
                    + [
                        f"[{i+1}] {d.get('subject','')}: {d.get('snippet', d.get('text',''))}"
                        for i, d in enumerate(docs)
                    ]
                )
                if docs
                else ""
            ),
        }
        lines: list[str] = []
        for line in template.splitlines():
            bare = line.strip()
            if bare in ("{system}", "{memory}", "{documents}"):
                if values[bare[1:-1]]:  # section line: dropped when empty
                    lines.append(values[bare[1:-1]])
                continue
            for key, val in values.items():  # inline placeholders (str.replace —
                line = line.replace("{" + key + "}", val)  # literal braces stay safe)
            lines.append(line)
        return "\n".join(lines)

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        assembled = inputs.get("in") or {}
        if not isinstance(assembled, dict):
            assembled = {"question": as_text(assembled)}
        layout = PROMPT_TEMPLATES.get(cfg.template, cfg.template)
        prompt = cls.render(layout, assembled)
        name = cfg.template if cfg.template in PROMPT_TEMPLATES else "custom"
        ctx.emit("prompt_assembled", node_id, template=name, tokens=approx_tokens(prompt))
        return {"prompt": prompt, "template": name}


@register
class LLMNode(NodeSpec):
    type_name = "llm"
    group = "core"
    doc = "The probabilistic component. Everything around it is the harness."
    inputs: ClassVar[dict] = {"in": "prompt"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        system: str = DEFAULT_LLM_SYSTEM  # standing instruction (editable); never empty
        temperature: float = 0.2
        top_p: float = 1.0
        max_tokens: int = 512

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        payload = inputs.get("in") or {}
        # dict payloads: a prompt_template dict carries "prompt"; anything else
        # (e.g. an assembler wired straight in) degrades to its text content
        prompt = (
            (payload.get("prompt") or as_text(payload))
            if isinstance(payload, dict)
            else as_text(payload)
        )
        if cfg.system:
            prompt = f"{cfg.system}\n\n{prompt}"
        _egress_gate(ctx, node_id, _provider_locality(ctx))  # before the model boundary
        ptok = approx_tokens(prompt)
        ctx.emit(
            "llm_request",
            node_id,
            provider=ctx.params.provider,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            prompt_tokens=ptok,
        )
        try:
            completion = ctx.provider.complete(
                prompt, temperature=cfg.temperature, max_tokens=cfg.max_tokens
            )
        except Exception as exc:  # provider failure: trace it, then fail the run honestly
            ctx.emit("llm_error", node_id, provider=ctx.params.provider, error=str(exc))
            raise
        rtok = completion.output_tokens or approx_tokens(completion.text)
        ctx.totals["tokens"] += (completion.input_tokens or ptok) + rtok
        ctx.totals["cost_usd"] = round(ctx.totals.get("cost_usd", 0.0) + completion.cost_usd, 6)
        ctx.emit(
            "llm_response",
            node_id,
            tokens=rtok,
            cost_usd=completion.cost_usd,
            preview=completion.text[:120],
        )
        return completion.text


@register
class OutputParserNode(NodeSpec):
    type_name = "output_parser"
    group = "core"
    doc = (
        "Normalizes model output into the declared output type. format=json "
        "extracts the first JSON value and re-serializes it canonically; "
        "unparseable output passes through as text, flagged in the trace."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        format: Literal["text", "json"] = "text"

    @staticmethod
    def _extract_json(text: str):
        """First parseable JSON value in the text (code fences stripped), or None."""
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M)
        decoder = json.JSONDecoder()
        for m in re.finditer(r"[\[{]", cleaned):
            try:
                value, _ = decoder.raw_decode(cleaned[m.start() :])
                return value
            except ValueError:
                continue
        return None

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", "")).strip()
        if cfg.format == "json":
            value = cls._extract_json(text)
            ctx.emit("output_parsed", node_id, format="json", ok=value is not None)
            if value is not None:
                return json.dumps(value, ensure_ascii=False)
        return text  # format=text is a silent strip — traces stay unchanged
