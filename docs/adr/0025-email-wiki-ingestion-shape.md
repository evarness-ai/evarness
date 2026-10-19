# ADR-0025: email → wiki ingestion shape

Status: Accepted (2026-05-26)

**Status pass (2026-10-18):** the decision holds; the code now lives in `email_workflows/wiki_ingestion.py` and `evarness/memory/knowledge/event_subscribers.py`. the two hops were split as planned

Context: Personal-assistant upgrade Phase 1 Track 1K. Parents: [ADR-0004](./0004-reuse-wiki-for-cross-domain-correlation.md) (wiki as cross-domain correlation engine), [ADR-0021](./0021-email-triage-skill-shape.md) (email.classified event). Companions: [ADR-0010](./0010-entity-extractor-tightening-not-rewrite.md) (entity extractor precision), [ADR-0022](./0022-resource-aware-triage-revision.md) (pure-kNN triage emits event).

## Context

ADR-0004 named the wiki/knowledge subsystem as the cross-domain correlation engine and called for domain skills to emit `WikiIngestEvent` so finance, calendar, and email all funnel facts into the same graph. Track 1G's `email.classified` event is the email-side signal; the wiki side has `WikiEngine.ingest(WikiIngestEvent)` (`src/iris_harness/knowledge/wiki_engine.py:115-164`) which extracts entities and upserts markdown pages under `data/wiki/entities/`.

Until now there's been NO bridge between the two — classified emails fire `email.classified` and nobody listens for wiki purposes. Track 1K builds the bridge. All the heavy lifting (entity extraction, page CRUD, ChromaDB indexing) already exists; this track is wiring + content shape decisions.

Eight non-obvious implementation calls had to be locked:

1. Where the subscriber lives on disk.
2. One-hop or two-hop architecture (`email.classified` → `WikiEngine.ingest` directly vs through a `WikiIngestEvent` indirection).
3. What text content goes into the event.
4. What entity hints to inject.
5. The `source_id` shape (account-qualified or flat).
6. Whether `iris email recategorize` (Track 1J) should re-fire `email.classified` so wiki picks up corrections.
7. Backfill: existing classifications have no wiki pages — do we walk them?
8. Index-rebuild cost — `WikiEngine.ingest` rebuilds the ChromaDB index on every call; is that a problem for the backfill?

## Decision

Eight implementation decisions:

1. **Subscriber lives at `src/iris_harness/email/wiki_ingestion.py`.** Co-located with `triage.py` and the other email-side subscribers. The function `subscribe_email_classified_to_wiki(bus)` mirrors `subscribe_email_triage`. Rejected: `src/iris_harness/knowledge/email_ingestion.py` (puts email-specific shaping inside the wiki subsystem, which violates the cross-cutting-engine design — wiki shouldn't know about email field names). Rejected: a new skill folder under `config/skills/` (the bridge is purely event plumbing, no agent-facing tool surface).

2. **Two-hop architecture.** The email subscriber emits `WikiIngestEvent` on the bus; a separate generic `WikiIngestEvent` consumer (also in `iris_harness.email.wiki_ingestion` for v1, factor out to `iris_harness.knowledge.event_subscribers` later) calls `WikiEngine.ingest`. Cost: one extra event dispatch (~µs). Benefit: when finance ingestion lands (Phase 3), it emits the same event; ONE wiki consumer handles both. Matches ADR-0004's stated pattern ("domain skills emit WikiIngestEvent"). Rejected: one-hop direct call — couples email subscriber to WikiEngine and prevents cross-domain reuse.

3. **Content: `f"{subject}\n\n{snippet}"`.** Both fields are already persisted in `email.db` per canonical §3.1. No Gmail re-fetch. ~400 chars per email — well above `WikiEngine.ingest`'s 50-char minimum. Rejected: re-fetching the full body from Gmail (quota cost; bodies were explicitly NOT cached per canonical §3.1).

4. **Entity hints: `[from_domain, root, branch, leaf]`.** All four are pre-known facts about the email (from the classification). Per the entity extractor's precision design (ADR-0010 §3), hints take priority at 0.95 confidence vs 0.6-0.8 for regex-discovered entities. Hinting the category hierarchy means the wiki page for "shopping/apparel/outlet-brand" gets surfaced as an entity that this email belongs to. Rejected: no hints (loses signal from the classification we worked hard to produce).

5. **`source_id` = `f"email/{account_id}/{message_id}"`.** Account-qualified for cross-account isolation. Re-classifications of the same email use the same source_id, so `WikiEngine.ingest`'s upsert semantics naturally update the page's "Recent Activity" section in place. Rejected: flat `email/{message_id}` — Gmail ids are globally unique in practice but the principle of explicit account-scoping matches the rest of the email subsystem.

6. **`iris email recategorize` emits `email.classified`.** When the user corrects a classification (Track 1J), the wiki picks up the correction automatically because the same event fires with the new category. Both subscribers (triage event-driven flow + wiki-ingestion) hear it; triage's idempotent mark-classified is a no-op when the row is already classified, and wiki's append-to-Recent-Activity tracks the correction as a new note. Rejected: stale wiki (bad UX); rejected: a new `email.recategorized` topic (two events for the same downstream concern).

7. **Backfill via `iris email reingest-wiki --account <slug> [--since DATE]`.** Walks `emails WHERE classified_category IS NOT NULL AND account_id = ?` and emits `email.classified` for each, with the SAME EmailClassifiedPayload shape as the auto-fire path. The wiki subscriber treats backfill rows the same as live ones — no special path. CLI exits 3 if nothing to backfill. Rejected: skip backfill (loses the existing 15 classifications' value); rejected: implement backfill as a one-shot Python script (open-source surface should be CLI-uniform per the convention from Track 1L's spike-script retirement).

8. **Accept the index-rebuild cost for Phase 1.** `WikiEngine.ingest` rebuilds the ChromaDB index on every call (`self._index.rebuild(self._pages.load_all())` at line 127). For a 15-email backfill that's 15 rebuilds. ChromaDB rebuild is O(pages); on a fresh wiki it's <1 page→fast, but after backfill ~30-100 pages → ~100ms each. Acceptable for the one-time backfill cost. A future `WikiEngine.ingest_batch` with deferred reindex would optimize; out of scope for Track 1K. Document the limitation in the writeup so future readers know.

## CLI surface

```
iris email reingest-wiki --account <slug>
  [--since <iso>]     Only re-emit for emails classified after this point
  [--limit N]         Cap (default 200 to match other email CLIs)
  [--db-path / --email-db]
```

Re-emits `email.classified` for every classified email matching the filters. Idempotent: re-running adds duplicate "Recent Activity" entries on each page (we treat this as audit, not bug — the user can see the timeline of when ingestions happened). A future "snapshot dedup" feature can squash duplicates if it becomes a problem.

## Consequences

- Every email triaged from now on automatically produces wiki pages for the entities mentioned (sender domain, brand names, mentioned people, etc.). The wiki becomes the cross-domain correlation engine ADR-0004 designed for.
- `WikiIngestEvent` becomes a shared event topic. Finance ingestion (Phase 3) emits the same; the wiki-side consumer doesn't change.
- The 50-char minimum in `WikiEngine.ingest` will skip very short emails (1-line confirmations). Acceptable for v1; future tracks can add metadata-only ingestion if needed.
- ChromaDB index rebuild on every ingest is a Phase 1 acceptable tax. Backfill of 15 emails runs once; live ingestion is one rebuild per email. Track 1I (richer kNN store) can subsume the per-ingest rebuild into a more efficient incremental index.
- `iris email recategorize` becoming a publisher of `email.classified` means it's no longer a purely-local mutation; wire it through `bus.emit_sync` exactly like `EmailTriageClassifier.classify_and_persist` does.
- Backfill CLI emits events synchronously; with the wiki consumer subscribed on the singleton bus, the user sees backfill happen page-by-page. Acceptable UX given one-shot nature.

## Alternatives considered

- **Have `WikiEngine.ingest` directly accept an `EmailClassifiedPayload`.** Simpler call site. Rejected: forces wiki to know email-specific field names; breaks cross-domain reuse when finance ingestion lands.
- **Use the new `email.classified` event topic for the WikiIngestEvent dispatch (publish twice on the same channel with different payloads).** Confusing — one topic shouldn't carry two payload shapes.
- **Add a "wiki-page-id" column to `emails` so we can later track which email → which page.** Tempting for diagnostics. Rejected for v1: redundant with `source_id` in the wiki page's frontmatter; query path is `wiki page → reverse-lookup source_id` not the other way. Add only if cross-table joins prove necessary.
- **Skip ChromaDB indexing on backfill.** Faster backfill. Rejected: leaves the index stale; the user runs `iris query "..."` and gets misses. Better to pay the indexing cost once than debug a stale-index symptom later.
- **Use a heartbeat to batch ingestion (drain pending classifications every N minutes).** Sounds good for throughput. Rejected: classification volume is low (10/day for one user); the event-driven path is correct semantics and the user gets fresh wiki pages immediately.
