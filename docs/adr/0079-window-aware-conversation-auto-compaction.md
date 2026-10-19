# ADR-0079: Window-aware conversation auto-compaction

> Source: section `### ADR-0079: Window-aware conversation auto-compaction` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: Make the `ConversationCompactor` (`src/iris_harness/memory/compactor.py`)
fire on a **token/window trigger**, not just a turn count. It now carries a
`token_budget` — the chat tier's window-derived prompt budget,
`budget_for(num_ctx)` for the `communication`/Tier-2 model — and compacts once the
running history reaches `compaction_ratio` (default **0.8**) of it. Whichever fires
first wins: the old `compaction_threshold` (20 turns) stays as a count floor for
tiny-turn chats, but the token trigger is the load-bearing one for real sessions,
where a handful of large turns fills the window long before 20 turns accrue. On
compaction the oldest turns are LLM-summarized (governance-wrapped, with an
extractive fallback) and the most-recent turns are kept verbatim, **bounded by a
token sub-budget** (~half the trigger) rather than a fixed count — so even a single
huge turn compacts while at least one recent turn is always preserved. Empty /
whitespace-only turns are dropped from the summary input ("keep only the last
*good* turns"). The compactor is built window-derived in `build_runtime` from the
live `tier_router`; env overrides win (`IRIS_COMPACTION_{TOKEN_BUDGET,RATIO,
THRESHOLD,KEEP_RECENT}`). Each event logs its telemetry (`trigger`, `archived`,
`tokens before->after`, `kept_turns`).

**Rationale**: This is the cross-turn twin of the ADR-0077 P3 in-loop
`ContextBudgetController` — same window-derived discipline, applied to the
multi-turn conversation instead of a single ReAct transcript. The prior count-only
trigger treated two terse turns and twenty long ones identically: a token-heavy
session (pasted logs, long answers) could silently overrun `num_ctx` and let Ollama
truncate the system prompt before 20 turns were ever reached. Anchoring compaction
to ~80% of the *actual* model window — the same heuristic Claude Code uses for
auto-compact — keeps the live context bounded (measured: an 8k-window session holds
a steady ~2.5k-token sawtooth, compacting the oldest turns each time it crosses the
4,915-token line) and stays model-agnostic: swap in a bigger-context model and the
trigger scales automatically. It also advances the broader "garbage in / garbage
out" goal — summarize and evict stale turns, preserve durable facts in the memory
stores, keep only what the next turn needs. Full design notes:
`docs/architecture/memory-subsystem.md` (compaction section); narrative
`docs/blog/2026-06-27-compacting-the-context-window.md`.

---
