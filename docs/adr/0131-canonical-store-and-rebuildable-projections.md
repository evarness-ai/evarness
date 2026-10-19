# ADR-0131: Canonical store + rebuildable projection is a standing rule, not a one-off fix

Status: Accepted (2026-10-05)
Relates to: ADR-0105 (user-fact store coherence — the worked example this ADR
generalizes), `docs/architecture/memory-store-registry.md`, `docs/oss-plan-review.md`,
the L4.5 Harness Hardening milestone.

## Context

IRIS persists memory across several representations: SQLite stores, five ChromaDB
collections (`iris_user_facts`, `iris_learning_signals`, `iris_conversation_turns`,
`iris_wiki_pages`, `iris_episodic_patterns`), the Memris graph, and
identity-file projections (`USER.md`'s auto-detected block, `episodic.md`). An external
architecture review asked the right question: which of these is canonical, and which
are rebuildable projections IRIS can delete and reconstruct without data loss?

The honest answer, checked against the code rather than assumed, is: **this project
already has the right architecture**, it was just never stated as a general rule.

- **Facts**: ADR-0105 made this explicit and shipped it. `memory.db` (SQLite) plus
  `user_fact_history` is canonical; `FactCoordinator` is the single write-seam;
  `SemanticIndex.reconcile_facts()` and `identity/loader.py::reconcile_user_facts_md()`
  rebuild the ChromaDB and `USER.md` projections from the store, dropping orphans, not
  just adding; `iris memory doctor` is the standing health check plus `--repair`.
- **Signals and turns**: `SemanticIndex.sync_from_store()` rebuilds both from
  `MemoryStore` at startup (turns incrementally, via a watermark).
- **Episodic patterns**: `SemanticIndex.sync_episodic_patterns()` reconciles the
  `iris_episodic_patterns` collection against `episodic.md`, including dropping stale
  ids — the same shape as the facts path.
- **Wiki**: `memory/knowledge/wiki_index.py` owns its own rebuild/reindex path,
  independent of the above (confirmed by the existing startup-sync test in
  `tests/unit/iris_harness/memory/test_knowledge/test_wiki_engine.py`).
- **Memris**: is itself the canonical store (a `GraphStore` contract over SQLite);
  nothing in `src/iris_harness/memory/` derives a second, separate vector projection
  from it that would need its own reconciliation.
- **Open question, not yet resolved**: `src/iris_harness/services/rag/index.py` (the
  document-RAG ChromaDB index) has no explicit rebuild/reindex function in that module.
  It may already be de facto rebuildable — re-running a connector's ingest is likely
  idempotent per source — but that has not been verified the way the five memory
  collections above were for this ADR.

## Decision

1. **State the rule explicitly, project-wide**: every persisted representation of
   memory is either the canonical record (has provenance, is the thing a human or
   `iris memory doctor`-style tool can audit and correct) or a projection that can be
   deleted and rebuilt from a canonical source with no data loss. A new store added to
   `iris_harness/memory/` (or any future sibling) must say, in its own module docstring
   or an ADR, which one it is and point at its rebuild path if it's a projection.

2. **ADR-0105 is the reference implementation of this rule**, not a one-off fix to a
   single reported "store sprawl" concern. Point future reviewers here first.

3. **Close the one open question**: verify `services/rag/index.py`'s rebuildability
   (confirm each connector's ingest path is safely re-runnable from scratch, or add an
   explicit rebuild entry point if it isn't) as a fast-follow under the L4.5 milestone.
   This is a verification task, not a redesign — if ingestion is already idempotent,
   the fix is documenting that fact next to the index, not writing new code.

## Alternatives considered

- **Audit and "fix" every store as if a new gap exists.** Rejected after checking the
  code: the five memory collections already satisfy this rule. Treating a sound
  architecture as broken because the general principle was never written down would
  have produced redundant, directionless work.
- **Leave the principle implicit, since the code already does the right thing.**
  Rejected: an unstated invariant is not enforceable against future additions. The
  review's real finding is that `docs/architecture/memory-subsystem.md` has no
  canonical-vs-projection statement at all — this ADR is that statement.

## Consequences

- Positive: a one-sentence test any future store addition must pass
  ("canonical, or rebuildable from what?"), backed by five working examples instead of
  a single abstract principle.
- Negative: none identified beyond the RAG-index verification task, which is small.
- Reversible: this is a documentation/process ADR; it changes no code by itself.

## Files

- `docs/architecture/memory-subsystem.md` — should cross-reference this ADR directly.
- `docs/architecture/memory-store-registry.md` — the per-store registry ADR-0105
  introduced; this ADR is the rule that registry exists to serve.
- `src/iris_harness/services/rag/index.py` — subject of the fast-follow verification.
