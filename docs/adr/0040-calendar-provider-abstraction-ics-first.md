# ADR-0040: Calendar agent foundation — provider abstraction, ICS first, one ICS implementation

Status: Accepted (2026-06-14)
Context: Calendar agent C0 (first of the 7 PAIA agents' calendar build). Companions: ADR-0003 (Keychain creds, for the future Google provider), ADR-0005 (reminders soft migration), ADR-0008 (calendar write R2/R3), ADR-0016 (calendar.db reserved). Canonical: `personal-assistant-upgrade.md` §3.3.

## Context

IRIS had no native calendar: only a legacy `calendar-reminders` skill +
`src/iris_harness/reminders.py` that writes local `.ics` files and *optionally*
mirrors to an external calendar **MCP** (disabled by default). The user
direction for the Calendar agent: build it **modular and extendable** —
**ICS (Apple Calendar) first, then native Google Calendar**; **migrate the
legacy ICS code, don't reinvent, remove duplicates**; **don't consume
external MCPs** (instead expose IRIS's own calendar tools *as* an MCP for
open-source users later).

## Decision

1. **Provider abstraction (`iris_harness.calendar.provider.CalendarProvider`).**
   One interface — `create/update/delete/list_event(s)` returning a
   `ProviderEventRef` — so ICS and Google are interchangeable. Skills, the
   store, and the brief depend only on the provider-agnostic
   `CalendarEvent` contract (`iris_harness.skills.contracts.calendar`).

2. **ICS provider first (`ICSCalendarProvider`).** Materialises one `.ics`
   per event under `data/calendar/ics/` (Apple Calendar / any RFC-5545
   client). Write-oriented: reads come from the store, not by polling files.

3. **Single ICS implementation.** The canonical RFC-5545 writer lives in
   `iris_harness.calendar.ics` (`render_ics_event` pure → string, `write_ics_event`
   persists; supports timed + all-day + attendees + alarms). The legacy
   `iris_harness.reminders` module now **delegates** to it — its duplicate
   `_write_ics_event`/`_ics_escape`/`_ics_datetime` were removed. One ICS
   code path, no behaviour change (40 legacy reminder/calendar tests green).

4. **`CalendarStore` over `data/calendar.db`** (the ADR-0016-reserved DB):
   authoritative record of events (provider-agnostic), window + by-source
   queries; `CalendarService` orchestrates store + the selected provider.

5. **No external MCP.** The new path never calls an external calendar MCP.
   The legacy skill's optional MCP mirror is left untouched (soft migration,
   ADR-0005) and is not the way forward. IRIS's own calendar tools are meant
   to be exposed *as* an MCP later (C3).

6. **Approval-ready.** `CalendarEvent.has_attendees` is surfaced now so the
   C2 write path can apply ADR-0008's split (attendee events → R3 prompt;
   solo/private → R2 auto-with-undo).

## Alternatives considered

- **Extend the legacy `calendar-reminders`/`reminders.py` in place.**
  Rejected — it conflates reminders+ICS+MCP and carries the deprecated
  status-bearing reminder model (ADR-0005). The Calendar agent is a clean
  native build; the legacy path soft-migrates.
- **Lean on an external calendar MCP as the primary connector.** Rejected
  per user direction + the ADR-0006 "native core, no SaaS intermediary"
  stance. MCP is for *exposing* IRIS, not depending on others.
- **Skip the store; read ICS files back.** Rejected — ICS-on-disk is lossy
  to parse and there's no live feed; the DB is the authoritative read model
  (and what Google sync will populate).

## Consequences

**Positive:**
- Modular: Google plugs in as a second provider with zero changes to
  skills/CLI/store/brief.
- One ICS implementation; the duplicate is gone.
- Usable now: `iris calendar add|list|delete` + `calendar-events` skill,
  events open in Apple Calendar.

**Negative / deferred:**
- ICS provider can't read external edits (no live feed) — that's the
  native Google provider's job (C1).
- Reminder soft-migration continues; the legacy `calendar-reminders` skill
  still exists alongside this one until retired.
- Multi-account, sync, write-governance, brief, meeting-prep, MCP-exposure
  are C1–C3.

## Related ADRs

- ADR-0003 (Keychain — future gcal OAuth), ADR-0005 (reminders migration),
  ADR-0008 (calendar write R2/R3), ADR-0016 (calendar.db), ADR-0029
  (brief slots — future calendar section).
