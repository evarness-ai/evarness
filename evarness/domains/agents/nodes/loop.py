"""The agent loop: the ReAct controller and its protocol."""

from __future__ import annotations

from __future__ import annotations
from typing import ClassVar, Literal
from pydantic import BaseModel, Field
from evarness.domains.agents.grounding import check_grounding, requested_count
from evarness.domains.agents.prompts import NUDGES, PROTOCOLS
from evarness.domains.agents.sim import (
    ToolError,
    approx_tokens,
)
from evarness.core.errors import NodeBlocked
from evarness.core.registry import register_node as register

from evarness.domains.agents.nodes.tools import (
    _run_real_tool_contained,
    _sim_default_result,
    _tool_spec_gate,
)
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
