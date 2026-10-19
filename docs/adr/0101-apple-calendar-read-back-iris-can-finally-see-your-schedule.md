# ADR-0101: Apple Calendar read-back — IRIS can finally see your schedule

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. only the `apple_calendar_sync` heartbeat entry is public

> Source: section `### ADR-0101: Apple Calendar read-back — IRIS can finally see your schedule` of the project decisions log (`project-iris-prd/13-decisions-and-rationale.md`), extracted on 2026-10-18 for the Evarness repository. It predates the rename from IRIS and is kept as written.

**Decision**: The Apple/iCloud provider gains real **read-back**. `AppleCalendarProvider.list_events`
now queries EventKit over a date window and maps each `EKEvent` to a `CalendarEvent`
(`provider="apple"`, `source="apple"`, stable id `apple:<identifier>:<start-epoch>`). A new
`src/iris_harness/calendar/apple_sync.py` (`sync_apple_calendar`, mirroring `google_sync`) upserts the
window into `calendar.db` and tombstones `source="apple"` rows that vanished — never touching
Google/IRIS-owned events. It runs on `iris calendar sync` and on a new `apple_calendar_sync`
heartbeat (15-min, darwin-only). Reads require **full access**: `_ek_read_authorized` is
`status == 3` (not `>= 3`), because write-only (status 4, macOS 14+) lets IRIS create events
but not read them — exactly the bug. `iris calendar apple-access` now requests *full* access
(`requestFullAccessToEventsWithCompletion:`, run-loop-pumped with a timeout).

**Rationale**: "How is my schedule today?" returned "no events" while the user had events in
Apple Calendar. Root cause (verified against the live store + EventKit): the Apple provider was
**write-only by design** — `list_events` returned `[]` ("read-back is a later slice") and
`iris calendar sync` only pulled Google, so `calendar.db` only ever held events IRIS itself
wrote. Compounding it, the macOS grant was **writeOnly (status 4)**: even with the read
implemented, the OS blocks it until the grant is upgraded to full. This is the canonical
band-aid trap ([[feedback_root_cause_no_bandaid]]) — the symptom is an empty digest; the cause
is two layers down (no read path + insufficient TCC grant). Fix both: implement EventKit read +
sync (read-only against Apple, same posture as Google), and make the grant path request full
access. The heartbeat is silent without full access (SKIPPED, never blocks on a prompt) so the
permission upgrade stays an explicit user action. Narrative
`docs/blog/2026-06-29-the-calendar-it-could-write-but-not-read.md`.
