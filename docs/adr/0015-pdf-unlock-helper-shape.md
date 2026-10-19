# ADR-0015: PDF unlock helper — implementation shape

Status: Accepted (2026-05-25)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Personal-assistant upgrade Phase 0 track 6. Companion: canonical doc §3.2 (parent design); ADR-0006 (PDF-from-email finance ingestion).

## Context

The canonical doc §3.2 named a **Shared module:
``src/iris_harness/finance/pdf_unlock.py``** for password-protected PDFs as a
Phase 0 track. The parent ADR-0006 established the finance-ingestion
path that needs this (statement PDFs arrive via email, many encrypted;
also CAS files in Phase 5). The implementation shape was left to
this phase.

Phase 0 track 6 implemented the helper (commit ``78adf33``) as three
pure functions over pdfplumber. Five implementation decisions were
resolved during that work that aren't in the parent ADRs.

## Decision

Five implementation decisions:

1. **Three pure functions; no class.** ``try_open``,
   ``find_password``, ``generate_pan_passwords`` — module-level
   functions only. No ``PDFUnlocker`` class because there is no state
   to hold (caching is explicitly deferred), and a class wrapper
   would only obscure that this is straightforwardly stateless.

2. **Thin layer over pdfplumber; no new PDF dependency.**
   ``pdfplumber.open(path, password=...)`` already supports the
   standard PDF security handlers (RC4-40, RC4-128, AES-128,
   AES-256) via pdfminer.six. Adding ``pikepdf`` or ``pypdf`` would
   be redundant for the MVP coverage we need. Phase 3+ may need
   ``pikepdf`` for *writing* unlocked copies; that's a separate
   future addition.

3. **No macOS Quartz fallback in this commit.** Paia's
   ``macos_pdf_decrypt.py`` exists for HDFC personal-loan
   statements that use a proprietary encryption handler pdfplumber
   can't crack. Real such files haven't yet been needed; deferring
   the ~250-LOC ``pyobjc-framework-Quartz`` integration until a
   Phase 3+ test surfaces one. When it does, extend with a
   ``try_open_with_quartz()`` fallback and document via a separate
   ADR.

4. **No password caching, vault integration, or persistent
   decrypted-copy writes.** Pure functions only at this layer; the
   future CAS skill (Phase 5) and finance-statements skill (Phase
   3) compose this with the Keychain-backed credentials module
   (ADR-0011 + ADR-0003) at their own layer. Keeping these
   concerns out of ``pdf_unlock`` lets the same helper be reused
   in different policy contexts.

5. **``try_open`` returns ``bool`` only — all errors collapse to
   False.** Wrong password, missing file, parse error, weird
   encryption handler all return False. Callers that need to
   distinguish failure modes call ``pdfplumber.open`` directly.
   Trade-off accepted: simpler contract beats richer-but-leakier
   error semantics. Logged at debug level so root-cause
   investigation is still possible.

## Alternatives considered

1. **``PDFUnlocker`` class with caching state.** Cleaner OOP shape;
   needed if/when we add password caching. Rejected for MVP:
   caching is explicitly deferred; adding the class scaffolding now
   would be premature.

2. **Add ``pikepdf`` as a runtime dep.** Stronger PDF coverage (some
   broken or malformed PDFs that pdfplumber can't open work with
   pikepdf). Rejected: not yet needed; adding a non-trivial native
   dep before there's a real failure case is wrong. pikepdf can be
   added as a fallback layer in a future ADR.

3. **Add ``pyobjc-framework-Quartz`` for the macOS path now.**
   Covers HDFC-loan-style proprietary encryption. Rejected for MVP:
   no real such PDF in the workflow yet; the dep is non-trivial
   (native ObjC bridge); the fallback path is naturally additive
   later.

4. **Distinguish wrong-password from file-not-found in ``try_open``.**
   Richer semantics; e.g. raise on I/O, return bool only for password
   validity. Rejected: callers either care about "did it open"
   (most cases) or they need full control (rare; call pdfplumber
   directly). The middle ground adds API complexity for marginal
   benefit.

5. **Return ``BinaryIO`` / opened pdfplumber.PDF on success
   rather than just ``True``.** Avoids re-opening the file when
   caller wants the contents. Rejected: encourages keeping the PDF
   open across function boundaries (resource management), and the
   re-open cost is negligible (sub-millisecond for the PDF header
   check). ``find_password`` callers should pass the discovered
   password to whatever pdfminer/pdfplumber operation they actually
   want.

## Consequences

**Positive:**
- Module is ~80 LOC. Phase 3+ skills (finance-statements,
  cas-statement) get a four-line consumer pattern:
  ``candidates = [...] + generate_pan_passwords(pan, dob);
   pw = find_password(path, candidates);
   if pw: <open with pdfplumber and pw>``
- No new dependency; the security envelope of the project doesn't
  expand for this track.
- 16-test suite covers candidate generation determinism, mocked
  pdfplumber success/failure paths, iteration short-circuit, and
  empty-candidate edge case. Real-PDF integration testing is
  punted to Phase 3 where real statements live.
- Two ``# noqa: S105`` annotations in the test mock document false-
  positive hits from ruff's hardcoded-password rule; they're
  explained inline so future readers don't think they're shipping
  credentials.

**Negative:**
- HDFC-style proprietary-encryption PDFs will fail silently
  (``find_password`` returns None even with correct password) until
  the Quartz fallback lands. Phase 3 testing with real HDFC PDFs
  will surface this and we'll add the fallback as ADR.
- The ``bool``-only contract means callers can't tell whether
  "didn't open" was due to wrong password, missing file, or
  unsupported encryption. For most callers this is fine; the
  module logs the underlying exception at debug level.
- Real encrypted-PDF integration tests need real statement files,
  which can't ship with the repo (PII/copyright). Phase 3
  test-strategy ADR will document the fixture approach (probably:
  one synthetic encrypted PDF generated by a Phase 3 dev-only
  pikepdf fixture).

**Reversibility:** high. Each function is independent; adding a
Quartz fallback or pikepdf layer is an additive change in
``try_open`` (or a sibling function); adding caching is a state
addition that could go through a thin class wrapper without changing
the existing function API.

## Related ADRs

- ADR-0006 (PDF-from-email finance ingestion) — names the dependency
  on this helper from finance-statements skill.
- ADR-0011 (Keychain credentials shape) — sibling Phase 0 helper for
  the "where does the password come from" half of the equation.
- ADR-0010, ADR-0012, ADR-0013, ADR-0014 — sibling implementation-
  shape ADRs from Phase 0.
- A future ADR will introduce the Quartz fallback when real HDFC-
  loan-style PDFs surface.
- A future ADR may add pikepdf as a fallback layer (broken/malformed
  PDFs pdfplumber can't open).
