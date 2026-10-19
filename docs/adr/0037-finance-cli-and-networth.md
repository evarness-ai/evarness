# ADR-0037: `iris finance` CLI + per-currency net-worth semantics

Status: Accepted (2026-06-13)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Finance build F3 (CLI + queries slice). Companion: ADR-0007 (multi-currency), ADR-0031 (data layer), ADR-0032 (secret store).

## Context

F0–F2 + per-doc-type extraction made the finance data reliable, but the
only way to drive it was throwaway scripts (`scripts/finance_set_*`) and
Python one-liners. F3 needs a real user surface: register accounts, store
PDF secrets safely, and *see* the result (net worth, spend). Computing net
worth surfaced a semantic question that needed deciding.

## Decision

1. **An `iris finance` CLI**, thin over the domain layer (logic stays in
   `iris_harness.finance`, unit-tested there):
   - `account add|list` — registry-aware registration (`register_account`
     defaults currency/country/type from the institution registry).
   - `secret set-pan | set-password` — hidden (`getpass`) input into the
     pluggable secret store (ADR-0032), replacing the throwaway scripts.
   - `networth | spend | statements | institutions` — read-only views.
   Bodies take a `--db-path` override so they're CliRunner-testable.

2. **Net-worth semantics (the non-obvious part).** Net worth is computed
   **per currency, never summed across** (ADR-0007), as
   `cash + investments`:
   - **cash** = latest closing balance of **bank/wallet** accounts only;
   - **investments** = sum of latest holding values.
   An **investment account's closing balance is its portfolio value — the
   same money as its holdings** — so counting both would double net worth.
   `cash_balances_by_currency` therefore joins `finance_accounts` and counts
   closing balances only for `bank`/`wallet` types; `balances_by_currency`
   (unfiltered, all accounts) stays for general use.
   **Cards and loans (liabilities) are not netted yet** — a known gap;
   they'd subtract from net worth.

## Alternatives considered

- **Sum all statement closing balances as "cash".** Rejected — double-counts
  investment accounts (verified: the CAS closing ₹1,00,000.00 equals its
  Acme holding; summing gives a false ₹2,00,000).
- **Inline the commands in `main.py`** (like the email CLI). Chose a
  separate `iris_harness.cli.finance` module — keeps `main.py` lean and the CLI
  testable in isolation.
- **A base/"total" currency line.** Rejected by ADR-0007 (no implicit FX);
  an opt-in `--convert-to` can come later.

## Consequences

**Positive:**
- The finance agent is usable end-to-end from the CLI; live:
  `iris finance networth` → `INR  cash 0  investments 1,00,000.00 = 1,00,000.00`
  off real extracted statements.
- Secrets onboarded via `getpass` → the secret store; no more throwaway
  scripts or values in shell history.

**Negative / still open:**
- Card/loan liabilities not yet subtracted from net worth.
- No opt-in FX consolidation yet (`--convert-to`).
- Transaction **categorization** (the kNN learning loop) is the next F3
  slice — this one is accounts + secrets + queries.

## Related ADRs

- ADR-0007 (multi-currency), ADR-0031 (data layer + `balances_by_currency`),
  ADR-0032 (secret store), ADR-0036 (per-doc-type extractors feeding
  holdings).
