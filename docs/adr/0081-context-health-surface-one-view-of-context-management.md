# ADR-0081: Context-health surface (one view of context management)

**Status pass (2026-10-18):** the decision holds; the code now lives in `agent/context_health.py`, `runtime/session_memory.py`, `cli/context_health.py`.

> Source: section `### ADR-0081: Context-health surface (one view of context management)` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Surface, in one place, how the harness bounds its working context.
Three mechanisms previously each kept their own counsel: the conversation
compactor (ADR-0079) bounds the multi-turn history to ~80% of the model window;
the in-loop `ContextBudgetController` (ADR-0077 P3) splits a per-turn budget
between the running transcript and the durable-memory block, evicting the
oldest/lowest-value parts; and the surface-feedback spine (ADR-0078) suppresses
proactive noise. Each logged; none was observable together. The new
`IrisRuntime.context_health(session_id)` composes a single snapshot — window fill %
+ last compaction (tracked per session in `_record_turn`), the window-derived
transcript/memory budget split + the latest in-loop prompt size and transcript
eviction (fed by a lightweight `budget_observer` the `AgenticCore` calls each
iteration, writing into a shared sink the runtime reads), and the suppression
roll-up (`SurfaceFeedbackStore.summary()` → active-suppression counts by
subsystem). The pure composition lives in `src/iris_harness/core/context_health.py`;
surfaces are `GET /context-health`, `iris context-health` (renders the live API),
and a Context-budget panel on the web Health screen (thin renderer).

**Rationale**: "Keep only what's needed, evict the rest" was implemented across
three subsystems but invisible — you couldn't answer "how full is the window right
now, what did the last compaction drop, how much is the loop evicting, how much am
I suppressing?" without reading logs across three packages. Making the brain's
memory management measurable in one read is the natural capstone to the
compaction/suppression/learning-activation work: it turns a set of internal
guardrails into something the user (and the agent) can watch and reason about. The
budget observer is deliberately decoupled (a callback into a shared dict, fired
before the kernel check so it works on the legacy no-kernel path too) so the hot
loop takes only a dict write per iteration. Wired to the primary system react loop;
the niche email loop is intentionally excluded to keep the budget split
unambiguous. Full design: `docs/architecture/memory-subsystem.md` (context-health
note); narrative `docs/blog/2026-06-28-one-look-at-the-brain.md`.

---
