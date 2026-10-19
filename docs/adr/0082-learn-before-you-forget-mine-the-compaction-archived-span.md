# ADR-0082: Learn before you forget (mine the compaction-archived span)

> Source: section `### ADR-0082: Learn before you forget (mine the compaction-archived span)` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: When conversation compaction (ADR-0079) archives a span of old turns,
capture that span so the behavior miner (ADR-0080, digital-twin L1) mines it
**specifically**, rather than relying solely on the daily tick's 80-turn recency
window. `CompactedHistory` now exposes `archived_turns`; `_record_turn` buffers them
into a bounded in-memory `_compaction_archive_buffer` (cheap, no LLM, only when the
miner is enabled); and `_run_behavior_mining` drains the buffer and mines it as its
**own** pass before dedup + propose. A separate pass is required because
`render_activity` keeps only the tail of the transcript (`text[-max_chars:]`), so
folding the older archived span into the recent window would truncate it away. The
mining stays on the heartbeat — `_record_turn` only buffers — so the hot chat path
takes no LLM cost.

**Rationale**: The behavior miner reads the last ~80 turns. In a long or very active
session, the oldest turns — often the richest for spotting a habit — scroll out of
that window before the daily tick runs, and are never mined. Compaction is the exact
moment the harness decides those turns no longer fit the working context; it is
therefore the natural trigger to extract durable learning from them *before* they
leave. This closes the loop between the two big context-management features: the same
event that evicts a span for forgetting now also feeds it to the part of the brain
that learns. The turns themselves are never lost (they persist in the conversation
store + semantic index); what this fixes is the miner's *blindness* to spans that
have aged past its window. Bounded buffer, propose-only, off unless the miner is on —
no new always-on cost. Full design: `docs/architecture/digital-twin.md` (L1 note) +
`docs/architecture/memory-subsystem.md` (compaction note); narrative
`docs/blog/2026-06-28-learning-from-what-you-forget.md`.

---
