# ADR-0075: Calendar as the single write authority (events, invites, appointments, reminders)

Status: Accepted (2026-06-23)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. no CalendarService or CalendarWriteGovernor in this repository

Relates to: ADR-0008 (calendar write governance, R2/R3 attendee split), ADR-0016 (per-domain DBs), issue 0025 (calendar agent read path)

## Context

The calendar agent can now *read* events (issue 0025). The user wants it to also
**create** events, meeting invites, appointments, and reminders — and to push
those out to Google and Apple calendars, not just sync inward.

Investigating the current surface (5 parallel subsystem deep-dives) found that the
write machinery is far more built than expected, but **ownership is fragmented
across two parallel time-management subsystems that do not share a store**:

1. **`iris_harness.calendar`** — the modern system. `CalendarService.add_event/update_event/
   delete_event` already writes through to **both** a provider and the SQLite store
   (`data/calendar.db`, `service.py:84,88`). `CalendarWriteGovernor` (ADR-0008 R2/R3
   + 600s undo) exists and is invoked by the CLI and skill tools — but **not**
   auto-wired into the service, so `service.add_event` can be called unguarded
   (the task/bill mirror deliberately does). Providers satisfy a `CalendarProvider`
   Protocol (`create/update/delete/list_events`). `GoogleCalendarProvider` implements
   full CRUD over the Google API (`google_provider.py:147-172`) with complete body
   mapping; it is simply **not enabled** (OAuth defaults to `calendar.readonly`,
   and the provider isn't registered in `CalendarService.default()`).

2. **`iris_harness.reminders`** — the legacy system. "remind me to X at 5pm" is caught by a
   deterministic chat intercept (`_handle_reminder_creation_turn`) that writes to a
   **separate markdown store** (`~/.iris/reminders/reminder.md` + companion `.ics`),
   with its own delivery heartbeat (`reminder_tick`) and delivery ledger. It never
   touches `data/calendar.db` (the chat intercept even passes `call_calendar_mcp=False`).

The **planner** is read-only — it aggregates calendar + tasks + bills + followups and
never writes. **Tasks** are their own store (`TaskStore`, `data/tasks.db`).

## The conflict

- **Split brain.** A reminder lands in `reminder.md` (gets *delivered* but is invisible
  to the calendar agent and planner). A calendar-agent-created event lands in
  `calendar.db` (shows on the calendar/planner but `reminder_tick` never fires it).
  Reminders that notify but aren't on the calendar; events on the calendar that never
  notify.
- **Double front-door.** The reminder intercept fires before intent routing and grabs
  "remind me…"; a calendar create flow would grab "schedule/add a meeting…". Overlapping
  phrasings could double-create.
- **Governance.** Meeting *invites* = attendee events = **R3** (require approval,
  ADR-0008). Reminders today bypass governance entirely.

## Decision

**The calendar subsystem (`data/calendar.db` via `CalendarService`) is the single
write authority for all time-bound items: events, meetings/invites, appointments,
and reminders.** A reminder is simply a no-attendee event carrying an alarm
(`alarm_minutes_before > 0`, `source="reminder"`); the planner shows it for free.

Role separation (no merging beyond this):
- **Calendar owns writes** — one governed front door (`CalendarService` wrapped by
  `CalendarWriteGovernor`); writes fan out to the store **and** the active provider(s).
- **Planner owns the aggregated read view** — unchanged; reminders appear automatically
  once they live in `calendar.db`.
- **Tasks owns to-dos** — unchanged; `calendar_mirror` keeps projecting due tasks onto
  the calendar for viewing.

Resolutions to the conflict:
- **Split brain → unify on `calendar.db`.** Reminders become alarm-bearing events
  (`source="reminder"`). A one-time importer migrates `reminder.md`; the chat reminder
  intercept is rewired to write through `CalendarService`; `reminder_tick` delivery is
  repointed to read due reminder-events from `calendar.db` (delivery idempotency via the
  existing `notifications` store).
- **Double front-door → keep the deterministic intercept, change its sink.** The robust
  regex/lexical reminder parser stays (local-model tool-calling is unreliable — same
  rationale as issue 0025/0024); it just persists via `CalendarService` instead of
  `reminders.py`. A parallel deterministic create intercept handles
  "schedule/add a meeting|appointment".
- **Governance → route writes through the governor.** Attendee events (invites) hit the
  R3 approval gate; private events/reminders are R2 (auto + undo window).

Apple/iCloud write-back: **EventKit via `pyobjc` with an `osascript` fallback**,
darwin-gated. Rationale: privacy-first (no stored credentials — uses the OS Calendar
authorization), local (writes to Calendar.app, which syncs to iCloud), least-credential
(no app-specific password or separate OAuth). Rejected: CalDAV-to-iCloud (needs an
app-specific password + credential plumbing) and ICS-import (no real write-back).

## Consequences

- One source of truth for everything time-bound; the planner and calendar agent see
  reminders and events alike.
- `reminders.py` write functions become thin shims/deprecated; `reminder.md` is migrated
  and retired. A transition window runs both readers until the importer has run.
- Enabling Google write requires a **scope upgrade + user re-auth**
  (`iris auth gcalendar login` with `calendar.events`). Apple write requires macOS +
  the EventKit framework (optional dependency; degrades to `osascript`).
- Invites become governed (R3); reminders/private events stay R2 with the undo window.

## Rollout (dependency-ordered slices, one PR each)

- **Slice 1 — foundation (no deps):** `CalendarEvent.alarm_minutes_before` + `"reminder"`
  in `EventSource`; additive `calendar.db` migration (`alarm_minutes_before` column via
  an idempotent `_add_column_if_missing`). *This PR.*
- **Slice 2 — governed front door:** make `CalendarService` route writes through
  `CalendarWriteGovernor` (opt-in injection; mirror keeps its documented bypass).
- **Slice 3 — Google write-back:** add `calendar.events` scope, register
  `GoogleCalendarProvider` in `CalendarService.default()` with account context, re-auth
  guide. *(parallelizable after slice 1)*
- **Slice 4 — Apple provider:** new `apple_provider.py` (EventKit + osascript fallback),
  darwin-gated registration. *(parallelizable after slice 1 — new file)*
- **Slice 5 — reminders unification:** *(shipped)* the chat reminder intercept now
  creates a `calendar.db` event (`source="reminder"`) and schedules delivery via the
  existing notifications `ReminderStore` event bridge (`schedule_event_reminder`) —
  fired by the already-wired `notification_reminder_tick`, which enriches the message
  from `calendar.db`. So no new delivery path was needed; the legacy markdown
  `reminder_tick` is left in place (now reads an empty store) and is deprecated. A
  one-time importer (`calendar/reminder_migration.py`, `iris calendar import-reminders`)
  migrates active `reminder.md` rows (idempotent via `source_id`). *(depends on slice 1)*
- **Slice 6 — chat create flow:** *(shipped)* a deterministic `_handle_meeting_creation_turn`
  intercept (in both `chat` and `chat_stream`, after the reminder intercept): parses
  "schedule/add a meeting|appointment|event … at <time>" (reusing the reminder
  time/date extractors) and creates the event through the governed `CalendarService`.
  No-attendee events are R2 (auto-created + undo); attendee **invites** are R3 — the
  chat path does NOT auto-send them, it reports that approval is needed (use the
  calendar CLI), so invites are never sent by mistake. *(depends on slices 1, 2)*

## Timezone handling (post-merge fixes #271, #272)

Live-verifying the create flow exposed a day-boundary bug: events are **stored as
aware UTC**, but the read surfaces computed their day windows in **UTC**, so once UTC
passed local midnight "any meeting tomorrow?" / "what's on today" selected the wrong
day, and times displayed in UTC. The rule, applied to both read surfaces:

- **Store** in UTC (unchanged) — uniform `+00:00` keeps the SQLite ISO-string range
  comparison correct.
- **Windows** are the user's *local* calendar day: derive the timezone from the
  caller's `now` (default `datetime.now().astimezone()`), build the day's
  `[start, end]` in that zone, then convert to UTC instants for the store query.
- **Display** converts each event/item back to local time.
- Tests stay deterministic by passing a UTC-aware `now` (→ `tz=UTC` reproduces the old
  bounds); a pinned non-UTC zone test asserts the local-day + local-time behavior.

Applied in `calendar/digest.py` (`parse_window`, `build_calendar_digest`, `_fmt_event`)
and `planner/plan.py` (`build_daily_plan` and the item builders). The Planner shared
the same latent UTC-day assumption, so it was fixed in the same way for parity.

## Planner target day (post-merge enhancement #273)

With the calendar now writable, the Planner — previously a today-only synthesizer —
accepts a target day. `parse_target_day(query, now)` recognizes today/tonight,
tomorrow, day-after-tomorrow, an ISO date, and weekday names (next occurrence);
`build_daily_plan(..., target_date=...)` plans that local day (events in the window,
tasks due that day, bills as-of that day). Overdue tasks are surfaced **only** when
planning the current day — a future plan shows just that day's items, no carryover.

## Amendment (2026-09-13, OSS plan M5.7 track A)

The single write authority stands; its **home** moved. `CalendarService` and
`CalendarWriteGovernor` are now `iris_harness.plugins_builtin.calendar.service`
/ `.write_governance`, with the concrete providers, their sync and Google OAuth, under the
owner's rule that core provides mechanisms and anything that talks to an external app is
a plugin. The chat paths that write (the `reminder_creation` and `meeting_creation`
intercepts and the `calendar_event` confirmation executor) moved with it. What the core
keeps is the governance *mechanism*: the confirmation intercept, the continuation the
question rides on, and the executor registry it dispatches by kind. The store the
service writes stays core, because the planner and the `calendar_lookup` tool read it.
