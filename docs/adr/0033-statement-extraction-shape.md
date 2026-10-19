# ADR-0033: Statement extraction — Tier-3 LOCAL into a strict schema, injectable extractor

Status: Accepted (2026-06-13)
Context: Finance build phase F2. Implements the extraction half of ADR-0006 (PDF-from-email) under the constraint of ADR-0002 (privacy-aware Tier 3). Companion: ADR-0007 (currency), ADR-0015 (pdf_unlock), ADR-0031 (data layer), ADR-0032 (secret store).

## Context

F1 lands statement PDFs as `pending` rows. F2 must turn each into
structured ledger data — balances, transactions, holdings — and write it
to `finance.db`. ADR-0006 already chose **LLM extraction into a strict
schema** over per-bank hand-coded parsers (PAIA's brittle path), and
ADR-0002 requires that statement content (high-sensitivity) be processed
by a **local** model only, never cloud.

Open questions for implementation: which LLM path; how to keep it testable
without a running model or real bank PDFs; how to be robust when the model
is down or returns junk; how passwords enter the picture.

## Decision

1. **Tier-3 LOCAL via `CodingLLMClient`.** Extraction calls the
   `complex_task` tier (`TierRouter().get_llm_config("complex_task")` →
   lmstudio/MLX MoE), whose governance hooks fire per invocation. The
   statement text never leaves the machine. No cloud fallback exists in
   this path by construction.

2. **Strict Pydantic schema, JSON-only prompt.** The model is instructed
   to return one JSON object matching `ExtractedStatement` (currency,
   statement/period dates, opening/closing balance, totals, amount due +
   due date, `transactions[]`, `holdings[]`). `parse_extractor_json`
   tolerates a stray prose wrapper (grabs the outer `{...}`), coerces messy
   values (currency symbols, thousands separators, ISO datetimes → date),
   and validates. Money is `Decimal`, never float.

3. **Injectable `StatementExtractor` protocol.** `extract_statement` takes
   an extractor and a text-reader as parameters. Production wires
   `Tier3LocalExtractor` + pdfplumber; tests inject a fake extractor and a
   lambda text-reader — so the whole orchestration (unlock → read → parse →
   persist) is unit-tested with **no live model and no real PDFs**, and
   live extraction is a later, separate validation step.

4. **Regex header fallback.** If the model is unavailable or returns
   unparseable output, `regex_header_fallback` fills the header fields
   (closing balance, amount due, due date, a date) so the statement still
   yields useful totals — just no transactions. The result records
   `used_regex_fallback`.

5. **Passwords via the ADR-0032 secret store.** `password_candidates`
   maps the institution's scheme to candidates: `pan_dob` → PAN+DOB
   permutations (`generate_pan_passwords`); `custom` → the stored
   per-institution password; `none` → try unencrypted. A protected
   statement with no configured secret ends `password_status=failed`,
   `extraction_status=failed` — retried once the secret is set, never
   crashing the batch.

6. **Currency layering (ADR-0007).** Statement-declared currency (from the
   model) wins; else the account/institution default already on the row.
   Every written transaction carries that currency.

7. **Idempotent persistence.** Transactions key on the F0 content
   `dedup_hash`; re-extracting upserts the same rows. Holdings are written
   only when the statement is linked to an account (the `Holding`
   account_id invariant) — otherwise skipped until the account is
   registered and the statement re-extracted.

## Alternatives considered

1. **Per-bank parsers (PAIA's path).** Rejected by ADR-0006 — brittle to
   format changes, doesn't scale across institutions.
2. **Cloud LLM extraction.** Rejected by ADR-0002 — statement content is
   high-sensitivity; local only.
3. **No fallback (LLM-or-nothing).** Rejected — a transient model outage
   would strand statements with zero data; the regex header fallback keeps
   balances/dues flowing.
4. **Function-calling / schema-constrained decoding instead of JSON
   prompt.** exp-004 found schema decoding deletes the parse-failure
   confidence signal and forces wrong commitments; a JSON prompt + tolerant
   parser is the safer default here. Revisitable per-tier.

## Consequences

**Positive:**
- Provider-agnostic, privacy-preserving extraction testable end-to-end
  offline.
- Robust to model downtime (fallback) and bad PDFs (soft-fail per
  statement; the batch continues).

**Negative / deferred:**
- Live extraction quality is unverified until run against the real MLX MoE
  (deliberate: F2 ships build + unit tests; live is the next step).
- Holdings for unresolved-account statements are dropped until re-extract.
- No OCR for scanned/image-only PDFs yet (pdfplumber text only).

## Related ADRs

- ADR-0002, ADR-0006, ADR-0007, ADR-0015, ADR-0031, ADR-0032.
