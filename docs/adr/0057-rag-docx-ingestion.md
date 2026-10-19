# ADR-0057: RAG Microsoft Word (.docx) ingestion

Status: Accepted (2026-06-14)
Context: RAG agent R4. Extends the document layer (ADR-0047/0048/0050) beyond markdown, PDF, and images to Microsoft Word `.docx` files — a common source format users keep proposals, notes, and reports in. R2 (ADR-0050) explicitly deferred "docx/pptx and other office formats" to future loaders; this is the first of those.

## Decision

1. **`.docx` text via python-docx, opt-in and lazy-imported.**
   `loaders.extract_docx_text(path)` lazy-imports `python-docx` and joins the
   document's paragraph texts with newlines. python-docx (MIT) is **not a
   bundled dependency** — it is opt-in exactly like OCR's pytesseract/Pillow
   (ADR-0050): the base install stays light, and a missing install raises a
   clear `RuntimeError` ("install python-docx to ingest .docx") rather than an
   opaque ImportError. Local-only; the file never leaves the box.

2. **Graceful degradation — never crash ingestion.** When python-docx is
   absent, `_load` catches the `RuntimeError`, logs an info-level skip
   (mirroring images-without-OCR), and returns `None` so the file is counted
   as skipped. A vault containing `.docx` files ingests its markdown/PDF
   content unaffected.

3. **One unit, file-stem title.** A `.docx` becomes a single loader unit
   `[(None, text)]` (no page concept), `title = file.stem`, empty `tags`/
   `links`. It feeds the shared chunk → store → index pipeline unchanged, so
   citations read `Proposal (proposal.docx#0)`.

4. **`kind` stays `"file"`.** A `.docx` source keeps the existing `"file"`
   `SourceKind` — no new Literal member — since it carries no PDF-style page
   citations or image semantics that would justify a distinct kind. This keeps
   `models.SourceKind` untouched.

5. **Idempotency unchanged.** The raw-bytes SHA-256 gate (ADR-0050) already
   covers `.docx` — an unchanged file is skipped on the mtime/hash gates
   without re-extracting.

## Alternatives considered

- **Bundle python-docx / make it a hard dependency.** Rejected — consistent
  with the OCR opt-in (ADR-0050): office-format support is genuinely optional
  for a markdown/PDF user, and keeping it opt-in keeps the base install light.
- **Add a `"docx"` SourceKind.** Rejected (for now) — a `.docx` has no
  page/OCR distinction worth surfacing; `"file"` is accurate and avoids
  touching the `SourceKind` Literal and its persistence/round-trip surface.
- **Parse tables / styles / headings for richer structure.** Deferred —
  paragraph text is sufficient for search/Q&A; structured extraction (tables,
  heading-based chunk titles) can layer on later, like PDF heading detection.
- **Convert `.docx` → markdown via an external tool (pandoc).** Rejected —
  adds a system-binary dependency and a conversion step; python-docx reads
  paragraphs directly, local and in-process.

## Consequences

**Positive:**
- Users can index Word documents alongside markdown/Obsidian/PDF/images — one
  `iris docs add`, one cited retrieval + Q&A surface.
- Base install stays light/license-clean; python-docx is a documented opt-in
  that degrades gracefully (logged skip, never a crash).
- Idempotency, chunking, and citations reuse the existing pipeline verbatim.

**Negative / deferred:**
- No page citations for `.docx` (the format has no fixed pagination); chunks
  cite `#chunk_index` like markdown.
- Paragraph text only — tables, headers/footers, comments, and embedded
  images are not extracted. No heading-based chunk titles yet.
- `.doc` (legacy binary), `.pptx`, `.xlsx`, and other office formats remain
  unhandled (future loaders).

## Related ADRs

- ADR-0050 (PDF + image OCR — established the opt-in/lazy-import/graceful
  loader pattern this mirrors, and the raw-bytes idempotency), ADR-0047
  (document foundation), ADR-0048 (Obsidian), ADR-0049 (grounded Q&A — answers
  now cite `.docx` sources), ADR-0002 (local-first privacy — extraction stays
  on-box).
