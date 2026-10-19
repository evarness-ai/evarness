# ADR-0034: PDF extraction engine — pypdfium2 + pdfplumber, no AGPL; deterministic decoding

Status: Accepted (2026-06-13)
Context: Finance build phase F2, quality pass. Refines ADR-0033 (extraction shape). Driven by live extraction on real statements.

## Context

F2's first extraction pass read PDFs with pdfplumber's plain
`extract_text()` plus a naive `" | "`-joined table dump, then handed that
to the Tier-3 LOCAL model. Live runs on real statements showed two
problems:

1. **Quality:** dense financial tables (HDFC contract note, multi-folio
   CAMS CAS) came out as flattened text with columns mangled — the model
   couldn't tell which number belonged to which column, producing empty
   descriptions and wrong figures.
2. **Licensing:** `pymupdf` (PyMuPDF/MuPDF) was a declared dependency. It
   is **AGPL-3.0**, incompatible with shipping IRIS under **Apache-2.0**.
   It was also unused (nothing imported it) — a latent license landmine.

A third issue surfaced mid-pass: the tier-3 default **temperature 0.7**
made extraction non-deterministic — the same statement yielded different
numbers across runs, including a misplaced decimal in a CAS closing
balance (`100000.00` → `1000.00000`).

## Decision

1. **Drop `pymupdf` (AGPL).** Removed from dependencies — unused and
   license-incompatible with the open-source goal.
2. **`pypdfium2` for narrative text.** Google's PDFium via pypdfium2 is
   **BSD-3 / Apache-2.0**, fast, ships prebuilt binaries (no system deps),
   and is the robust/efficient engine PyMuPDF would have been — without
   the AGPL. Used for header/narrative text and password-protected docs.
3. **`pdfplumber` (MIT) for table structure, rendered as aligned
   markdown.** Tables are detected per page and emitted as GitHub-style
   markdown grids (`| col | col |` + separator) so the model reads
   columns, not a flattened blob. `extract_pdf_text` = pdfium narrative +
   markdown tables; either engine failing degrades to the other.
4. **Deterministic extraction (temperature 0).** The extractor pins the
   tier-3 config to `temperature=0.0` (`model_copy`). Extraction is a
   transcription task, not generation — determinism removes run-to-run
   variance and fixed the decimal-mangling. The 0.7 default stays for the
   tier's generative uses.
5. **Text cap raised to ~24k chars.** Markdown is more verbose; 24k keeps
   headroom inside num_ctx 16384 for the schema + answer.

## Alternatives considered

- **PyMuPDF (fitz).** Fastest/robust, but AGPL — rejected for an
  Apache-2.0 project. (A commercial MuPDF licence was not pursued.)
- **camelot.** Strong lattice tables but pulls **Ghostscript (AGPL)** —
  drags the licence problem back in.
- **docling (IBM, MIT).** Excellent table fidelity but a heavy ML
  dependency (downloads torch models); too costly for the efficiency goal.
  Reconsider if dense-table fidelity becomes a hard requirement.
- **Status quo (pdfplumber plain text).** Rejected — the flattened-column
  problem is exactly what hurt quality.

## Consequences

**Positive:**
- Permissive, efficient, no system deps; AGPL liability removed.
- Aligned-markdown tables + deterministic decoding materially improved
  fidelity (CAS balance correct; stable across runs).

**Negative / still open:**
- **Line-item fidelity on dense Indian statements remains imperfect** —
  the CAS still drops some transaction descriptions and the contract note
  surfaces notional/settlement rows. This is a model+layout limit, not a
  parser bug. Deeper levers (per-document-type prompts, a
  sum-of-transactions-vs-balance reconciliation/validation pass, or a
  local vision model on page images) are deferred to F3+.
- Long multi-folio CAS files still truncate at the char cap; chunking is
  future work.

## Addendum (2026-06-13): pymupdf4llm evaluated as an opt-in backend

A user pointed at `pymupdf4llm` (the "PDF→LLM markdown" tool). It is
**dual-licensed AGPL-3.0 / Artifex-commercial** and hard-depends on
`pymupdf` — the same AGPL blocker — so it cannot be a *bundled* dependency
of an Apache-2.0 project. We made `extract_pdf_text` **backend-pluggable**
(`IRIS_PDF_BACKEND`): the permissive pypdfium2+pdfplumber path is the
shipped default; `pymupdf4llm` is a **lazy, user-installed, opt-in**
backend that never enters IRIS's dependency tree (same pattern as the
secret store, ADR-0032).

**Empirically it did not improve extraction** on our hardest real
document. On the CAMS CAS, head-to-head through the same Tier-3 model:

| backend | closing balance | holding qty | note |
|---|---|---|---|
| default (permissive) | **₹100000.00 ✅** | **2000.000 ✅** | correct |
| pymupdf4llm | None ❌ | 2.000000 ❌ (1000× off) | cleaner markdown, worse numbers |

pymupdf4llm produced more nicely-structured markdown but the model then
**misread magnitudes** (Indian thousands separators) and missed the
closing balance; the permissive default extracted better numbers. Takeaway:
the bottleneck is the **model reading dense Indian-formatted tables**, not
the PDF→text engine. The genuine quality levers are per-document-type
prompts + a balance-reconciliation/validation pass (deferred to F3+), not a
parser swap. The opt-in backend is kept as infrastructure (zero cost when
unused), but the default stays permissive **and** measured-better here.

## Related ADRs

- ADR-0033 (extraction shape — this refines its text layer + decoding).
- ADR-0002 (Tier-3 local), ADR-0006 (LLM extraction, no per-bank parsers).
- ADR-0032 (pluggable secret store — same opt-in-backend pattern).
