# ADR-0035: Deterministic numbers — don't let the LLM transcribe digits; read cells + reconcile

Status: Accepted (2026-06-13)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Finance build F2, accuracy pass. Follows ADR-0033 (extraction shape), ADR-0034 (PDF engine + the pymupdf4llm evaluation). Driven by live extraction errors on real statements.

## Context

F2 extracted statement numbers with the Tier-3 LOCAL LLM. Live runs showed
the LLM **mis-transcribes digits** from tables — not a parsing or
prompt-quality bug, an intrinsic one:

- a CAS holding quantity `2000.000` came back as `2.000000` (lost a
  decimal magnitude on the Indian thousands-separator);
- the CAS closing balance was sometimes wrong or missed entirely;
- counter-intuitively, feeding the model *cleaner* markdown (pymupdf4llm,
  ADR-0034) made some numbers **worse**, because nicer structure gave it
  new ways to misread magnitude.

For a finance ledger, a silently-wrong number is the worst possible
failure. The PDF→text engine was never the bottleneck — both engines
surfaced the data; the model corrupts it on the way into JSON.

## Decision

**The LLM does not transcribe numbers. Code does.**

1. **`iris_harness.finance.money` — the trust anchor.** A pure, exhaustively-tested
   parser: Indian lakh grouping (`1,23,456.78`) + Western (`1,234,567.89`),
   currency symbols (`₹ Rs. INR $ USD`), accounting negatives (`(109)`,
   trailing/leading `-`), and `Cr`/`Dr` direction markers. Every amount in
   `finance.db` flows through it.

2. **`iris_harness.finance.tables` — deterministic table → transactions.**
   pdfplumber yields the **exact** table cells; columns are mapped by
   header keywords (date / description / debit / credit / amount / balance
   / Dr-Cr); each amount is parsed by `money`. The digits come straight
   from the PDF — zero LLM transcription. Returns `[]` when no recognizable
   transaction table is found (caller falls back to the LLM).

3. **Labelled balances, read exactly.** `extract_labeled_amount` scans the
   text for `closing balance` / `opening balance` / `amount due` labels and
   parses the adjacent number with `money` — again, exact digits, not the
   model.

4. **Reconciliation guard.** `_reconcile` checks
   `opening + Σcredits − Σdebits ≈ closing` (±1). When it fails the
   statement is flagged (`ExtractionResult.reconciled = False`) and logged.
   "Reliable" is redefined as **correct, or flagged as untrustworthy** —
   never silently wrong.

5. **Deterministic-first merge.** `extract_statement` prefers deterministic
   transactions/balances; the LLM is kept for what it's good at (currency,
   statement dates, document type) and as the fallback when the
   deterministic pass finds no structure. `ExtractionResult.transactions_source`
   records `tables` vs `llm`.

## Alternatives considered

- **Trust the LLM, validate after.** Re-checking a number the LLM already
  corrupted doesn't recover it. Rejected.
- **A "better" PDF library (pymupdf4llm).** Evaluated in ADR-0034 — cleaner
  markdown, *worse* numbers. The bottleneck isn't the parser.
- **A bigger/cloud model.** Cloud is barred for statement content
  (ADR-0002); and digit-transcription is unreliable across model sizes.
  Determinism is free and exact.

## Consequences

**Positive:**
- Exact numbers wherever a table is recognised; balances read exactly.
- The reconciliation guard surfaces bad extractions instead of hiding them
  (live: it correctly flagged the CAS + margin-report extractions).
- `money` is reusable across the whole finance domain.

**Negative / still open:**
- The deterministic transaction extractor targets **ledger-style** tables
  (bank/card: Date/Debit/Credit). **CAS and contract notes are not ledgers**
  (holdings / trades) — they still fall to the LLM and get flagged by
  reconciliation. Per-document-type extractors (CAS→holdings,
  contract-note→trades) are the next step (F3+).
- Header-keyword column mapping is heuristic; unusual layouts fall back to
  the LLM.

## Related ADRs

- ADR-0033 (extraction shape), ADR-0034 (PDF engine + pymupdf4llm eval),
  ADR-0002 (Tier-3 local), ADR-0007 (currency).
