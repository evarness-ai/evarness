# ADR-0050: RAG PDF + image ingestion with opt-in OCR

Status: Accepted (2026-06-14)
Context: RAG agent R2. Extends the document layer (ADR-0047/0048/0049) beyond markdown to PDFs and images, with OCR for scanned/image content — per user direction that R2 must cover images and OCR, not just typed PDFs.

## Decision

1. **PDF text via the bundled permissive backend (pypdfium2), per page.**
   `loaders.extract_pdf_pages` returns one string per page (page 1 → index 0)
   using `pypdfium2` — the BSD/Apache backend IRIS already bundles for Finance.
   **PyMuPDF/pymupdf4llm are AGPL and deliberately not bundled** (ADR-0033); we
   do not use them here either. Chunks carry their page number so citations
   read `Report (report.pdf p.3)`.

2. **OCR is opt-in, lazy, and graceful — never a hard dependency.**
   `iris_harness.rag.ocr` lazy-imports `pytesseract` + `Pillow` and probes the
   Tesseract binary (`ocr_available()`); `ocr_image()` raises `OcrUnavailable`
   on a missing toolchain or a bad image. Tesseract is Apache-2.0 but a
   **system** binary, and pytesseract/Pillow aren't bundled, so OCR mirrors the
   pymupdf4llm opt-in: install `pytesseract pillow` + tesseract to enable;
   without it, typed PDFs/markdown ingest unaffected and images are skipped
   with a log (never a crash). OCR runs locally — images never leave the box.

3. **Two OCR paths.** (a) **Images** (`.png/.jpg/.jpeg/.tiff/.bmp/.webp`) →
   OCR'd to text, one source. (b) **Image-only PDF pages** → when a page yields
   no embedded text and OCR is available, the page is rendered
   (`pypdfium2` → PIL) and OCR'd, so scanned statements/notes become
   searchable with correct page citations.

4. **One ingestion pipeline, type-dispatched by a loader.** `_load(file, raw)`
   returns `(title, tags, links, units)` where a unit is `(page|None, text)`:
   markdown = one unit (front-matter/wikilinks as before), PDF = one unit per
   non-empty page, image = one OCR'd unit. The rest (chunk → store → index) is
   shared. Markdown behaviour (R0/R1) is byte-for-byte unchanged.

5. **Idempotency keyed on raw file bytes.** The content hash is now the
   **SHA-256 of the file bytes** (not extracted text), so an unchanged PDF/
   image is skipped on the mtime/hash gates **without re-extracting or
   re-running OCR** — important because OCR is expensive.

## Alternatives considered

- **PyMuPDF/pymupdf4llm for richer PDF→markdown.** Rejected — AGPL, not
  bundleable in Apache-2.0 IRIS (ADR-0033). pypdfium2 per-page text is
  sufficient and license-clean; pymupdf4llm remains a user opt-in for Finance.
- **Bundle an OCR engine / make OCR a hard dep.** Rejected — Tesseract is a
  system binary and the Python wrappers add weight; OCR is genuinely optional
  for a typed-document user. Opt-in keeps the base install light and clean.
- **A cloud OCR / vision API.** Rejected — violates the local-first, privacy
  stance (ADR-0002); documents must stay on the box.
- **Hash extracted text for idempotency.** Rejected — would re-extract/re-OCR
  every sync just to compute the hash; raw-bytes hashing avoids that.

## Consequences

**Positive:**
- Users can index PDFs (with page citations) and images/scanned PDFs (via OCR)
  alongside their markdown/Obsidian notes — one `iris docs add`, one cited
  retrieval + Q&A surface.
- Base install stays light/license-clean; OCR is a documented opt-in that
  degrades gracefully.
- Unchanged files never trigger re-OCR (raw-bytes idempotency).

**Negative / deferred:**
- OCR accuracy/layout is Tesseract-grade (no tables/columns reconstruction);
  good enough for search, not a layout-faithful parse.
- No language config / preprocessing knobs yet (deskew, DPI tuning beyond the
  2× render scale).
- docx/pptx and other office formats remain unhandled (future loaders).
- Per-page chunk titles for PDFs are the file title (no heading detection in
  PDF text).

## Related ADRs

- ADR-0047 (document foundation), ADR-0048 (Obsidian), ADR-0049 (grounded Q&A
  — answers now cite PDF pages / OCR'd images), ADR-0033 (PDF backend
  licensing — pypdfium2 bundled, pymupdf4llm opt-in), ADR-0002 (local-first
  privacy — OCR stays on-box).
