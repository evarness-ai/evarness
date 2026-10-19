# ADR-0046: Planner P2 — grounded LLM plan narrative (brief headline dropped)

Status: Accepted (2026-06-14)
Context: Planner agent P2 (final phase). Builds on ADR-0044 (deterministic daily plan) + ADR-0045 (planner agent type / parallel execution). Completes the Planner: makes "plan my day" *read* intelligently while keeping the facts deterministic. A morning-brief "today's plan" headline was attempted and **dropped** — see below.

## Decision

1. **LLM narrative layered ON TOP of the deterministic plan, never replacing
   it.** `narrate_plan(plan, *, llm_call)` renders the deterministic
   `DailyPlan` text (ADR-0044) and, when an `llm_call` is supplied, prepends a
   short LLM summary. The factual list is **always appended**, so the model can
   only *summarise* the grounded plan — it cannot invent, drop, or renumber
   items, or alter a time/amount. The prompt says exactly that. This is the
   same deterministic-first contract as Finance (numbers) and Calendar
   (structure): the LLM reads structure, it does not author facts.

2. **Fallback is the deterministic text.** No `llm_call`, an LLM error, or an
   empty plan → return the plain plan text. The narrative is optional polish;
   the facts are never at the model's mercy. (`narrate_plan` catches LLM
   exceptions and logs.)

3. **Wired with the existing tier-1 `llm_call`.** Bootstrap passes the same
   `llm_call` it already gives the TaskPlanner / intent classifier to
   `build_planner_handler(llm_call=...)` — no new client wiring.

4. **No morning-brief headline (dropped).** A `todays_plan` brief slot was
   attempted but **removed**: the routine-authoring section matcher
   (`match_brief_slots`) scores each slot's snippet against the request, and a
   "Today's plan / Nothing scheduled today" slot scores ≥ threshold against a
   bare *"send me a morning briefing"* — i.e. the headline collides
   semantically with the brief's own name, so a bare request would auto-bind
   that one section instead of asking the user which sections they want
   (regressing the clarify UX, caught by
   `test_parse_generic_morning_briefing_clarifies_sections`). The per-domain
   sections already surface the content; the unified plan lives in `iris plan
   today` and the interactive "plan my day" turn. A brief headline can return
   later with a matcher that excludes synthesis slots.

## Alternatives considered

- **LLM authors the plan from raw data.** Rejected — violates
  deterministic-first; an LLM inventing the plan is untrustworthy. The LLM
  only narrates the already-assembled plan.
- **Replace the factual list with the narrative.** Rejected — keeping the list
  appended guarantees the user always sees the exact, correct items even if the
  narrative drifts.
- **LLM narrative in the brief too.** Rejected — the brief is a deterministic,
  cheap daily render; an LLM call per slot adds latency/cost. The brief shows
  the deterministic ranked list; the narrative is for the interactive
  "plan my day" turn.
- **Drop the per-domain brief sections in favour of the unified plan.**
  Rejected — they carry detail (attendees, amounts) the one-line plan items
  don't; the plan is the headline, the sections are the detail.
- **Bundle ResponseCurator wiki/related-Qs enrichment here.** Deferred — that's
  a cross-cutting *curator* feature benefiting all agents, not planner-specific;
  it belongs in its own change, not stapled to the Planner. Tracked separately.

## Consequences

**Positive:**
- "Plan my day" reads like an assistant ("start with the overdue taxes, then
  your 10:00 standup…") while every fact stays grounded and verifiable.
- Robust: deterministic fallback on any LLM failure; empty plans skip the LLM.
- The Planner agent is feature-complete (P0 synthesis → P1 execution/routing →
  P2 grounded narrative).

**Negative / deferred:**
- The narrative is a Tier-1 call on the interactive planning turn (one extra
  LLM call); acceptable for an explicit "plan my day" request, and it degrades
  gracefully.
- ResponseCurator wiki/related-Qs enrichment remains unbuilt (separate concern).
- The handler still plans "today" only; date-scoped/NL planning ("plan my
  week") is future work.

## Related ADRs

- ADR-0044 (deterministic plan — what's narrated), ADR-0045 (planner agent
  type / parallel execution), ADR-0035 (deterministic-numbers principle this
  extends), ADR-0029 (brief slots).
