# ADR-0006: Finance ingestion via PDF-from-email + manual drop; no Plaid/scraping in core

Status: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §3.2.

## Context

The upgrade adds finance ingestion: statements, transactions, bills,
account balances, holdings. Four candidate ingestion paths exist:

| Path | Source |
|---|---|
| **(A)** PDF statements arriving via email | Bank's statement-email cadence |
| **(B)** Plaid (or similar SaaS aggregator) | Third-party holds bank creds |
| **(C)** Bank-portal scraping (Selenium/Playwright) | Browser automation per bank |
| **(D)** Manual file drop into a watched folder | User downloads, IRIS parses |

The user explicitly rejected Plaid as core: "Plaid is not acceptable
for our policy."

The user banks in two countries (India: HDFC/SBI; US: Chase/Amex), which
makes aggregator coverage gaps and per-bank scraper maintenance
particularly painful.

## Decision

**Core ingestion path: (A) PDF statements from email + (D) manual folder
drop.** Tier 3 LOCAL LLM extracts transactions from raw PDF text into a
strict Pydantic schema. (B) and (C) are explicit non-goals for core;
contributors may ship them as community skills.

```
Gmail statement email ──▶ email-triage ──▶ download attachment ──▶
                                            data/finance/statements/
                                            <provider>/<YYYY>/<file>.pdf
                                            │
User drops PDF ──────────────▶ folder watcher
                                            │
                                            ▼
                              finance-statements skill:
                              pdftotext/pdfplumber → Tier 3 LOCAL →
                              finance_transactions
```

Statement parsing uses Tier 3 LOCAL (not hand-coded per-bank parsers).
Caches aggressively; re-parses only on schema migration.

Balance is **statement-derived**, not real-time:
`closing_balance + sum(post-statement transactions)`. Post-statement
transactions come from email-triage classifying transaction-confirmation
emails. A reconciliation routine runs nightly.

Bill discovery uses **three converging signals** with manual
confirmation: (1) recurring sender pattern from email-triage,
(2) recurring vendor in transactions, (3) manual `iris finance bill add`.
Dedup via wiki-canonicalized payee name.

## Alternatives considered

1. **Plaid in core.** Rejected by user policy. Adds SaaS dependency,
   ongoing cost, and gives Plaid access to all transactions. Coverage
   is US-strong but India-spotty — wouldn't even cover the user's
   primary case.
2. **Per-bank portal scrapers in core.** Brittle (sites change weekly),
   MFA breaks them, often ToS-violating, requires per-bank maintenance.
   Acceptable as community per-bank skills; not core.
3. **Hand-coded per-bank PDF parsers.** Paia's path; brittle to format
   changes; doesn't scale across institutions. Rejected in favor of
   LLM extraction.
4. **Cloud LLM extraction.** Rejected by ADR-0002 (statement content
   is high-sensitivity; local Tier 3 only).
5. **CSV/OFX/QFX-only ingestion.** Many banks support these, but not
   all, and the user experience of "download CSV from portal monthly"
   is uglier than letting statement emails flow naturally. Covered as
   a secondary `finance-csv-importer` skill, not the primary path.

## Consequences

**Positive:**
- Default install works for any user worldwide without third-party
  signup or per-bank engineering.
- Privacy holds: statement PDFs never leave the machine; Tier 3 stays
  local for high-sensitivity content per ADR-0002.
- Plays to email-triage's strengths; no new ingestion infrastructure.
- Open-source contributors can ship Plaid/scraping as community skills
  with clean opt-in.

**Negative:**
- Not real-time. Balance estimate may drift between statement cycles
  by amount of unsettled transactions. UI shows "estimated as of <ts>"
  disclaimer.
- Statement-email cadence is set by the bank; daily transaction
  visibility depends on transaction-confirmation emails (which most
  banks send for credit cards but fewer send for checking accounts).
- Manual folder drop required for any bank that posts statements only
  on portal. Documented bridge; not a blocker.

**Reversibility:** high. Adding a Plaid-style aggregator later is a new
skill, not a refactor. The `finance_transactions` schema is
provider-agnostic.

## Related ADRs

- ADR-0002 (privacy-aware Tier 3) determines that statement extraction
  uses `tier3.local`, never cloud.
- ADR-0007 (multi-currency rules) governs how statements from different
  jurisdictions are tagged and aggregated.
