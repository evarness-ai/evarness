# ADR-0047: Document RAG foundation — index the user's docs (connector, not owner), cited retrieval

Status: Accepted (2026-06-14)
Context: RAG agent R0 (5th PAIA agent). An audit showed retrieval *capability* already exists (ChromaDB `SemanticIndex`, memory retriever, wiki engine, ReAct `web_search`/`wiki_search`/`memory_search`). The genuine gap is letting users bring their **own** documents (Obsidian vaults, note folders, PDFs) into IRIS and get **cited** answers — without duplicating a knowledge engine they already maintain.

## Decision

1. **Connector model — index, don't own.** The user points IRIS at an
   existing file or folder (`iris docs add <path>`); the files stay the source
   of truth; IRIS chunks + embeds them for retrieval and re-syncs on change
   (`iris docs sync`). No import/copy, no second authoring surface — the same
   stance as the Gmail/gcal connectors. This is what makes "port your Obsidian
   vault / NotebookLM sources" real rather than a migration.

2. **A self-contained `iris_harness.rag` package, separate from the wiki.** Documents
   are user-provided *source material*; the knowledge **wiki** is
   auto-synthesised (entity pages from email, ADR-0025). Keeping them distinct
   avoids conflating the two. `iris_harness.rag` owns: `DocumentStore` (`data/rag.db`
   — sources + chunks + content hash), a markdown-aware `chunker`, a
   `DocumentIndex`, `ingest`, and cited `retrieve`.

3. **A dedicated ChromaDB collection on its own persist dir.**
   `iris_documents` lives at `data/chroma_docs` with its **own**
   `PersistentClient` — not the memory `SemanticIndex` client — so the two
   never contend, and the document layer is independently droppable. Cosine
   space so similarity scores are a sane ~[0,1] for citations.

4. **Citations are first-class.** Every `RetrievedChunk` carries a `Citation`
   (`source_path`, nearest-heading `title`, `chunk_index`) → label like
   `Project X (notes/x.md#2)`. This is the NotebookLM-style requirement and the
   substrate for grounded answers (R3).

5. **Deterministic + graceful degradation.** Chunking is deterministic (no
   model). Retrieval uses the vector index when ready and falls back to a
   deterministic **keyword** search over the store when ChromaDB is
   unavailable (or `IRIS_TEST_NULL_EMBEDDINGS` is set) — so the feature works,
   and is testable, without the 80 MB embedding model.

6. **Idempotent ingestion by content hash.** A source whose SHA-256 is
   unchanged is skipped; a changed file drops its old chunks (store + index)
   and re-chunks. Re-running `sync` is cheap and correct.

7. **Surfaced as a skill + CLI.** A default-on `docs-search` skill
   (`search_documents`, `rag/read`) — which auto-becomes a ReAct tool, so chat
   can ground answers in the user's docs — plus `iris docs add|sync|list|
   search|remove`.

## Alternatives considered

- **Fold documents into the existing wiki / `iris_wiki_pages`.** Rejected —
  the wiki is auto-synthesised IRIS output; user documents are external source
  material with their own lifecycle (sync, removal) and citation needs. R1's
  Obsidian connector *can* preserve `[[wikilinks]]` as cross-refs without
  merging the stores.
- **Reuse the memory `SemanticIndex` client/collection.** Rejected — a second
  collection there couples doc-RAG to memory and risks one client owning
  everything; a separate persist dir is cleanly isolated.
- **Import/copy documents into IRIS.** Rejected — that's the "duplicate engine"
  the user explicitly wants to avoid; connector/sync keeps their tool the
  source of truth.
- **LLM-based chunking / semantic splitting.** Deferred — deterministic
  heading/size chunking is reproducible and dependency-free; good enough for
  R0.

## Consequences

**Positive:**
- Users can bring an Obsidian vault / notes folder and get cited retrieval
  today; the engine they already use stays authoritative.
- Fully offline-testable (keyword fallback) and live-verified on the real
  vector path (relevant note ranked above an unrelated one, with citations).
- Clean base for R1 (Obsidian connector), R2 (PDF), R3 (grounded Q&A + MCP).

**Negative / deferred:**
- R0 ingests only `.md`/`.txt`; PDFs/other formats are R2.
- No `[[wikilink]]`/frontmatter handling yet (R1 Obsidian connector).
- No live file-watching; sync is explicit (`iris docs sync`) or via a future
  heartbeat.
- Grounded cited *answers* (LLM over retrieved chunks) + source/notebook
  grouping + MCP exposure are R3.

## Related ADRs

- ADR-0025 (knowledge wiki / email→entity — the *synthesised* counterpart),
  ADR-0035 (deterministic-first — chunking/keyword fallback), ADR-0043 (stdlib
  MCP server — R3 will expose doc search through it).
