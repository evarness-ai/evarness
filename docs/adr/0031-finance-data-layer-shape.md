# ADR-0031: Finance data layer — schema, models, and hybrid institution registry

Status: Accepted (2026-06-13)
Context: Finance build phase F0. Companion: ADR-0006 (PDF-from-email ingestion), ADR-0007 (multi-currency, no implicit FX), ADR-0015 (PDF unlock helper), ADR-0016 (which deferred per-domain finance schemas to "their producer skills"). This ADR is that producer.

## Context

The Finance agent is the next PAIA agent after Email. ADR-0016 created
the cross-cutting `email_accounts` registry (in `data/iris.db`) but
explicitly deferred the finance-domain tables — `finance_accounts`,
`finance_statements`, `finance_transactions` — to "their producer skills
in Phases 3-6" to avoid dead scaffolding. F0 builds that data layer: the
foundation every later finance phase (email→finance bridge, statement
extraction, categorization, monitor) writes against.

Three things needed deciding before any table existed:

1. **Where finance tables live** and how money is represented in SQLite.
2. **How an incoming statement email is mapped to an institution** — the
   user banks across two countries (India INR, USA USD) in two separate
   Gmail accounts; statements arrive only by email (ADR-0006).
3. **The boundary between reference data and user data** so the system
   can be open-sourced without leaking the author's specific accounts.

## Decision

**1. `data/finance.db` owns four tables.** `finance_accounts`,
`finance_statements`, `finance_transactions`, `finance_holdings`. Finance
is a single domain, so unlike the email split (where `email_accounts` is
cross-cutting and lives in `iris.db`) all four live together in
`finance.db`. `FinanceStore` mirrors `EmailStore`: lazy `ensure_schema()`,
idempotent upserts, `sqlite3.Row` factory.

**2. Money is `Decimal`, stored as TEXT, paired with an explicit ISO-4217
currency on every monetary row (ADR-0007).** SQLite has no decimal type
and floats corrupt cent arithmetic, so amounts serialize to TEXT via
`str(Decimal)` and rehydrate as `Decimal`. Currency is denormalised onto
`finance_transactions` and `finance_holdings` (not just the statement) so
no query has to join to know the unit, and a multi-currency forex card
stays correct line-by-line. **Aggregation never sums across currencies** —
`balances_by_currency()` / `spend_by_currency()` return `{currency:
Decimal}` dicts. There is no base-currency conversion in this layer;
opt-in FX (`--convert-to`) is a query-time concern for a later phase.

**3. Hybrid institution identification, registry first.** Institution
*definitions* live in `config/finance/institutions.yaml` as reference
data (sender-domain patterns → institution id, country, currency,
account type, password scheme). `InstitutionRegistry.match()` does an
exact-or-suffix domain match, confirmed/disambiguated by optional subject
regexes, and returns a confidence + reason. This deterministic first pass
is auditable and open-sourceable (a contributor adds their bank by
editing YAML). Emails that match nothing fall through to the **learned
fallback** in F1 — the existing email-triage kNN-gate + correction
centroids. Registry knowledge is never written to `finance.db`; the
user's real accounts (`finance_accounts.institution_id`) reference it.

**4. Stable, human-readable identities.**
- `finance_accounts.id` = `<institution_id>:<last4-or-label-slug>`
  (same delete/re-add stability rationale as ADR-0016's account slugs).
- `finance_statements.id` = the file's SHA-256 — the natural dedup key,
  so a re-sent statement or a manual drop of an already-emailed file
  upserts rather than duplicates.
- `finance_transactions.id` = a content `dedup_hash`
  (statement_id + date + amount + normalised description + direction),
  so re-extracting a statement is idempotent on the ledger.

**5. Provider-agnostic shapes (ADR-0006).** No per-bank columns anywhere;
bank-specific knowledge is confined to the YAML registry. Most
statement/transaction monetary and date fields are nullable because they
are unknown until F2 extraction runs; `account_id`/`institution_id` are
nullable because resolution may lag ingestion.

## Alternatives considered

1. **Floats for money.** Rejected — silent rounding error in a finance
   ledger is unacceptable; `Decimal`-as-TEXT is the standard SQLite
   workaround.
2. **A base currency with stored FX conversions.** Rejected by ADR-0007:
   a stale/wrong rate silently corrupts every reported total.
3. **Institutions as a DB table seeded from code.** Rejected for F0:
   institution definitions are static reference data and code-reviewable
   YAML is more transparent and contributor-friendly than a seed
   migration. (User-discovered institutions via the learned path may be
   promoted to YAML later, but that is a manual, reviewed step.)
4. **Learned-only institution ID (kNN/centroids exclusively).** Rejected
   as the primary path — it is opaque and needs labelled examples to
   bootstrap, whereas a bank's sender domain is a near-perfect
   deterministic signal. Kept as the *fallback*, not the front door.
5. **One DB shared with email (`email.db`).** Rejected for the same
   ownership-clarity reason ADR-0016 gave: finance code shouldn't have to
   open the email domain's DB.

## Consequences

**Positive:**
- Later finance phases write against a stable, currency-correct schema.
- Adding a bank is a YAML edit, no code change; open-sourcing ships a
  generic starter registry with no personal data.
- Idempotent identities make the whole pipeline safe to re-run.

**Negative / deferred:**
- F0 ships no ingestion or extraction — the tables are empty until F1/F2.
- The registry's starter domains are best-effort and will need real-mail
  verification during F1 (sender domains vary by bank mailer).
- `email_account_id` is a soft FK (SQLite can't enforce cross-file FKs);
  integrity is the bridge's responsibility.

**Reversibility:** high. The schema is additive; new columns follow the
`EmailStore._apply_migrations` PRAGMA pattern when needed.

## Related ADRs

- ADR-0006 — PDF-from-email ingestion path (this is its data sink).
- ADR-0007 — multi-currency rules this layer enforces.
- ADR-0015 — PDF unlock helper (the `password_scheme` field feeds it).
- ADR-0016 — deferred these tables to here; defines the `email_accounts`
  join target.
