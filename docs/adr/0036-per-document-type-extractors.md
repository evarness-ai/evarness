# ADR-0036: Per-document-type extractors — contract notes → trades, CAS → holdings

Status: Accepted (2026-06-13)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Finance build F2, accuracy pass 2. Extends ADR-0035 (deterministic numbers). Driven by inspecting real CAMS CAS + HDFC Securities documents.

## Context

ADR-0035 made the *numbers* deterministic, but its transaction extractor
targets **ledger** tables (bank/card: Date / Debit / Credit). Investment
documents aren't ledgers and fell through to the LLM (then got flagged by
reconciliation):

- a **contract note** is a list of **trades** (security, qty, rate, net
  cash), not a running balance;
- a **CAS** (Consolidated Account Statement) is a **holdings** snapshot
  (scheme, units, NAV, cost, value).

Inspecting the real PDFs (pdfplumber): the contract note has a clean
per-security net-summary table (`Security | Qty Brought | Qty Sold | Rate |
… | Net Amount`), while the CAS fragments into ~20 small tables but exposes
the portfolio cost/value/NAV as labelled values.

## Decision

A **document-kind dispatch** in `extract_statement`, keyed off the text
(`detect_doc_kind`: `contract_note` | `cas` | `ledger`), routing to a
structure-aware deterministic extractor in `iris_harness.finance.doctypes`:

1. **Contract note → trades.** Find the per-security net-summary table by
   header keywords; each row with a positive *quantity bought* is a BUY
   (cash out → debit), positive *quantity sold* a SELL (cash in → credit);
   the amount is the exact net-cash cell. Holdings are suppressed (a trade
   isn't a position).
2. **CAS → holdings (per fund).** Each fund's section ends in a
   ``Fund Summary`` block (NAV / Cost / Value) preceded by its scheme name.
   Split on those anchors → **one holding per scheme**; name from the fund
   line (header/label/allocation lines skipped); units = value / NAV. CAS
   PDFs repeat each summary per page, so holdings are **deduped by value**
   (keeping the named one). No anchor → single aggregate fallback.
3. **Everything else → ledger** (the ADR-0035 table extractor).

All amounts parsed by `iris_harness.finance.money` from exact cells/text. Each
extractor returns empty when it can't find its structure → LLM fallback,
still guarded by reconciliation.

## Live result (real documents, 2026-06-13)

| document | kind | deterministic output |
|---|---|---|
| HDFC Securities contract note | contract_note | **BUY 15 ACME LIMITED @ 100.00, debit ₹2000.00** (exact) |
| CAMS / Acme AMC CAS | cas | **Acme Smallcap Fund · 2000.000 units · cost ₹80,000.00 · value ₹1,00,000.00** (exact) |

The CAS units `2000.000` is precisely the figure the LLM had mangled to
`2.000000` — now read exactly.

## Alternatives considered

- **One generic extractor for all docs.** Rejected — ledger column logic
  doesn't fit trades or holdings; structure differs fundamentally.
- **Per-document-type LLM prompts (no deterministic parse).** Still leaves
  the model transcribing digits (ADR-0035's core problem). Deterministic
  structure-aware parsing is the reliable path; doc-type *prompts* remain a
  possible enhancement for the fallback.

## Consequences

**Positive:**
- Exact trades from contract notes and exact holdings from CAS — the two
  document types that previously failed.
- Re-extraction now **replaces** rows (store gains
  `delete_{transactions,holdings}_for_statement`, called before persist) so
  switching method (LLM → deterministic) doesn't leave stale rows.

**Negative / still open:**
- A margin report is neither trades nor holdings nor a ledger → still LLM +
  reconciliation-flagged. A dedicated kind could be added if needed.
- Multi-fund CAS splitting is validated against **synthetic** multi-fund
  text (our only real CAS is single-fund); the real CAS regression confirms
  the anchor+dedup path yields the one correct holding. A real multi-fund
  CAS should be run through once it arrives.
- Doc detection is text-marker based; unusual wording falls back to ledger.

## Related ADRs

- ADR-0035 (deterministic numbers), ADR-0033/0034 (extraction shape + PDF
  engine), ADR-0007 (currency).
