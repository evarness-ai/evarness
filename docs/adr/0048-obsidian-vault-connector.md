# ADR-0048: Obsidian vault connector — front-matter, wikilinks, incremental sync

Status: Accepted (2026-06-14)
Context: RAG agent R1. Builds on ADR-0047 (document RAG foundation). Realises the user's "port your Obsidian vault / don't duplicate the engine" goal: make IRIS understand the markdown conventions an Obsidian vault adds over a flat folder.

## Decision

1. **Enhance markdown ingestion, don't fork a separate code path.** Any
   markdown file may carry YAML front-matter / `[[wikilinks]]` / `#tags`, so
   the existing `ingest_path` now always parses them via pure functions in
   `iris_harness.rag.obsidian` (`parse_note` → title, stripped body, tags, links). An
   Obsidian vault gets the full value; plain markdown is unaffected. `kind`
   stays a label (`obsidian` vs `folder`), not a branch.

2. **Front-matter informs metadata, not the index body.** The YAML fence is
   stripped before chunking (it isn't content); `title` (or first `alias`,
   else filename) becomes the source/citation title; `tags` are stored.

3. **Wikilinks are the vault graph.** `[[Target|alias]]` / `[[Target#heading]]`
   resolve to `Target`; stored as the source's outgoing `links`. A compact
   `tags: … | links: …` **context line is embedded into chunk 0**, so a note
   is retrievable by its tags and the notes it links to — the graph improves
   recall without a separate graph store.

4. **Incremental sync by mtime, then content hash.** Sources gain `tags`,
   `links`, `mtime` columns (added **additively** via guarded `ALTER TABLE` so
   existing R0 `rag.db` files migrate). Re-sync skips a file whose mtime is
   unchanged **without reading it** (cheap over large vaults); if mtime moved
   but the SHA-256 is identical, it refreshes mtime and skips reindex; only a
   real content change re-chunks.

5. **Surface the graph.** `iris docs list` shows tags; `iris docs related
   <path>` prints a note's tags + outgoing links.

## Alternatives considered

- **A separate `ingest_vault` path / Obsidian-only parser branch.** Rejected —
  front-matter/wikilinks are valid in any markdown; one enhanced path is
  simpler and benefits every `.md` source.
- **Merge vault notes into the `iris_wiki_pages` collection.** Rejected
  (ADR-0047) — user documents are external source material with their own
  sync/removal lifecycle, distinct from the auto-synthesised wiki. Wikilinks
  are preserved as document cross-refs, not wiki pages.
- **A real link graph table + graph traversal at query time.** Deferred —
  embedding the context line into chunk 0 captures most of the recall benefit
  with no new store; a traversable graph can come later if needed.
- **Resolve wikilinks to concrete note ids at ingest.** Deferred — targets are
  kept as names; resolution (and backlinks) is a future enhancement.

## Consequences

**Positive:**
- Pointing IRIS at an Obsidian vault now yields proper titles, tags, and
  link-aware retrieval — the vault is more than a flat folder.
- Large-vault re-sync is cheap (mtime fast-path); migration is automatic.
- Pure parsers → fully unit-tested with no filesystem.

**Negative / deferred:**
- Wikilinks aren't resolved to note ids / backlinks yet (names only).
- No live file-watching; sync is explicit (`iris docs sync`).
- Nested/array front-matter beyond title/tags/aliases is ignored.
- PDF/other formats remain R2; grounded cited Q&A + MCP exposure remain R3.

## Related ADRs

- ADR-0047 (document RAG foundation this extends), ADR-0025 (the synthesised
  wiki — distinct from these user documents), ADR-0035 (deterministic-first —
  pure parsers + keyword fallback).
