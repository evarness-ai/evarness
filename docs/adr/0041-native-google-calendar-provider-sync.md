# ADR-0041: Native Google Calendar provider — OAuth, provider mapping, syncToken delta

Status: Accepted (2026-06-14)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Calendar agent C1 (second slice; builds on ADR-0040's provider abstraction). Companions: ADR-0003 (Keychain creds), ADR-0016 (`email_accounts` cross-cutting registry / `calendar.db`), ADR-0040 (provider abstraction, ICS first). Mirrors the Gmail connector shape (`gmail_oauth.py`, history-delta sync).

## Context

ADR-0040 shipped the provider abstraction with one provider (ICS, write-only,
no live feed). To read a user's real calendar we need a **native Google
Calendar** provider — per the standing user direction: *ICS (Apple) first,
then native Google; no external MCP.* It must plug into the existing
`CalendarProvider` interface with **zero changes** to the store, service,
skills, or CLI semantics, and reuse the established Google connector
machinery (OAuth installed-app flow, Keychain credential storage, the
`email_accounts` registry) rather than reinventing it.

## Decision

1. **Reuse the Gmail connector shape for OAuth.**
   `iris_harness.identity.gcalendar_oauth` mirrors `gmail_oauth` exactly —
   installed-app flow, tokens in Keychain under `KEYRING_PROVIDER =
   "gcalendar"`, accounts registered in the ADR-0016 `EmailAccountStore`
   with `provider = "gcalendar"`. Scope is **read-only**
   (`calendar.readonly`) for C1; the authorized address is resolved from
   `calendarList().get(calendarId="primary")["id"]` (the primary calendar's
   id *is* the account email). `login`/`status`/`logout`/`load_credentials`
   parallel Gmail one-for-one — one mental model for every Google connector.

2. **Pure mapping functions, separate from I/O.**
   `google_event_to_calendar_event(ev, *, calendar_id)` and
   `calendar_event_to_google_body(event)` are pure dict↔`CalendarEvent`
   transforms (timed vs all-day `dateTime`/`date` nodes, attendee emails,
   `cancelled` status preserved, deterministic
   `id = "google:{calendar_id}:{gid}"`). The `GoogleCalendarProvider`
   (a `CalendarProvider`) is a thin shell over `events().{list,insert,patch,
   delete}` that delegates all shape logic to those functions — so mapping
   is unit-testable with **no network and no fakes**.

3. **Incremental delta via `syncToken`, stored per (account, calendar).**
   `google_sync.sync_calendar` is the cold-start-vs-delta analogue of the
   Gmail history sweep. First run (no token) pulls a **bounded window**
   (`timeMin = now − 30d`, `singleEvents=True`); subsequent runs send the
   stored `syncToken` with `showDeleted=True`. A new `calendar_sync` table
   (`account_id, calendar_id, sync_token`, PK `(account_id, calendar_id)`)
   holds the cursor. `nextSyncToken` is persisted only after the last page.

4. **410 → automatic full resync.** An expired `syncToken` returns HTTP 410;
   the sweep clears the token, resets counters, sets
   `fell_back_to_full=True`, and re-runs from the bounded window in the same
   call. Idempotent: re-running upserts the same rows.

5. **Cancellations tombstone locally.** Mapped events with
   `status == "cancelled"` (surfaced by `showDeleted`) call
   `store.delete_event` rather than upsert, so a deletion on Google removes
   the local row. All other events upsert by the deterministic id.

6. **Read-only into the authoritative store.** Sync never writes to Google;
   it only populates `calendar.db`, which remains the single read model
   (ADR-0040). `create/update/delete_event` on the provider exist for the C2
   write path but are not exercised by sync.

7. **CLI surface.** `iris calendar sync [--account --calendar --db-path]`
   iterates connected `gcalendar` accounts (soft-fails per account), and
   `iris auth gcalendar login|status|logout` mirror the Gmail auth commands.

## Alternatives considered

- **Poll `events.list` by time window every run (no syncToken).** Rejected —
  re-downloads the whole window each sync, can't observe deletions cheaply,
  and doesn't scale. `syncToken` is Google's first-class delta primitive.
- **A separate `gcalendar_accounts` table / credential store.** Rejected —
  reuse the ADR-0016 `email_accounts` registry + Keychain (`provider`
  column already discriminates). One identity substrate for all Google
  connectors.
- **Request read/write scope now.** Rejected for C1 — least privilege; the
  write scope arrives with the C2 write-governance path (ADR-0008), not the
  read sweep.
- **Parse ICS exported from Google.** Rejected — lossy, no delta, no
  deletions; the API + `syncToken` is the live feed ADR-0040 deferred.

## Consequences

**Positive:**
- Google plugs into the existing abstraction with no store/service/skill/CLI
  semantic changes — the abstraction paid off.
- Mapping is pure and fully unit-tested offline (timed/all-day/attendees/
  cancelled/no-id, paginated list, initial→delta, cancellation-tombstone,
  410→full-resync) — 9 new tests, no network.
- One Google connector mental model (OAuth/Keychain/registry identical to
  Gmail); least-privilege read-only scope.

**Negative / deferred:**
- Read-only: local edits don't propagate to Google yet (C2 write path).
- One calendar per sync call (`primary` default); multi-calendar fan-out is
  a caller loop, not built in.
- Bounded 30-day cold-start window is a default, not yet configurable per
  account.
- mypy emits `no-untyped-call`/`no-any-return` on the google-auth surface —
  same accepted pattern as the merged `gmail_oauth.py`; mypy is not CI-gated.

## Related ADRs

- ADR-0003 (Keychain creds), ADR-0016 (`email_accounts` registry /
  `calendar.db`), ADR-0040 (provider abstraction, ICS first), ADR-0008
  (calendar write R2/R3 — C2), ADR-0029 (brief slots — C2).
