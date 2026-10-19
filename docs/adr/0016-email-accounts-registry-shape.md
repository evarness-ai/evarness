# ADR-0016: Email accounts registry — implementation shape and DB location

Status: Accepted (2026-05-25)
Context: Personal-assistant upgrade Phase 0 track 7. Companion: canonical doc §3.1, §3.2; ADR-0007 (multi-currency model).

## Context

The canonical doc §3.1 names ``email_accounts`` as a NEW first-class
concept introduced by the Q16 design lock: a table that captures
which email accounts IRIS is connected to, with optional
``currency_default`` and ``country_hint`` per account that power the
multi-currency aggregation rule from ADR-0007.

Three things were left to implementation:
1. Where the table lives (the doc said ``email.db`` but the table is
   cross-cutting).
2. The shape of the primary-key (UUID, hash, or canonical slug).
3. Whether to ship empty per-domain DB scaffolds (``finance.db``,
   ``calendar.db``) alongside this table, or defer them.

Phase 0 track 7 implemented (commit ``c34b16a``) with six
implementation decisions resolved.

## Decision

Six implementation decisions:

1. **Scope: cross-cutting table only.** The remaining-work bullet
   listed "email.db, finance.db, calendar.db, email_accounts table"
   as the last Phase 0 track, but the only thing that genuinely
   blocks Phase 1 work is the cross-cutting ``email_accounts`` table.
   Per-domain schemas (``finance_statements``,
   ``finance_transactions``, ``calendar_events``, per-DB sync cursors,
   etc.) are deferred to their producer skills in Phases 3-6.
   Pre-creating empty DBs would be dead scaffolding.

2. **DB location: ``data/iris.db``, not ``data/email.db``.** The
   canonical doc §3.1 placed it in ``email.db`` (the table's name
   sounds email-ish). Rejected: ``email_accounts`` is **referenced by
   the future email skill, AND the finance-accounts join via
   ``email_account_id``, AND the calendar sweep**. Putting it under
   one domain's DB would mean ``finance-statements`` skill code has
   to ``sqlite3.connect("data/email.db")`` to resolve account
   currency, which is confusing. ``data/iris.db`` is the new
   cross-cutting registry; future cross-cutting tables (e.g., a
   ``wiki_canonicalization`` slug map) land there too.

3. **Primary key: canonical slug ``provider:address``, not UUID.**
   ``gmail:user.in@example.com`` is the row's ID. Reasons:
   stable across delete-and-re-add cycles (re-adding the same
   account doesn't orphan downstream references); human-readable
   in debugging; trivially derivable by callers without a lookup.
   Uppercase / whitespace normalized at write time so
   ``Gmail:user.in@example.com`` and ``gmail:user.in@example.com``
   resolve to the same row.

4. **Strict ISO-code validation at the Pydantic layer.**
   ``currency_default`` must be 3 uppercase letters (ISO-4217);
   ``country_hint`` must be 2 uppercase letters (ISO-3166-1
   alpha-2). Validation runs both on direct construction and on
   ``EmailAccountStore.update_currency_country``. Catches typos
   like ``"inr"`` (lowercase) or ``"India"`` (full name) before
   they hit the DB.

5. **``update_currency_country`` is a set-only API (cannot clear
   fields).** Passing ``None`` for a field leaves it unchanged.
   This is a deliberate MVP simplification — users who need to
   clear a currency/country hint can re-construct the row via
   ``deactivate`` + ``add``, or we add an explicit ``clear_*``
   method when there's a real use case. Avoids ambiguity in the
   None-vs-unset distinction.

6. **No bus / no events emitted in MVP.** EmailAccount mutations
   are rare (initial setup; occasional currency override).
   No current consumer would subscribe to them. Adding event
   emission later is additive; the API stays stable.

## Alternatives considered

1. **Pre-populate finance.db and calendar.db schemas now per
   canonical doc §3.2 / §3.3.** Pre-anticipates what Phase 3-6
   skills will need. Rejected: schema decisions are best made
   alongside their producers; pre-baking risks committing to
   columns that get revised. Phase 0 unblocks producers by
   establishing patterns (see ``src/iris_harness/tasks/`` from Track 5);
   it doesn't pre-write every schema.

2. **Put ``email_accounts`` in ``email.db`` per the literal
   canonical-doc text.** Matches the doc directly. Rejected: the
   table is cross-cutting in usage and the doc was inconsistent
   with its own §3.2 (which describes finance referencing email
   accounts). Fixed the doc in a follow-on commit.

3. **UUID primary keys.** Standard pattern (matches Task / Goal).
   Rejected for this table: slug is more useful here because the
   pair ``(provider, address)`` is the natural identity; UUID adds
   indirection (callers need to look up by address anyway). For
   user-domain tasks (created/updated frequently) UUIDs make more
   sense; for a tiny registry of accounts (changed rarely) the
   slug is cleaner.

4. **Lenient string validation for currency/country (e.g., accept
   ``"inr"``, normalize to ``"INR"``).** Friendlier UX. Rejected:
   silent normalization hides typos; better to fail loudly so the
   caller (CLI, config import) corrects at the source.

5. **Allow clearing currency/country with explicit ``""`` or
   sentinel.** More expressive API. Rejected for MVP: adds
   complexity for an edge case (almost no one sets then clears a
   country hint). Add explicit ``clear_currency_default()``
   method when needed.

6. **Emit ``email_account.added`` / ``.deactivated`` events from
   day one.** Matches the tasks-subsystem pattern (Track 5).
   Rejected: tasks emit because Phase 1+ consumers immediately
   subscribe (morning brief, calendar mirror); ``email_accounts``
   has no immediate consumer subscribing to lifecycle events.
   Add when a real subscriber lands.

## Consequences

**Positive:**
- Phase 1 (email read + classify) can begin with a clear surface:
  ``EmailAccountStore().list()`` returns "which Gmail addresses am I
  connected to," each with the currency/country context the
  classifier and ledger need.
- Cross-cutting tables have a clear home (``data/iris.db``)
  separate from per-domain DBs — pattern set for any future
  cross-cutting registry.
- Slug-based ID makes the data self-describing in debugging:
  ``SELECT * FROM email_accounts`` shows immediately
  recognizable IDs.
- Strict ISO validation prevents the entire class of
  "I typed inr instead of INR" bugs from polluting downstream
  finance aggregation.

**Negative:**
- The canonical doc §3.1 had a stale "email.db" reference for
  email_accounts. Fixed in a follow-on doc commit but the ADR
  reader should know there was a one-line correction.
- ``update_currency_country`` cannot clear a previously-set value
  for now. Users who want to clear must use the (currently
  nonexistent) recreate-via-deactivate-and-readd flow. If this
  becomes an issue in Phase 1+ user testing, we add explicit
  ``clear_*`` methods.
- No events means subscribers can't be notified when an account
  is added or deactivated. Phase 1+ skills that need this (e.g.,
  re-bootstrapping cursor state) will subscribe via the bus once
  emission is added.
- ``data/iris.db`` is a new file in the data directory that
  future operators need to know about. Documented; gitignored
  per ``data/`` convention.

**Reversibility:** high. Moving the table to ``email.db`` is a
schema migration + one path change. Adding event emission is
additive. The slug-vs-UUID PK decision is the hardest to reverse
(would invalidate any downstream FK that referenced the slug),
but no FKs exist yet — only references will land in Phase 3+
when finance_accounts ships with ``email_account_id``.

## Related ADRs

- ADR-0007 (multi-currency, no implicit FX) — the parent design
  that needs per-account currency_default and country_hint.
- ADR-0011 (Keychain credentials shape) — the credentials module
  stores OAuth tokens keyed by ``(provider, address)``; this
  table stores the *non-secret* per-account configuration. The
  two are separate concerns by design (ADR-0011 explicitly
  rejected co-mingling secrets and config).
- ADR-0014 (tasks subsystem) — sibling pattern for SQLite-backed
  domain stores; this module follows the same conventions
  (ensure_schema, Pydantic models, dict-based row helpers).
- ADRs 0010, 0012, 0013, 0015 — sibling implementation-shape
  ADRs from Phase 0.
- A future ADR will add per-domain schemas for finance_statements,
  finance_transactions, finance_bills, calendar_events as their
  producer skills land in Phases 3-6.
