# ADR-0044: Planner agent foundation — deterministic daily-plan synthesis

Status: Accepted (2026-06-14)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Planner agent P0 (first of the orchestration agent's phases; 4th PAIA agent after Email/Finance/Calendar). The AgenticCore pipeline (intent router, task planner, agent executor, response curator, ReAct loop) already exists and is real — the Planner *composes* the domain agents, it does not reinvent the pipeline.

## Context

Email, Finance, and Calendar are built, and each produces the same currency —
calendar events, tasks (incl. meeting-prep), awaiting-reply followups, and
bills due — but nothing composes them into a single "what does my day look
like / what do I act on" view. The morning brief shows these as *separate*
sections; there is no unified, ranked plan.

The orchestration pipeline (`src/iris_harness/core/*`) is already real (intent
routing, `TaskPlan` DAG with `execution_groups()`, agent executor registry,
response curator). So the Planner's first job is **synthesis**, not new
plumbing.

## Decision

1. **P0 is deterministic.** `iris_harness.planner.build_daily_plan` assembles one
   `DailyPlan` from store reads only — no LLM — mirroring the
   deterministic-first principle Finance (numbers) and Calendar (structure)
   follow. The LLM narrative ("what should I focus on") is a later phase that
   layers *on top* of this plan, so the facts are always trustworthy.

2. **Four sources, one ranked list.** Events (Calendar), open/doing tasks due
   today or overdue (Tasks), awaiting-reply followups (Email followups =
   tasks with `wait_for`), and bills due within a week / overdue (Finance).
   Each becomes a `PlanItem(kind, title, when, detail, overdue)`.

3. **Stable, deterministic ranking.** Sort by `(dated-before-undated, when,
   kind-rank, title)`: chronological, undated items last, and on ties events
   → bills → tasks → followups. No randomness, no model.

4. **Mirror events are de-duplicated.** Because Calendar C3 mirrors tasks/bills
   into the calendar (`source` = `task`/`bill`), the event source is filtered
   to exclude those — otherwise a due task would appear twice (once as its
   calendar mirror, once from the task store). Items come from their
   authoritative store, never the mirror.

5. **Surfaces.** A read-only `daily-plan` skill (`plan_today`, default-on) +
   `iris plan today`. The brief integration and LLM narrative are deferred to
   the phase that adds the narrative (so the brief gets the *synthesized*
   headline, not a fourth copy of the component sections).

## Alternatives considered

- **LLM-first plan ("ask the model to plan my day").** Rejected as the
  foundation — the plan must be grounded in real store state; an LLM narrative
  over a deterministic plan is trustworthy, an LLM *inventing* the plan is not.
  Deferred to P2 as a layer.
- **Re-list the brief's existing sections.** Rejected — the value is the
  unified *ranking* (one ordered list answering "what do I do, in order"), not
  repeating the per-domain sections.
- **A new planner store / persisted plans.** Rejected — the plan is a
  derived view; recomputing from the source stores is cheap and always fresh.
- **Build a new orchestration pipeline.** Rejected — `src/iris_harness/core/*` is
  real; P1 will *compose* it (parallel `execution_groups`, a registered
  `planner` agent type), not replace it.

## Consequences

**Positive:**
- A single prioritized daily view composing three shipped agents, deterministic
  and fully unit-tested (composition, ordering, mirror de-dup, overdue flag,
  followup separation, empty/summary).
- Trustworthy substrate for the P2 LLM narrative and P1 multi-agent execution.

**Negative / deferred:**
- No brief "today's plan" headline yet (lands with the P2 narrative).
- No LLM prioritization/reasoning (P2).
- The bill horizon (7 days) and task window (today/overdue) are fixed
  defaults, not yet user-configurable.

## Related ADRs

- ADR-0005 (tasks/followups), ADR-0039 (finance bills/monitor), ADR-0043
  (calendar mirror — the source of the mirror events de-duplicated here),
  ADR-0029 (brief slots — future planner headline). PRD-04 (AgenticCore) is
  the orchestration pipeline P1 composes.
