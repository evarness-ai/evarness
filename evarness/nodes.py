"""Node registry — the core node set plus conversation_buffer.

Each node type declares: Config (pydantic → auto-generates the inspector form),
typed ports, docs, and run(). Adding a node = adding a class here + @register.
(v1 drift: single module instead of one-folder-per-node — see DECISIONS.md.)
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from .classification import classify, egress_allowed, max_class
from .grounding import check_grounding, requested_count
from .prompts import DEFAULTS, NUDGES, PROMPT_TEMPLATES, PROTOCOLS
from .sim import REDACTION_RULES, SimVectorStore, ToolError, approx_tokens, redact, redact_pii

REGISTRY: dict[str, type["NodeSpec"]] = {}

# Editable default instructions so the model is never left ungrounded (an empty
# system prompt is how small models confabulate). The strings live in
# prompts.yaml; users tweak per node in the inspector or override the
# defaults in ~/.evarness/prompts.yaml.
DEFAULT_LLM_SYSTEM = DEFAULTS["llm_system"]
DEFAULT_AGENT_SYSTEM = DEFAULTS["agent_system"]


def register(cls):
    REGISTRY[cls.type_name] = cls
    return cls


# Palette presentation — icon + friendly label per node type. The Builder palette
# renders straight from this (via the registry endpoint), so a new node shows up
# with its icon/label automatically; unlisted types fall back to a hex + Title Case.
# Group display order + titles let the palette group nodes sensibly.
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


class NodeBlocked(RuntimeError):
    """Raised by governance nodes when execution must stop (deterministic block)."""

    def __init__(self, node_id: str, reason: str):
        super().__init__(reason)
        self.node_id = node_id
        self.reason = reason


class RunPaused(RuntimeError):
    """Raised by an approval_gate when a human decision is needed and none is
     present yet. Unlike NodeBlocked (a terminal deny), this PAUSES the run: the
     engine records it as `paused`, and a later execute() with the decision in
     `approvals` replays deterministically up to this gate and continues past it
    . The pause is a first-class outcome, not a failure."""

    def __init__(self, node_id: str, prompt: str, preview: str):
        super().__init__(prompt)
        self.node_id = node_id
        self.prompt = prompt
        self.preview = preview


def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or value.get("prompt") or value)
    return str(value)


def _tool_spec_gate(tool: str, cfg, node_id: str):
    """T0 safety gate: a tool whose spec says it has side effects (write /
    destructive => approval by default) must be explicitly approved on the node
    (`approve_side_effects`) or the harness refuses the call — safety is
    manifest data, enforced here, not a convention. Returns the spec (or None
    for tools with no manifest, e.g. fixture-only lesson tools)."""
    from .catalog import tool_spec

    spec = tool_spec(tool)
    if spec and spec.safety.approval_required() and not getattr(cfg, "approve_side_effects", False):
        raise NodeBlocked(
            node_id,
            f"tool '{tool}' has {spec.safety.side_effects} "
            "side effects and requires approval — enable "
            "'approve_side_effects' on the node to opt in",
        )
    return spec


def _run_real_tool_contained(tool: str, query: str, cfg, spec, node_id, ctx, sandbox_value: str):
    """Real tool execution has not graduated into this release — every tool
    here runs in simulation. This is a loud governance block, not an import
    error: a graph that asks for `mode: real` gets an actionable refusal, and
    the trace records the block. The `sandbox`/`egress` knobs on the node
    config take effect when real execution arrives (with OS-enforced
    containment); until then they configure nothing and the run refuses before
    reading them."""
    raise NodeBlocked(
        node_id,
        f"tool '{tool}' is set to mode: real, and real tool execution is not "
        "part of this release — every tool here runs in simulation "
        "(fixture-scripted). Set mode: sim, or wait for the release that "
        "introduces sandboxed real execution.",
    )


def _sim_default_result(spec, tool: str, query: str, ctx) -> list[dict]:
    """Sim fallback (T0): when the fixture does not script this tool at all,
    the manifest's default sim rules answer — a fresh install works in sim mode
    immediately. Fixture scripts always win (the lesson author's intent)."""
    if spec is None or not spec.sim or tool in ctx.fixture.tools:
        return []
    from .toolspec import sim_result

    return sim_result(spec, query)


_EGRESS_MODE_RANK = {"off": 0, "warn": 1, "enforce": 2}


def _provider_locality(ctx) -> str:
    """Where a model call actually goes: sim | local | cloud. When a tier_router
    has armed a tier, its DECLARED locality wins (the egress law reasons about
    the tier the run represents, not the sim twin). Otherwise the provider's own
    locality; anything undeclared fails CLOSED as cloud."""
    tier_loc = getattr(ctx, "tier_locality", None)
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
    mode = getattr(ctx, "egress_mode", "off")
    if mode == "off":
        return
    classification = getattr(ctx, "classification", "public")
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


def _warn_free_search(node_id: str, tool: str, mode: str, provider: str, ctx) -> None:
    """A best-effort web.search backend (e.g. free DuckDuckGo) is rate-limited with no
    ranking guarantees — surface that in the trace so the user sees the limitation they
    accepted, not a silent quality drop. Extensible: any provider that sets
    best_effort=True warns, not just a hard-coded name."""
    if mode != "real" or tool != "web.search":
        return
    try:
        from .tools.search import get_search_provider  # type: ignore[import-not-found]
    except ImportError:  # real search backends arrive with real execution
        return

    p = get_search_provider(provider)
    if p is not None and p.best_effort:
        ctx.emit(
            "tool_warning",
            node_id,
            tool=tool,
            provider=p.name,
            warning=f"'{p.name}' is a best-effort search backend: rate-limited, "
            "no ranking guarantees — prefer a configured provider (e.g. "
            "searxng) for reliable results",
        )


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


# ---------------------------------------------------------------- core


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


# ---------------------------------------------------------------- governance (deterministic)


@register
class IntentRouterNode(NodeSpec):
    type_name = "intent_router"
    group = "governance"
    doc = (
        "Deterministic, non-LLM routing. Unmatched intents never reach the model — "
        "governance starts here."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        rules: list[dict] = Field(default_factory=lambda: [{"route": "general", "keywords": ["?"]}])
        fallback: Literal["reject", "human_review", "generic_llm"] = "reject"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        low = text.lower()
        for rule in cfg.rules:
            if any(str(k).lower() in low for k in rule.get("keywords", [])):
                ctx.emit("intent_routed", node_id, route=rule.get("route"), matched=True)
                return text
        ctx.emit("intent_routed", node_id, route=cfg.fallback, matched=False)
        if cfg.fallback == "reject":
            raise NodeBlocked(
                node_id, "no intent rule matched; deterministic router rejected the input"
            )
        return text


@register
class InterceptorNode(NodeSpec):
    type_name = "interceptor"
    group = "governance"
    doc = (
        "Pure-Python middleware at a node boundary: pii_scrub, policy_check, "
        "schema_validate, audit_tap. Can pass, transform, or block."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        chain: list[str] = Field(default_factory=lambda: ["pii_scrub", "policy_check"])

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        for rule in cfg.chain:
            if rule == "pii_scrub":
                text, n = redact_pii(text)
                ctx.emit("interceptor_applied", node_id, rule=rule, redactions=n)
                if n:
                    ctx.emit("redaction_applied", node_id, count=n)
            elif rule == "policy_check":
                hit = next((t for t in ctx.fixture.blocklist if t in text.lower()), None)
                verdict = "block" if hit else "pass"
                ctx.emit(
                    "interceptor_applied",
                    node_id,
                    rule=rule,
                    verdict=verdict,
                    **({"term": hit} if hit else {}),
                )
                if hit:
                    raise NodeBlocked(node_id, f"policy_check blocked content containing '{hit}'")
            elif rule == "schema_validate":
                ok = bool(text.strip())
                ctx.emit(
                    "interceptor_applied", node_id, rule=rule, verdict="pass" if ok else "block"
                )
                if not ok:
                    raise NodeBlocked(node_id, "schema_validate: empty output")
            elif rule == "audit_tap":
                ctx.emit("interceptor_applied", node_id, rule=rule, logged=True)
            else:
                ctx.emit("interceptor_applied", node_id, rule=rule, verdict="unknown_rule")
        return text


@register
class DataClassifierNode(NodeSpec):
    type_name = "data_classifier"
    group = "governance"
    doc = (
        "Tags flowing content public/internal/personal/secret (registered "
        "classifiers — bring your own; markers tunable in classification.yaml "
        "+ ~/.evarness overlay) and ARMS the egress law: from here on, "
        "model and tool boundaries check the run's classification high-water "
        "mark against the egress table. 'Personal never leaves local tiers' "
        "as topology, not convention."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        classifier: str = "keyword"  # any registered classifier
        egress: Literal["off", "warn", "enforce"] = "enforce"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        classification, signals, unknown = classify(text, cfg.classifier)
        # monotonic high-water mark: later, lower classifications never launder
        # earlier, higher ones; the strictest requested egress mode wins
        ctx.classification = max_class(getattr(ctx, "classification", "public"), classification)
        current = getattr(ctx, "egress_mode", "off")
        if _EGRESS_MODE_RANK[cfg.egress] > _EGRESS_MODE_RANK.get(current, 0):
            ctx.egress_mode = cfg.egress
        ctx.emit(
            "content_classified",
            node_id,
            classifier=cfg.classifier if not unknown else "keyword",
            classification=classification,
            signals=signals[:8],
            run_classification=ctx.classification,
            egress=ctx.egress_mode,
            **({"unknown_classifier": unknown} if unknown else {}),
        )
        return text


@register
class ApprovalGateNode(NodeSpec):
    type_name = "approval_gate"
    group = "governance"
    doc = (
        "Human-in-the-loop checkpoint: PAUSES the run (never silently fails) "
        "until a person approves or rejects. Resume replays deterministically "
        "with the decision injected. The runtime counterpart to a tool's "
        "static approve_side_effects flag — that one is the author's "
        "design-time opt-in; this is a person's decision at run time."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        prompt: str = "Approve this action?"
        # WHEN a human is required. always | classified (anything above public) |
        # personal_or_secret (only the most sensitive). Composes with the egress law.
        require_when: Literal["always", "classified", "personal_or_secret"] = "always"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        classification = getattr(ctx, "classification", "public")
        if cfg.require_when == "classified":
            needs = classification != "public"
        elif cfg.require_when == "personal_or_secret":
            needs = classification in ("personal", "secret")
        else:
            needs = True
        if not needs:
            ctx.emit(
                "approval_skipped",
                node_id,
                require_when=cfg.require_when,
                classification=classification,
            )
            return text

        decision = getattr(ctx, "approvals", {}).get(node_id)
        if decision is None:
            # no human decision yet — pause, don't fail. The engine records the
            # run as `paused`; a resume with approvals[node_id] set replays here.
            ctx.emit(
                "approval_requested",
                node_id,
                prompt=cfg.prompt,
                preview=text[:120],
                classification=classification,
            )
            raise RunPaused(node_id, cfg.prompt, text[:120])
        if decision == "approve":
            ctx.emit("approval_granted", node_id, prompt=cfg.prompt)
            return text
        ctx.emit("approval_rejected", node_id, prompt=cfg.prompt, decision=str(decision))
        raise NodeBlocked(node_id, f"human rejected the action: {cfg.prompt}")


@register
class TierRouterNode(NodeSpec):
    type_name = "tier_router"
    group = "governance"
    doc = (
        "Routes the turn to a model tier by intent (tiers + intent map in "
        "tiers.yaml + ~/.evarness overlay). Each tier declares a locality; "
        "when the run is classified and the chosen tier's locality is "
        "egress-forbidden, it DOWNSHIFTS to a local tier — 'personal never "
        "leaves local tiers' as a routing decision, not just a boundary block "
        ". Arms the downstream llm/loop provider + locality."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        # per-harness overrides of tiers.yaml (empty = use the packaged/overlay map)
        intents: dict[str, str] = Field(default_factory=dict)
        default_tier: str = ""
        # what to do when the intent's tier is egress-forbidden for this content
        on_forbidden_egress: Literal["downshift", "block", "warn"] = "downshift"

    @staticmethod
    def _last_intent(ctx) -> str | None:
        for e in reversed(ctx.emitter.events):
            if e["type"] == "intent_routed":
                return e["payload"].get("route")
        return None

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        from .providers import make_provider
        from .tiers import fallback_tier, resolve_tier, tier_locality, tier_provider

        text = as_text(inputs.get("in", ""))
        intent = cls._last_intent(ctx)
        tier, unknown = resolve_tier(intent, cfg.intents, cfg.default_tier)
        if unknown:
            tier = fallback_tier()
        locality = tier_locality(tier)
        reason = "intent"

        egress_on = getattr(ctx, "egress_mode", "off") != "off"
        classification = getattr(ctx, "classification", "public")
        if egress_on and not egress_allowed(classification, locality):
            if cfg.on_forbidden_egress == "block":
                ctx.emit(
                    "egress_denied",
                    node_id,
                    destination=locality,
                    classification=classification,
                    action="block",
                )
                raise NodeBlocked(
                    node_id,
                    f"tier '{tier}' ({locality}) is egress-"
                    f"forbidden for {classification} content",
                )
            if cfg.on_forbidden_egress == "warn":
                ctx.emit(
                    "tier_egress_warning",
                    node_id,
                    tier=tier,
                    locality=locality,
                    classification=classification,
                )
            else:  # downshift — the classification×tier-routing payoff
                fb = fallback_tier()
                fb_loc = tier_locality(fb)
                if egress_on and not egress_allowed(classification, fb_loc):
                    # nothing legal to route to: secret content can reach no tier
                    ctx.emit(
                        "egress_denied",
                        node_id,
                        destination=fb_loc,
                        classification=classification,
                        action="block",
                    )
                    raise NodeBlocked(
                        node_id,
                        f"no egress-legal tier for "
                        f"{classification} content — redact before routing",
                    )
                ctx.emit(
                    "tier_downshifted",
                    node_id,
                    **{"from": tier},
                    to=fb,
                    reason=f"egress_{classification}",
                    classification=classification,
                )
                tier, locality, reason = fb, fb_loc, "egress_downshift"

        spec = tier_provider(tier)
        ctx.tier = tier
        ctx.tier_locality = locality
        ctx.provider = make_provider(spec, ctx.fixture)
        ctx.emit(
            "tier_selected",
            node_id,
            intent=intent,
            tier=tier,
            provider=spec,
            locality=locality,
            reason=reason,
            **({"unknown_tier": True} if unknown else {}),
        )
        return text


# ---------------------------------------------------------------- tools & RAG


@register
class ToolNode(NodeSpec):
    type_name = "tool"
    group = "tools"
    doc = (
        "Tool with a sim twin (fixture-scripted) and real twins behind the same "
        "query->documents contract: web.fetch (read a URL you have; host allowlist, "
        "deny by default), web.search (terms -> ranked sources; searxng or free ddg), "
        "and fs.search (confined under its root). mode: real makes the run "
        "non-deterministic — the trace says so."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "documents"}

    class Config(BaseModel):
        tool: str = "email.search"
        mode: Literal["sim", "real"] = "sim"
        timeout_ms: int = 3000
        # T0 safety opt-in: a tool whose manifest declares write/destructive
        # side effects only runs when the user approves it HERE, per node
        approve_side_effects: bool = False
        retries: int = 2
        allow_hosts: list[str] = Field(
            default_factory=list
        )  # web.fetch allowlist / web.search scope
        root: str = "~/.evarness/sandbox"  # fs.search confinement
        # containment for mode:real. off = in-process (historical); subprocess
        # = child w/ timeout+rlimits+scrubbed env+per-invocation secrets; strict
        # = subprocess + OS confinement (macOS sandbox-exec / Linux bubblewrap:
        # deny network for network:none tools, deny writes outside `root`). Blank
        # = the sandbox.yaml default. A requested-but-unavailable level BLOCKS the run.
        sandbox: Literal["", "off", "subprocess", "strict"] = ""
        # governed egress for a network:outbound tool under sandbox:strict.
        # off = the OS network policy of the tier applies as-is; gateway = the
        # tool reaches ONLY a hostname-allowlisting proxy (allow_hosts), every
        # other destination denied by the OS. Unavailable ⇒ the run BLOCKS.
        egress: Literal["", "off", "gateway"] = ""
        # web.search knobs (ignored by other tools). search_provider names any
        # registered backend; search_options carries provider-specific, non-secret
        # config (e.g. {"searxng_url": "..."}) — API keys come from env, never here.
        search_provider: str = "searxng"  # any registered provider (built-ins: searxng, duckduckgo)
        searxng_url: str = ""  # SearXNG endpoint — user-owned node config (or search_options)
        search_category: str = "general"  # general | news | code | social
        freshness: str = "any"  # any | day | week | month | year
        max_results: int = 8
        search_options: dict[str, str] = Field(default_factory=dict)
        # write-only: holds "" or the secret marker, NEVER the key. The plaintext lives
        # in the vault; a provider reads it via self.secret() at runtime.
        api_key: str = ""

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        query = as_text(inputs.get("in", ""))
        spec = _tool_spec_gate(cfg.tool, cfg, node_id)  # refuses unapproved side effects
        _egress_gate(ctx, node_id, _tool_destination(spec, cfg.mode))  # egress law
        ctx.emit(
            "tool_called",
            node_id,
            tool=cfg.tool,
            query=query[:120],
            mode=cfg.mode,
            **({"tool_version": spec.version} if spec else {}),
        )
        _warn_free_search(node_id, cfg.tool, cfg.mode, getattr(cfg, "search_provider", ""), ctx)
        sim_default = False
        try:
            if cfg.mode == "real":
                result = _run_real_tool_contained(
                    cfg.tool, query, cfg, spec, node_id, ctx, getattr(cfg, "sandbox", "")
                )
            else:
                result = ctx.fixture.tool_result(cfg.tool, query)
                if not result:
                    result = _sim_default_result(spec, cfg.tool, query, ctx)
                    sim_default = bool(result)
        except ToolError as exc:
            ctx.emit("tool_error", node_id, tool=cfg.tool, mode=cfg.mode, error=str(exc))
            return []
        ctx.emit(
            "tool_result",
            node_id,
            tool=cfg.tool,
            count=len(result),
            docs=_doc_previews(result),
            **({"sim_default": True} if sim_default else {}),
        )
        return result


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


# ---------------------------------------------------------------- memory & context


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


# ---------------------------------------------------------------- governance (phase 4)


@register
class LLMGuardNode(NodeSpec):
    type_name = "llm_guard"
    group = "governance"
    doc = (
        "Probabilistic guard — a classifier, i.e. a model guarding a model. Real "
        "detections come from fixture attack markers; the sim FP/FN knobs (drawn "
        "from the seeded rng) let lessons show guards failing honestly."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        detects: list[str] = Field(default_factory=lambda: ["prompt_injection", "jailbreak"])
        threshold: float = 0.85
        fp_rate: float = 0.02
        fn_rate: float = 0.05
        on_trigger: Literal["block", "flag"] = "block"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        low = text.lower()
        detections = [
            a
            for a in ctx.fixture.attacks
            if str(a.get("marker", "")).lower() in low
            and (a.get("type", "prompt_injection") in cfg.detects or "all" in cfg.detects)
        ]
        score = max((float(a.get("severity", 0.9)) for a in detections), default=0.0)
        triggered = bool(detections) and score >= cfg.threshold
        missed = false_positive = False
        roll = ctx.rng.random()  # one seeded draw per evaluation — deterministic per seed
        if triggered and roll < cfg.fn_rate:
            triggered, missed = False, True
        elif not detections and roll < cfg.fp_rate:
            triggered, false_positive, score = True, True, cfg.threshold
        ctx.emit(
            "guard_evaluated",
            node_id,
            detections=len(detections),
            score=round(score, 4),
            triggered=triggered,
            missed=missed,
            false_positive=false_positive,
        )
        if triggered:
            kinds = sorted({a.get("type", "prompt_injection") for a in detections}) or ["suspected"]
            ctx.emit("guard_triggered", node_id, kinds=kinds, false_positive=false_positive)
            if cfg.on_trigger == "block":
                raise NodeBlocked(
                    node_id,
                    f"llm_guard triggered on {'/'.join(kinds)}"
                    + (" (false positive)" if false_positive else ""),
                )
        return text


@register
class LLMJudgeNode(NodeSpec):
    type_name = "llm_judge"
    group = "governance"
    doc = (
        "LLM-as-judge scoring node (sim:judge-v1). Emits judge_scored events the "
        "Experiments metrics consume. Judges drift too — calibrate against fixtures."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        rubric: list[str] = Field(default_factory=lambda: ["groundedness", "safety", "tone"])
        threshold: float = 0.5
        on_fail: Literal["flag", "block"] = "flag"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        scores = ctx.fixture.judge_scores(text, cfg.rubric)
        mean = round(sum(scores.values()) / len(scores), 4) if scores else 0.0
        ctx.emit("judge_scored", node_id, provider="sim:judge-v1", scores=scores, mean=mean)
        if mean < cfg.threshold:
            ctx.emit("judge_flagged", node_id, mean=mean, threshold=cfg.threshold)
            if cfg.on_fail == "block":
                raise NodeBlocked(
                    node_id, f"llm_judge score {mean} below threshold {cfg.threshold}"
                )
        return text


@register
class JudgeChainNode(NodeSpec):
    type_name = "judge_chain"
    group = "governance"
    doc = (
        "An ordered curation chain: several registered judges run in "
        "sequence, each with its own power — safety/leak HALT, schema is "
        "repairable (retry budget), faithfulness WARNS or halts. The first "
        "halt short-circuits. A judge the fixture marks as timed-out FAILS "
        "OPEN (a banner, not a block). Richer than a single llm_judge."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        judges: list[str] = Field(default_factory=lambda: ["safety", "faithfulness"])
        retry_budget: int = 1  # repair attempts per repairable (retry) judge

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        from .judges import get_judge, get_repair, judge_config

        text = as_text(inputs.get("in", ""))
        timeouts = ctx.fixture.judge_timeouts
        verdicts: list[dict] = []
        banners: list[str] = []

        for name in cfg.judges:
            jcfg = judge_config(name)
            # fail-open: an unavailable judge must not block the response
            if name.lower() in timeouts:
                ctx.emit("judge_degraded", node_id, judge=name, reason="timeout")
                banners.append(f"{name} unavailable")
                verdicts.append({"judge": name, "verdict": "degraded"})
                continue
            judge = get_judge(name)
            if judge is None:
                ctx.emit("judge_signal", node_id, judge=name, verdict="unknown_judge")
                verdicts.append({"judge": name, "verdict": "unknown_judge"})
                continue

            repair = get_repair(name)
            attempts = 0
            while True:
                sig = judge(text, jcfg, ctx)
                ctx.emit(
                    "judge_signal",
                    node_id,
                    judge=name,
                    verdict=sig.verdict,
                    **({"score": round(sig.score, 4)} if sig.score is not None else {}),
                    **({"reason": sig.reason} if sig.reason else {}),
                )
                if sig.verdict == "retry" and attempts < cfg.retry_budget:
                    attempts += 1
                    if repair is not None:
                        text = repair(text)
                        ctx.emit("judge_repaired", node_id, judge=name, attempt=attempts)
                    continue
                break

            verdict = sig.verdict
            if verdict == "retry":  # budget exhausted without a pass
                verdict = jcfg.get("on_exhausted", "warn")
                ctx.emit(
                    "judge_exhausted", node_id, judge=name, attempts=attempts, downgraded_to=verdict
                )
            if verdict == "halt":
                ctx.emit("chain_halted", node_id, judge=name, reason=sig.reason)
                raise NodeBlocked(
                    node_id, f"judge '{name}' halted the response: " f"{sig.reason or 'unsafe'}"
                )
            if verdict == "warn":
                banners.append(sig.reason or f"{name} flagged")
            verdicts.append({"judge": name, "verdict": verdict})

        ctx.emit(
            "judge_chain_finished", node_id, verdicts=verdicts, banners=banners, passed=not banners
        )
        if banners:
            # a warned/degraded response ships, but honestly labeled (the trace
            # and the reader both see it was reviewed, not silently passed)
            text = f"[reviewed: {'; '.join(banners)}] {text}"
        return text


@register
class RedactionRulesNode(NodeSpec):
    type_name = "redaction_rules"
    group = "governance"
    doc = (
        "Deterministic redaction — pure rules, provable, unit-testable. Emits "
        "redaction_applied events for the compliance trail."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        rules: list[str] = Field(default_factory=lambda: list(REDACTION_RULES))
        mask: str = "████"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text, count, hit = redact(as_text(inputs.get("in", "")), cfg.rules, cfg.mask)
        ctx.emit("redaction_applied", node_id, count=count, rules_hit=hit)
        return text


@register
class PolicyGateNode(NodeSpec):
    type_name = "policy_gate"
    group = "governance"
    doc = (
        "Org-policy allow/deny rules evaluated deterministically from config — the "
        "contrast to the interceptor's fixture-world blocklist. Warn mode traces "
        "the verdict without blocking."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        deny: list[str] = Field(default_factory=lambda: ["wire transfer", "password"])
        mode: Literal["enforce", "warn"] = "enforce"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        text = as_text(inputs.get("in", ""))
        low = text.lower()
        hit = next((t for t in cfg.deny if str(t).lower() in low), None)
        ctx.emit(
            "policy_checked",
            node_id,
            verdict="deny" if hit else "allow",
            mode=cfg.mode,
            **({"term": hit} if hit else {}),
        )
        if hit and cfg.mode == "enforce":
            raise NodeBlocked(node_id, f"policy_gate denied content containing '{hit}'")
        return text


@register
class RateBudgetLimiterNode(NodeSpec):
    type_name = "rate_budget_limiter"
    group = "governance"
    doc = (
        "Hard resource ceilings checked at this point in the flow. Emits "
        "budget_breached — the difference between a demo and a production harness."
    )
    inputs: ClassVar[dict] = {"in": "any"}
    outputs: ClassVar[dict] = {"out": "any"}

    class Config(BaseModel):
        max_tokens: int = 10000
        max_tool_calls: int = 8
        on_breach: Literal["abort", "degrade"] = "abort"

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        tokens = ctx.totals["tokens"]
        tool_calls = sum(1 for e in ctx.emitter.events if e["type"] == "tool_called")
        breach = tokens > cfg.max_tokens or tool_calls > cfg.max_tool_calls
        ctx.emit(
            "budget_checked",
            node_id,
            tokens=tokens,
            tool_calls=tool_calls,
            max_tokens=cfg.max_tokens,
            max_tool_calls=cfg.max_tool_calls,
            within=not breach,
        )
        if breach:
            ctx.emit(
                "budget_breached",
                node_id,
                tokens=tokens,
                tool_calls=tool_calls,
                action=cfg.on_breach,
            )
            if cfg.on_breach == "abort":
                raise NodeBlocked(
                    node_id, f"budget breached: {tokens} tokens, " f"{tool_calls} tool calls"
                )
        return inputs.get("in")


# ---------------------------------------------------------------- observability (phase 4)

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


# ---------------------------------------------------------------- long-term memory (phase 4)
# The fixture is the persistent world: episodic/facts/instructions sections are what
# "was remembered" before this run. Writes are trace events, not cross-run persistence —
# honest sim, the write PATH (governance, approval, redaction) is what these nodes teach.


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


# ---------------------------------------------------------------- ReAct loop (phase 4)


@register
class LoopControllerNode(NodeSpec):
    type_name = "loop_controller"
    group = "core"
    doc = (
        "ReAct tool loop: the model decides which tool to call, the harness owns "
        "the loop — iteration cap, allowed-tool gate, repeated-action guard, exit "
        "conditions. Decisions are fixture-scripted (`react` section) in sim mode; "
        "with a real provider and no script, the model drives the TOOL/FINAL "
        "protocol live under the same guards."
    )
    inputs: ClassVar[dict] = {"in": "text"}
    outputs: ClassVar[dict] = {"out": "text"}

    class Config(BaseModel):
        tools: list[str] = Field(default_factory=lambda: ["email.search"])
        max_iterations: int = 4
        dedup_guard: bool = True
        system: str = DEFAULT_AGENT_SYSTEM  # standing instruction (editable); never empty
        on_stop: Literal["best_effort", "answer_partial", "fail"] = "best_effort"
        require_evidence: bool = True  # refuse a live-model FINAL before any tool ran
        # deterministic faithfulness gate: entities/figures in a live FINAL must
        # appear in the gathered Observations. off | warn (trace only) | retry (refuse
        # once, nudge the model to correct, then accept with the verdict traced)
        grounding_check: Literal["off", "warn", "retry"] = "warn"
        # WHICH registered rules run: built-in entity_support; bring your own
        # via register_grounding_rule + tune in grounding.yaml / ~/.evarness overlay
        grounding_rules: list[str] = Field(default_factory=lambda: ["entity_support"])
        # how many unsupported FINALs to refuse before accepting (with the verdict
        # traced) — small models may need two nudges to actually search a topic
        grounding_retries: int = 1
        tool_mode: Literal["sim", "real"] = "sim"  # how allowed tools execute
        timeout_ms: int = 3000
        # T0 safety opt-in: a tool whose manifest declares write/destructive
        # side effects only runs when the user approves it HERE, per node
        approve_side_effects: bool = False
        allow_hosts: list[str] = Field(
            default_factory=list
        )  # web.fetch allowlist / web.search scope
        root: str = "~/.evarness/sandbox"  # fs.search confinement
        sandbox: Literal["", "off", "subprocess", "strict"] = ""  # containment for tool_mode:real
        egress: Literal["", "off", "gateway"] = ""  # filtered egress under strict
        # web.search knobs — the loop can hold both web.search and web.fetch and pick per
        # step. search_provider names any registered backend; keys come from env.
        search_provider: str = "searxng"  # any registered provider (built-ins: searxng, duckduckgo)
        searxng_url: str = ""  # SearXNG endpoint — user-owned node config (or search_options)
        search_category: str = "general"  # general | news | code | social
        freshness: str = "any"  # any | day | week | month | year
        max_results: int = 8
        search_options: dict[str, str] = Field(default_factory=dict)
        # write-only: holds "" or the secret marker, NEVER the key
        api_key: str = ""

    REACT_PROTOCOL = PROTOCOLS["react_decision"]  # data, not code (prompts.yaml)

    @classmethod
    def _model_decision(cls, ctx, node_id, cfg, transcript):
        """Real-provider ReAct step: the model replies in the TOOL/FINAL protocol;
        a reply outside the protocol is treated as a final answer (models do that)."""
        prompt = (f"{cfg.system}\n\n" if cfg.system else "") + cls.REACT_PROTOCOL.format(
            tools=", ".join(cfg.tools), transcript=transcript
        )
        try:
            completion = ctx.provider.complete(prompt, max_tokens=256)
        except Exception as exc:
            ctx.emit("llm_error", node_id, provider=ctx.params.provider, error=str(exc))
            raise
        ctx.totals["tokens"] += completion.input_tokens + completion.output_tokens
        ctx.totals["cost_usd"] = round(ctx.totals.get("cost_usd", 0.0) + completion.cost_usd, 6)
        text = completion.text.strip()
        # models mix lines ("FINAL: I don't know.\nTOOL: ..." — found live):
        # a TOOL line ANYWHERE wins — the intent to gather evidence beats the hedge
        tool_line = next(
            (ln.strip() for ln in text.splitlines() if ln.strip().upper().startswith("TOOL:")), None
        )
        if tool_line:
            tool_part, _, rest = tool_line[5:].partition("|")
            tool_input = rest.split(":", 1)[1].strip() if ":" in rest else rest.strip()
            rule = {"action": {"tool": tool_part.strip(), "input": tool_input}}
            kind, preview = "tool_call", tool_line
        else:
            rule = {
                "respond": {"text": text[6:].strip() if text.upper().startswith("FINAL:") else text}
            }
            kind, preview = "final", text
        ctx.emit(
            "llm_response",
            node_id,
            tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            preview=preview[:120],
            decision=kind,
        )
        return rule

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        question = as_text(inputs.get("in", ctx.user_input))
        # the loop is a model boundary — one gate before any iteration, so a
        # denied run shows egress_denied and never a loop_started or llm_request
        _egress_gate(ctx, node_id, _provider_locality(ctx))
        transcript = question
        used: set[int] = set()
        seen_actions: set[tuple[str, str]] = set()
        queries_run: list[str] = []  # ordered, for the coverage rules
        observations = ""  # evidence WITHOUT the question — coverage rules must
        #                    not match request words against the request itself
        answer, stopped, iterations = None, "max_iterations", 0
        refused_final = False
        grounding_retries_used = 0
        # fixture react rules always win (deterministic lessons/tests); a real
        # provider without a script drives the live TOOL/FINAL protocol
        scripted = bool(ctx.fixture.react_rules) or ctx.provider.deterministic
        # "top 10" is a contract: a requested count raises the search
        # result cap so the count is even possible — traced, never silent
        wanted = None if scripted else requested_count(question)
        if wanted and getattr(cfg, "max_results", 0) < wanted:
            cfg = cfg.model_copy(update={"max_results": wanted})
        ctx.emit(
            "loop_started",
            node_id,
            max_iterations=cfg.max_iterations,
            tools=cfg.tools,
            scripted=scripted,
            **({"requested_count": wanted, "max_results": cfg.max_results} if wanted else {}),
        )

        for i in range(cfg.max_iterations):
            iterations = i + 1
            ctx.emit("loop_iteration", node_id, iteration=i)
            ptok = approx_tokens(transcript)
            ctx.emit(
                "llm_request",
                node_id,
                provider=ctx.params.provider,
                purpose="react_decision",
                iteration=i,
                prompt_tokens=ptok,
            )

            if scripted:
                idx, rule = ctx.fixture.react_decision(transcript, used)
                if rule is None:  # off-script: the honest "I don't know", not fake competence
                    answer = ctx.fixture.default_response.get("text", "")
                    ctx.totals["tokens"] += ptok + approx_tokens(answer)
                    ctx.emit(
                        "llm_response",
                        node_id,
                        tokens=approx_tokens(answer),
                        preview=answer[:120],
                        decision="final_default",
                    )
                    stopped = "no_matching_script"
                    break
                used.add(idx)
            else:
                rule = cls._model_decision(ctx, node_id, cfg, transcript)

            if "respond" in rule:
                # a live model will happily answer from its priors
                # without touching a tool. The harness refuses the FIRST ungrounded
                # final and nudges; a second one is accepted (honest ignorance beats
                # a dead loop). Scripted fixtures are exempt — the script IS the
                # lesson author's intent.
                if not scripted and cfg.require_evidence and not seen_actions and not refused_final:
                    refused_final = True
                    ctx.emit(
                        "loop_guard",
                        node_id,
                        reason="final_without_evidence",
                        refused_preview=rule["respond"].get("text", "")[:80],
                    )
                    transcript += "\n" + NUDGES["evidence_required"]
                    continue
                answer = rule["respond"].get("text", "")
                # evidence PRESENCE isn't evidence
                # FAITHFULNESS — a live model turned "soccer politics at World Cup"
                # into "the U.S. Open controversy". Deterministic gate: entities and
                # figures in the FINAL must appear in the Observations. Only
                # meaningful once tools ran; scripted fixtures stay exempt.
                if not scripted and cfg.grounding_check != "off" and seen_actions:
                    g_ctx = {
                        "question": question,
                        "queries": queries_run,
                        "observations": observations,
                    }
                    unsupported, unknown_rules = check_grounding(
                        answer, transcript, cfg.grounding_rules, g_ctx
                    )
                    refuse = bool(
                        unsupported
                        and cfg.grounding_check == "retry"
                        and grounding_retries_used < cfg.grounding_retries
                    )
                    ctx.emit(
                        "grounding_checked",
                        node_id,
                        mode=cfg.grounding_check,
                        verdict="unsupported" if unsupported else "grounded",
                        unsupported=unsupported[:8],
                        refused=refuse,
                        rules=cfg.grounding_rules,
                        **({"unknown_rules": unknown_rules} if unknown_rules else {}),
                    )
                    if refuse:
                        grounding_retries_used += 1
                        # an uncovered topic needs a SEARCH, not a rewrite — lead
                        # with the tool command (found live: the generic nudge got
                        # a reworded answer instead of the missing search)
                        topics = [
                            v.split("'")[1] for v in unsupported if v.startswith("request topic '")
                        ]
                        if topics:
                            transcript += "\n" + NUDGES["grounding_topics_missing"].format(
                                topics=", ".join(topics), first_topic=topics[0]
                            )
                        else:
                            transcript += "\n" + NUDGES["grounding_flagged"].format(
                                issues="; ".join(unsupported[:5])
                            )
                        answer = None  # after the retry budget, accepted (traced)
                        continue
                if scripted:
                    ctx.totals["tokens"] += ptok + approx_tokens(answer)
                    ctx.emit(
                        "llm_response",
                        node_id,
                        tokens=approx_tokens(answer),
                        preview=answer[:120],
                        decision="final",
                    )
                stopped = "final_answer"
                break

            action = rule.get("action", {})
            tool, tool_input = str(action.get("tool", "")), str(action.get("input", ""))
            if scripted:
                decision = f"call {tool}({tool_input})"
                ctx.totals["tokens"] += ptok + approx_tokens(decision)
                ctx.emit(
                    "llm_response",
                    node_id,
                    tokens=approx_tokens(decision),
                    preview=decision,
                    decision="tool_call",
                )

            if tool not in cfg.tools:  # the model asked; the harness refuses
                ctx.emit("loop_guard", node_id, reason="tool_not_allowed", tool=tool)
                raise NodeBlocked(
                    node_id,
                    f"loop_controller: model requested tool "
                    f"'{tool}' outside allowed tools {cfg.tools}",
                )
            if cfg.dedup_guard and (tool, tool_input) in seen_actions:
                ctx.emit(
                    "loop_guard", node_id, reason="repeated_action", tool=tool, input=tool_input
                )
                stopped = "repeated_action"
                break
            seen_actions.add((tool, tool_input))

            spec = _tool_spec_gate(tool, cfg, node_id)  # NodeBlocked if unapproved
            _egress_gate(ctx, node_id, _tool_destination(spec, cfg.tool_mode))  # egress law
            ctx.emit(
                "tool_called",
                node_id,
                tool=tool,
                query=tool_input[:120],
                iteration=i,
                mode=cfg.tool_mode,
                **({"tool_version": spec.version} if spec else {}),
            )
            tool_err = None
            sim_default = False
            try:
                if cfg.tool_mode == "real":
                    result = _run_real_tool_contained(
                        tool, tool_input, cfg, spec, node_id, ctx, getattr(cfg, "sandbox", "")
                    )
                else:
                    result = ctx.fixture.tool_result(tool, tool_input)
                    if not result:
                        result = _sim_default_result(spec, tool, tool_input, ctx)
                        sim_default = bool(result)
            except ToolError as exc:
                ctx.emit("tool_error", node_id, tool=tool, mode=cfg.tool_mode, error=str(exc))
                result, tool_err = [], str(exc)
            ctx.emit(
                "tool_result",
                node_id,
                tool=tool,
                count=len(result),
                docs=_doc_previews(result),
                **({"sim_default": True} if sim_default else {}),
            )
            # the model must see WHY a call failed, not a bare "no results" —
            # otherwise it can't distinguish "nothing exists" from "bad call"
            obs = "; ".join(
                f"{d.get('subject', '')}: {d.get('snippet', d.get('text', ''))}" for d in result
            ) or (f"tool error: {tool_err}" if tool_err else "no results")
            transcript += f"\nObservation[{tool}]: {obs}"
            queries_run.append(tool_input)
            observations += f"\nObservation[{tool}]: {obs}"
            # an empty search is usually a bad QUERY, not absent information —
            # live models take "no results" as "nothing exists" and give up
            # (found live: a verbatim-sentence query got 0 hits and the model
            # reported "no news available"). The harness nudges a reformulated
            # retry; dedup_guard already blocks repeating the same query.
            # Scripted fixtures are exempt — the script is the author's intent.
            if (
                not result
                and not scripted
                and tool.endswith(".search")
                and i + 1 < cfg.max_iterations
            ):
                ctx.emit(
                    "loop_guard",
                    node_id,
                    reason="empty_search_retry",
                    tool=tool,
                    query=tool_input[:120],
                )
                transcript += "\n" + NUDGES["empty_search_retry"].format(tool=tool)

        ctx.emit(
            "loop_finished",
            node_id,
            iterations=iterations,
            stopped=stopped,
            transcript_tokens=approx_tokens(transcript),
        )
        if answer is None:
            if cfg.on_stop == "fail":
                raise NodeBlocked(
                    node_id,
                    f"loop_controller stopped without an answer "
                    f"({stopped} after {iterations} iterations)",
                )
            if cfg.on_stop == "best_effort" and not scripted:
                # the user deserves a response, not a dead loop (user feedback):
                # one wrap-up call, grounded ONLY in what the tools actually returned
                answer = cls._best_effort(ctx, node_id, cfg, transcript, stopped)
                # the wrap-up can distort too — verdict traced, no budget to retry
                if answer and cfg.grounding_check != "off" and seen_actions:
                    unsupported, unknown_rules = check_grounding(
                        answer,
                        transcript,
                        cfg.grounding_rules,
                        {
                            "question": question,
                            "queries": queries_run,
                            "observations": observations,
                        },
                    )
                    ctx.emit(
                        "grounding_checked",
                        node_id,
                        mode="warn",
                        verdict="unsupported" if unsupported else "grounded",
                        unsupported=unsupported[:8],
                        refused=False,
                        rules=cfg.grounding_rules,
                        **({"unknown_rules": unknown_rules} if unknown_rules else {}),
                    )
            if answer is None:
                tried = ", ".join(f"{t}({q[:40]})" for t, q in sorted(seen_actions)) or "no tools"
                answer = (
                    f"I could not reach a final answer ({stopped} after "
                    f"{iterations} iterations; tried {tried})."
                )
        return answer

    @classmethod
    def _best_effort(cls, ctx, node_id, cfg, transcript, stopped) -> str | None:
        prompt = (f"{cfg.system}\n\n" if cfg.system else "") + PROTOCOLS[
            "best_effort_wrapup"
        ].format(stopped=stopped, transcript=transcript)
        ctx.emit(
            "llm_request",
            node_id,
            provider=ctx.params.provider,
            purpose="best_effort_answer",
            prompt_tokens=approx_tokens(prompt),
        )
        try:
            completion = ctx.provider.complete(prompt, max_tokens=400)
        except Exception as exc:  # fall back to the honest partial text
            ctx.emit("llm_error", node_id, provider=ctx.params.provider, error=str(exc))
            return None
        ctx.totals["tokens"] += completion.input_tokens + completion.output_tokens
        ctx.totals["cost_usd"] = round(ctx.totals.get("cost_usd", 0.0) + completion.cost_usd, 6)
        text = completion.text.strip()
        if text.upper().startswith("FINAL:"):
            text = text[6:].strip()
        ctx.emit(
            "llm_response",
            node_id,
            tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            preview=text[:120],
            decision="best_effort",
        )
        return text or None
