# ADR-0084: Act on context-health (on-demand compaction)

> Source: section `### ADR-0084: Act on context-health (on-demand compaction)` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Make the context-health surface (ADR-0081) *actionable*, not just
observable. The compactor already fires **eagerly** at every turn boundary (in
`_record_turn`), so the conversation is never carried over-full into the next turn —
the gap was never "compaction waits too long," it was that there was no way to *act*
on the pressure the surface reports. So: `ConversationCompactor.compact(turns,
force=True)` summarizes the older span even below the auto trigger (labelled
`trigger="manual"`, and still a no-op when there's nothing old enough to archive);
`IrisRuntime.compact_now(session_id)` force-compacts and applies the result through a
new shared `_apply_compaction` helper (extracted from `_record_turn`); surfaced at
write-gated `POST /context-health/compact` and a **Compact now** button on the web
Context-budget panel. The GET snapshot stays open.

**Rationale**: A dashboard you can only read is half a tool. The previous feature gave
the user a clear view of window pressure, last compaction, eviction, and suppression —
and then nothing to *do* about it. On-demand compaction closes that: see the window at
85%, reclaim it with one click; deliberately checkpoint a long conversation below the
threshold; (later) let the agent self-compact when it senses pressure. It's a thin
addition because the machinery already existed — the only new primitive is "compact
even though the auto trigger hasn't fired," which is a `force` flag plus a shared apply
path that the reactive turn-boundary compaction and the on-demand control now both use.
Write-gated (`IRIS_WEBUI_ALLOW_WRITES`) like the other control mutations. Full design:
`docs/architecture/memory-subsystem.md` (context-health control); narrative
`docs/blog/2026-06-28-a-dashboard-you-can-press.md`.

---
