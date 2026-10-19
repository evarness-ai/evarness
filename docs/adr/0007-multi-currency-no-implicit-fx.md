# ADR-0007: Multi-currency model — currency-required, group-by-currency aggregation, no implicit FX

Status: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §3.2.

## Context

The user operates two Gmail accounts: one for Indian bank accounts
(INR) and one for US bank accounts (USD). Statements arrive in
different currencies; bills are denominated per-jurisdiction; transaction
amounts must never be mixed across currencies in arithmetic.

The naive approach — store amounts as numbers, infer currency, convert
to a base currency for aggregation — has well-known failure modes:
silent FX rates can corrupt every reported number with one bug; stale
rates compound; a misclassified currency tag becomes invisible damage.

## Decision

1. **Every monetary column carries an explicit `currency` field**
   (ISO-4217 code). No bare numeric amounts in finance tables.
2. **A new `email_accounts` table** captures which Gmail addresses IRIS
   knows about, with `currency_default` and `country_hint` per account.
3. **`finance_accounts` is enriched** with `country`, `currency`, and
   `email_account_id` (which Gmail receives this account's statements).
4. **Currency detection is layered:**
   1. Statement-declared (Tier 3 LOCAL returns currency as required field).
   2. Account-bound default (from `email_accounts.currency_default`).
   3. LLM cross-check (corroborating signals like bank name, IFSC vs
      routing number, ZIP vs PIN). Discrepancies flagged, not silenced.
5. **Aggregation rule: never sum across currencies.** All queries
   group-by-currency and return per-currency dicts (e.g.,
   `{INR: 45000, USD: 1200}`). Morning brief renders them as
   per-currency lines.
6. **Optional consolidation only via explicit opt-in:**
   `iris finance show --convert-to USD`. Uses a daily-cached FX rate
   from a free provider (e.g., exchangerate.host). Converted values
   prefixed with `≈` and tagged with the rate's `as_of` timestamp.

There is **no "base currency"** concept anywhere in core. Per-account
currency is the unit of account; user explicit conversion is the unit
of comparison.

## Alternatives considered

1. **Implicit FX with base currency.** Pick one (USD or INR), store
   everything converted, expose original separately. Rejected: a stale
   or wrong rate silently corrupts the displayed "total wealth"
   number; reconciling against statements becomes a math problem.
2. **Per-account currency only, no FX at all.** Cleaner but fails the
   user's request for a unified view occasionally. Adding opt-in
   `--convert-to` preserves the option.
3. **Live FX provider per query.** Rejected: adds network dependency
   to display, rate-limits matter, errors propagate to UI. Daily
   cache is plenty for personal use.
4. **Single-currency MVP (just INR or USD).** Rejected: user has both
   from day one; deferring is just deferred pain.

## Consequences

**Positive:**
- One whole class of bugs (silent FX miscalculation) is structurally
  impossible.
- Reports honestly reflect what the data is: separate amounts in
  separate currencies.
- Per-jurisdiction handling (Indian vs US bank statement formats,
  date/number formats) inherits cleanly from `country` field.
- Multi-account UX with one OAuth grant per email account works
  naturally; each connector skill loops over `email_accounts WHERE
  active=1`.

**Negative:**
- Slightly verbose query results (dicts of currency→amount instead of
  scalars). UI/CLI must render them well.
- The `iris finance show --convert-to` flag is one more concept to
  document. Worth it.
- A skill author who forgets to set `currency` on a monetary column
  hits a NOT NULL constraint at write time. Documented in the finance
  schema authoring guide.

**Reversibility:** moderate. Removing the currency column from all
finance tables would be a destructive schema migration; the user-visible
UX (per-currency dicts) could be replaced with a base-currency view via
a config flag without touching the schema.

## Related ADRs

- ADR-0006 (PDF-from-email finance ingestion) — where statement
  currencies enter the system.
