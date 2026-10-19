# ADR-0105: User-fact store coherence — single write-seam + reconcile doctor, no DB merge

Status: Accepted (2026-06-30) — shipped on `feat/memory-store-coherence`
Relates to: ADR-0093 (fact hygiene + full portfolio), the memory subsystem + curation docs, the new `docs/architecture/memory-store-registry.md`

## Context

A review of IRIS's persistence raised a "store sprawl" concern: `ls data/` shows ~17
SQLite files plus several ChromaDB dirs, which looks like it must be hard to keep
coherent. The literal reading of the concern — "merge the databases" — turned out to
be the wrong fix, and tracing the code showed why.

1. **Most stores are deliberately separate, not redundant.** Domain stores
   (`email.db`, `finance.db`, `calendar.db`, `tasks.db`, `routines.db`, …) are owned
   by their subsystems. The governance stores — notably the **two** distinct audit DBs
   (`data/audit.db` for governor/router decisions vs. the user-facing kernel ledger at
   `~/.local/share/iris/audit.db`) — are separate **trust boundaries**. Collapsing any
   of these would couple unrelated lifecycles and break the governance design. They are
   single-owner and were never the problem.

2. **The memory subsystem is already a clean source-of-truth + derived-index shape.**
   A user fact lives in three homes: `memory.db` (SQLite — the **truth** plus the
   reversible `user_fact_history`), the `iris_user_facts` ChromaDB collection (derived
   recall), and the `## Auto-detected` block of `USER.md` (derived, prompt-loaded
   projection). The index already had a rebuild path (`sync_from_store`).

3. **The actual risk is coherence, and the root cause is a write/delete asymmetry.**
   The *write* path (`_persist_fact`) fanned out to all three homes, but every
   *forget / correct / restore* path touched **only the SQLite store** — the `iris facts`
   CLI, the in-chat `memory_forget/correct/restore` tools, all of them. So each
   correction silently desynced the index and `USER.md` from the truth. On top of that,
   `sync_from_store` never dropped orphans (a forgotten fact lived forever in the index),
   and `USER.md` had no reconcile (only a seed-if-absent). This is exactly the drift that
   previously forced the same junk facts to be purged in three places by hand (ADR-0093).

## Decision

Fix coherence, not the file count. No databases are merged.

1. **Single write-seam.** `memory/coordinator.py::FactCoordinator` owns every fact
   mutation (`record` / `forget` / `correct` / `restore`) and fans out to all three
   homes. The store write is authoritative and runs first; derived-home writes are
   best-effort (logged, never raised) so a Chroma/markdown hiccup cannot lose a fact.
   The pre-existing partial call sites — `_persist_fact`, the in-chat memory tools, and
   the `iris facts correct/forget/restore` CLI — now all route through it.

2. **Make the derived homes fully rebuildable.** `SemanticIndex.reconcile_facts()`
   re-indexes from the store *and drops orphans*; `loader.reconcile_user_facts_md()`
   rewrites the `## Auto-detected` block to exactly match the store (preserving the
   user's hand-curated head/tail). Repair is strictly one-way — it never mutates the
   truth — so it is safe to run anytime.

3. **A coherence doctor.** `memory/coherence.py` diagnoses drift (index/USER.md missing
   vs. orphan vs. value-mismatch, excluding human-curated facts) and repairs it.
   `iris memory doctor` is the CLI: read-only by default, exits non-zero on drift (so it
   doubles as a health check), `--repair` reconciles the derived homes.

4. **A store registry.** `docs/architecture/memory-store-registry.md` documents every
   store's owner, trust domain, and truth/derived status — so the breadth stays legible
   and the "don't merge these" boundaries are explicit.

## Consequences

- A forget/correct in chat or CLI now leaves no stale copy in recall or the prompt — the
  ADR-0093 class of bug cannot recur silently, and any residual drift is detectable and
  repairable with one command.
- Repair can never lose a fact or its history (truth is read-only to it).
- Found + fixed a latent bug while testing the real surface: `sync_user_facts_to_md`
  created the legacy `memory/` dir but wrote to `workspace/USER.md`, crashing on a
  missing parent when called outside startup. Now ensures the target dir.
- Not addressed (deliberately, out of scope): index *value* drift is detected only via
  the markdown leg today (the key-diff doesn't compare embedded documents); `repair`
  fixes it regardless by re-indexing. A periodic background reconcile (vs. on-demand
  `doctor`) is a possible follow-up.
- No change to domain or governance stores. The "sprawl" was mostly correct separation;
  this ADR records that finding so it is not re-litigated.
