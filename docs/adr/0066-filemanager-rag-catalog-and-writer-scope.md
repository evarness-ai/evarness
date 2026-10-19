# ADR-0066 — RAG documents in the catalog; custody scope for other writers (FMX4)

- **Status:** Accepted
- **Date:** 2026-06-18
- **Phase:** FMX4 (final convergence phase)
- **Builds on:** ADR-0062 (custodian + catalog), ADR-0063 (finance custody),
  ADR-0064 (media indexer)

## Context

FMX1 migrated finance — the one subsystem that *downloads and stores external
documents* — onto the custodian. FMX4 was scoped to "migrate the other file
writers (RAG, calendar, reminders, wiki) onto custody too." Before doing so we
audited what each actually writes:

| Subsystem | Writes | Nature | Verdict |
|---|---|---|---|
| **RAG** | `data/rag.db`, `data/chroma_docs/` | embeddings/metadata; **reads user docs in place, never copies** | not custody — but the *documents* should be in the catalog |
| **Calendar** | `data/calendar/ics/*.ics` | generated, **rewritten** each update | not custody |
| **Reminders** | `~/.iris/reminders/*.md`, `*.ics` | generated, rewritten; `~/.iris` is **denylisted** | not custody |
| **Wiki** | `data/wiki/<type>/*.md` | generated knowledge pages, rewritten on save | not custody |

Byte-custody is for **immutable, content-addressed external documents with
provenance + retention** (the finance model). Forcing mutable, regenerated
artifacts — or the agents' own SQLite/Chroma datastores — into content-addressed
custody would be wrong: it fights their mutability and buys nothing.

## Decision

1. **RAG documents are registered in the catalog (not custodied).** RAG reads
   the user's files in place, so the correct model is an **in-place observer
   entry**, exactly like the FMX2 indexer. `ingest_path(..., catalog=…)` now,
   for each indexed file, calls `register_rag_document()` which upserts a catalog
   row: `owner_module="rag"`, `location_tier=icloud|os_local`, media kind,
   content sensitivity (scanned), `source="rag"`, `retention_days=0`, and —
   uniquely — RAG's own content hash as the catalog `sha256`. `file_id =
   "rag_"+source_id`. Wired into `iris docs add` and `iris docs sync`.
   Best-effort: registration never breaks ingestion.

2. **Calendar, reminders and wiki are deliberately NOT migrated.** They produce
   mutable, regenerated operational artifacts (and reminders live under the
   denylisted `~/.iris`). They are already correctly architected; custody would
   be an anti-pattern. This is recorded, not deferred-with-intent-to-do.

3. **Agents' own datastores stay put.** `rag.db`, `chroma_docs`, `calendar.db`,
   `memory.db`, `wiki/` etc. are mutable databases/indexes, not documents, and
   remain owned by their subsystems.

## Consequence

The catalog is now the unified authority over every *document* IRIS handles:
finance statements (`managed`, custodied), files on disk + iCloud Drive
(`os_local`/`icloud`, indexed), Apple Photos (`photos`), and **RAG-indexed
documents (`owner=rag`)** — answerable via `iris files catalog ls --owner rag`.
Operational artifacts and datastores are intentionally out of scope.

**Caveat (accepted):** a file that is *both* RAG-indexed *and* under an allowed
root will have two catalog rows — a `rag_…` entry and an `os_…` indexer entry —
representing two facts ("RAG indexed this" vs "this exists on disk in a root").
In practice RAG sources (e.g. an Obsidian vault) and FileManager roots are
usually disjoint. A future unification (a per-file row with multiple
referencing subsystems) is possible but out of scope.

## Verification
- `tests/unit/test_rag/test_catalog_sync.py`: registers a doc (owner=rag, media
  kind, content sha carried), classifies a secret doc, idempotent re-ingest,
  and no-op without a catalog. RAG + filemanager suites: 167 pass; ruff/black/
  mypy clean.

## Convergence complete
FMX0–FMX4 land the program: FileManager owns external-document bytes (finance),
and its catalog is the single index across managed, on-disk, iCloud, Photos and
RAG documents — with retention, sensitivity, and password awareness throughout.
