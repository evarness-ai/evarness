# ADR-0045: Planner P1 — parallel wave execution + a registered planner agent type

Status: Accepted (2026-06-14)
Context: Planner agent P1. Builds on ADR-0044 (deterministic daily plan) and the existing AgenticCore pipeline (intent router, `TaskPlan` DAG, agent executor, response curator). Two gaps from the P0 survey: plan execution was sequential, and there was no agent the router could send "plan my day" to.

## Decision

1. **Parallel execution by wave.** `AgentExecutor.execute_wave(tasks)` runs the
   tasks of one dependency wave **concurrently** (a bounded `ThreadPoolExecutor`,
   default 4 workers), preserving input order in the results;
   `execute_waves(waves)` runs waves in order. `_execute_plan` now drives
   `TaskPlan.execution_groups()` (the topological sort that already existed but
   was unused) through `execute_wave` per wave. A **single-task plan — the
   common case — runs inline with no thread**, so the hot path is unchanged.

2. **Threads, not asyncio.** Agent handlers are sync callables
   (`Callable[[AgentTask], HandlerResult]`); a thread pool parallelises them
   without an async refactor of the handler contract. `execute` already
   captures handler exceptions into `AgentResult`, so one task's failure can't
   abort its siblings, and the pool never sees a raised exception.

3. **Bounded concurrency.** Default 4 workers, capped at the wave size. A local
   single-model LLM serialises calls anyway, so a large pool buys little; the
   win is overlapping the *I/O* of independent branches (e.g. email + calendar
   + finance reads). Concurrency is conservative on purpose.

4. **A registered `planner` agent type.** `iris_harness.planner.build_planner_handler`
   returns an `AgentExecutor` handler that renders today's deterministic
   `DailyPlan` (P0) as text; bootstrap registers it as `planner`. The intent
   router gains a `planner` intent + agent type (in `_VALID_INTENTS`,
   `_VALID_AGENT_TYPES`, `_INTENT_TO_AGENT`), a keyword rule (`plan my day`,
   `what's on today`, `what should I focus on`, `priorities`) ordered **before**
   the time/date rule, and a mention in the LLM-router prompt.

## Alternatives considered

- **Async refactor of the handler contract.** Rejected — handlers are sync and
  widely registered; threads parallelise them with no contract change.
- **Result-passing between dependent sub-tasks.** Deferred — the pre-existing
  executor didn't thread predecessor outputs into dependent prompts either;
  P1 preserves that and only adds *concurrency within a wave*. Cross-task
  context handoff is future work (P2+).
- **Large/!unbounded worker pool.** Rejected — local LLM contention makes it
  pointless and risks resource pressure; 4 is plenty for branch I/O overlap.
- **No router changes (handler only).** Rejected — without a `planner` intent
  the router can't send planning queries to the handler; the keyword rule is
  the user-facing hook.

## Consequences

**Positive:**
- Independent multi-step branches overlap (latency win) with no change to the
  single-task hot path; topological `execution_groups()` is finally used.
- Failures are isolated per task; results stay deterministically ordered.
- "Plan my day" now routes to the planner agent and returns the composed
  daily plan in chat.

**Negative / deferred:**
- True parallelism only helps when a wave has independent tasks *and* they're
  not all blocked on one local LLM; for LLM-bound waves the win is marginal.
- No cross-task result handoff yet (a dependent task doesn't see its
  predecessor's output in its prompt).
- The planner handler ignores the query text (always "today"); date-scoped or
  natural-language planning is P2.

## Related ADRs

- ADR-0044 (deterministic daily plan — what the planner handler renders),
  PRD-04 (AgenticCore pipeline composed here). P2 adds the LLM plan narrative
  + curator enrichment.
