# ADR-0077: Converge on one governed agentic loop — tools as universal actionable items

Status: Accepted (2026-06-27) — Phase 1 shipped; Phases 2-4 planned
Relates to: ADR-0021 (semantic skill router & brief tools), ADR-0007 (per-currency money, no implicit FX), ADR-0068 (escalation judge / observability backbone), the unified governance layer (kernel hook points), issues 0001/0002 (email ReAct loop + clarify)

## Context

A live chat test exposed a design flaw, not a one-off bug. The user asked:

> "I should have some dues from a store-cobranded card, can you find it?"

IRIS replied with a generic precomputed dues digest (an unrelated card's balance) and
**never searched the Gmail** where the actual the store card due emails live. Tracing it:

1. **Single-intent → single-handler dispatch.** The keyword `dues` matched the `finance`
   intent (conf 0.85, no fallback) → the **finance handler**, which was a *deterministic
   digest with zero tools*. It dumped the whole dues list and ignored the named entity.
2. **Tools were siloed per agent.** `system` ran the `AgenticCore` ReAct loop (builtin +
   semantically-gated skills); `email` ran a ReAct loop scoped to email tools; `finance`
   and `calendar` were deterministic with **no tools**. There was no shared pool and no
   way for one turn to use both finance data and email search.
3. **The kernel had no view of context.** The ReAct transcript (Thought/Action/Observation)
   grew unbounded within a turn — every block re-entered the next prompt — and the
   governance kernel only saw the final prompt string at `PRE_LLM_CALL`, never the
   composition or size.

The user's framing is the requirement, and it generalises past finance:

> "It is not just finance and email — tools must surface by all agents if needed. Skills
> and tools are actionable items in the harness; it is the router and model's
> responsibility to decide which tools to perform on what query… This will also lead to
> risks such as context pollution and [mis]routing when combined, so there needs to be a
> layer by the Governor API to decide when, how much context to add and how much to evict,
> at what time. This is critical."

## Decision

Converge on **one governed agentic loop** where domain capabilities are **universal
tools** the model selects among, intent is a **bias** (tier, tool ordering, prompt) rather
than a terminal lane, and a **context-budget layer** bounds what enters each prompt. Three
coupled pillars, rolled out in phases (each validated live before the next), because
widening the tool pool without selection intelligence and a context budget reproduces the
exact pollution/mis-routing the user warned of.

- **P1 — Convergence + minimal context guardrail (shipped).** Finance + email join the
  unified loop as tools; the running transcript is capped.
- **P2 — Router/model intelligence (planned).** Tool-level relevance shortlisting + N-cap;
  tier escalation that triggers on tool-count / context-size, not just multi-step.
- **P3 — Governor context layer (planned).** A `ContextBudgetController` (admission +
  eviction + mid-loop compaction), policy-driven, fully kernel-audited.
- **P4 — Universal surfacing (shipped, opt-in).** Calendar + planner join the loop:
  their digests are in-loop tools (`calendar_lookup`, `daily_plan`), and under
  `IRIS_AGENTIC_CORE_P4=1` (default off, requires the base loop) the `calendar` +
  `planner` intents run the unified ReAct handler. The deterministic handlers survive
  as the loop's per-intent degrade fallbacks (a `{intent → handler}` map) + the
  rollout-off path + routine/brief fast-paths. A `daily_plan`↔`calendar_lookup` prompt
  nudge steers whole-day asks to the aggregator. Calendar **writes** are unaffected —
  `_handle_meeting_creation_turn` short-circuits create/invite upstream of dispatch
  (ADR-0075/0076).

**Context ownership:** an **in-loop controller, kernel-audited** — not a new mandatory
hook. The loop enforces the budget; the kernel **observes and records** context size and
evictions via `PRE_LLM_CALL` telemetry. (Can be promoted to a hard `PRE_PROMPT_ASSEMBLE`
hook later if pre-assembly veto is ever needed.) Default-on, no feature flag.

## Phase 1 — what shipped

- **`finance_lookup` tool** (`bootstrap._domain_tools`): a single entity-aware tool
  wrapping the deterministic finance digest, so dues / balances / holdings / net-worth /
  spend all resolve through one tool and honour a named bank or card. Paired with the
  existing email tools (`search_inbox` / `read_email` / …) on the same loop, so one turn
  can check local statements **and** search email.
- **Finance routes to the unified ReAct handler** when the agentic loop is on; the
  deterministic digest becomes the loop's **degrade fallback** (and the rollout-off path),
  so the worst case is never worse than before — and never a web-search stall.
  `intent="finance"` still biases tier-2 (added to `tier2.use_for`) + the prompt nudge.
- **Entity-aware dues** (`digest.build_dues_digest(query=…)`): filters to a named
  institution (reusing the statements/balances entity-matching pattern), and when it has
  nothing for that name reports it honestly (`named_absent`) so the loop knows to look in
  email. Generic "any dues pending?" is unaffected (a dues-vocabulary frame prevents
  false "named" detection). Per-currency grouping preserved (ADR-0007).
- **Cross-domain prompt nudge** (`agentic_core._build_react_prompt`): one generic sentence
  — "call finance_lookup first; if nothing on file for the named bank/card, follow up with
  search_inbox" — rendered **only** when both tools are present. Names no brand.
- **Context guardrail** (`agentic_core._trim_react_history` + `_apply_context_budget`):
  caps the running transcript at `IRIS_REACT_HISTORY_TOKEN_BUDGET` (default 3000 tok),
  evicting oldest blocks while pinning the freshest. `context_tokens` + `evicted_tokens`
  are emitted into the `PRE_LLM_CALL` hook payload and whitelisted into the audit row
  (counts only — never prompt content).
- The parallel `finance-query` skill is disabled (`default_enabled: false`): redundant and
  non-entity-aware; removing it reduces tool-confusion.

## Alternatives rejected

- **Patch intent regexes case by case.** Explicitly rejected by the user — the finance lane
  was a canned report, not an agent; no regex makes it search email.
- **Governor-owned `PRE_PROMPT_ASSEMBLE` hook now.** Heaviest (splits prompt assembly in
  two); deferred in favour of the in-loop controller, which ships sooner and is auditable.
- **Keep finance deterministic; add an email "see also" heuristic.** Still single-lane;
  doesn't generalise to other cross-domain pairs.

## Consequences

- A finance turn can now answer cross-domain ("find my the store card dues" → finance_lookup →
  search_inbox → answer from email). Determinism for routines/briefs is untouched (they use
  `finance.monitor.bills_due`, not `build_dues_digest`).
- Cross-domain tools run under the loop as `tool/<name>` with `agent_type="system"`;
  governance policies keyed on `email/*` / `agent_type=="email"` must be checked against
  this path (Phase 1 governance test covers tool firing).
- Larger tool menus raise 7B tool-confusion risk — mitigated now (single `finance_lookup`;
  reserved names; conditional nudge; tier-2) and structurally in P2/P3.
- The kernel now has per-turn context-size telemetry — the foundation for the P3 controller.

See `docs/blog/2026-06-27-one-loop-to-find-them.md` for the narrative write-up.

## Addendum (2026-10-01): the shortlist without an embedder

The P2 shortlist ranks by the MiniLM embedder. With no model on disk (a fresh offline
install, the hosted runner) it fell back to pool order, which put the builtins first and
dropped every email tool from an email turn's 12-tool menu. The contract is unchanged:
the core and `pinned` tools are always kept, the cap holds, dropped names are logged,
and with an embedder the ranking is exactly as before. Only what fills the slots the
embedder did not rank changed, to an order that needs no model
(`_deterministic_tool_order` in `runtime/handlers/react.py`):

1. the turn's own domain: the tools of every mounted plugin that serves the turn's intent
   or agent (registered its intent handler, put it on the loop, or lists it under
   `read_first_intents`), read from the registry by `PluginRegistry.tools_serving`;
2. lexical overlap between the query and each tool's name + description, each word
   weighted by how rare it is across the pool (no word list in the core);
3. pool order.

The same order backfills a partly-embedded pool and a router whose query will not embed
(offline with no test flag), so both no-model paths behave alike.
