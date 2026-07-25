"""The deterministic controls: routing, interception, classification,
approvals, tier routing, guards, judges, redaction, policy gates, budgets."""

from __future__ import annotations

from __future__ import annotations
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from evarness.domains.agents.classification import classify, egress_allowed, max_class
from evarness.domains.agents.state import agents_state
from evarness.domains.agents.sim import (
    REDACTION_RULES,
    redact,
    redact_pii,
)
from evarness.core.errors import NodeBlocked, RunPaused
from evarness.core.registry import register_node as register

from evarness.domains.agents.nodes.base import (
    _EGRESS_MODE_RANK,
    NodeSpec,
    as_text,
)


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
        st = agents_state(ctx)
        st.classification = max_class(st.classification, classification)
        if _EGRESS_MODE_RANK[cfg.egress] > _EGRESS_MODE_RANK.get(st.egress_mode, 0):
            st.egress_mode = cfg.egress
        ctx.emit(
            "content_classified",
            node_id,
            classifier=cfg.classifier if not unknown else "keyword",
            classification=classification,
            signals=signals[:8],
            run_classification=st.classification,
            egress=st.egress_mode,
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
        classification = agents_state(ctx).classification
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
        from evarness.domains.agents.providers import make_provider
        from evarness.domains.agents.tiers import (
            fallback_tier,
            resolve_tier,
            tier_locality,
            tier_provider,
        )

        text = as_text(inputs.get("in", ""))
        intent = cls._last_intent(ctx)
        tier, unknown = resolve_tier(intent, cfg.intents, cfg.default_tier)
        if unknown:
            tier = fallback_tier()
        locality = tier_locality(tier)
        reason = "intent"

        st = agents_state(ctx)
        egress_on = st.egress_mode != "off"
        classification = st.classification
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
        st.tier = tier
        st.tier_locality = locality
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
        from evarness.domains.agents.judges import get_judge, get_repair, judge_config

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
