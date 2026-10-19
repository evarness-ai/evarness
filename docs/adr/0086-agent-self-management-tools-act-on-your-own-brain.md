# ADR-0086: Agent self-management tools (act on your own brain)

> Source: section `### ADR-0086: Agent self-management tools (act on your own brain)` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Expose the harness's own brain controls — built up across ADRs 0079–0085
as user-facing CLI / API / web surfaces — to the **agent itself** as three ReAct tools:
`context_health` (introspect this conversation's window fill + last compaction +
suppression), `compact_context` (summarize older turns to reclaim window space when near
full), and `learning_status` (read which miners are on + the accept/reject tallies). So
IRIS can reason about and manage its own state mid-conversation — *"I'm running low on
context, let me compact"* — instead of only the user being able to. The tools are built
per turn inside `_core_for`, capturing the live `session_id`, and reach the runtime via a
1-slot holder filled after `IrisRuntime` is constructed (the runtime is built *after* the
handler, so the reference is late-bound). They participate in the relevance shortlist, so
they only surface on self-referential turns and don't crowd a normal tool menu. Opt-in via
`IRIS_AGENT_SELF_MANAGEMENT`, off by default.

**Rationale**: Every brain capability so far had a human on the lever — *you* check
context-health, *you* hit "compact now", *you* watch the acceptance rate. But the entity
best placed to notice "my context is filling and I'm about to lose the thread" is the
agent in the middle of the turn, not a user reading a dashboard. Giving it the same
controls as tools closes that loop: observability and action it already exposes to the
user become things the agent can do for itself. The wiring is deliberately thin — no new
mechanism, just three closures over the existing `context_health` / `compact_now` /
`learning_flags` methods — and deliberately gated: it adds tools to the loop and one of
them mutates the conversation, so it stays opt-in until the user wants their assistant to
self-manage. Off by default keeps agent behaviour unchanged unless asked. Full design:
`docs/architecture/memory-subsystem.md` (self-management note); narrative
`docs/blog/2026-06-29-an-agent-that-minds-itself.md`.

---
