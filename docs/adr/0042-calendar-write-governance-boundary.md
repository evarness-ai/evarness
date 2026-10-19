# ADR-0042: Calendar write governance as a boundary module (not a governor-policy extension)

Status: Accepted (2026-06-14)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. the code it names is in the private domain tree

Context: Calendar agent C2 (read/write skill split + write governance + brief + event reminders). Builds on ADR-0040 (provider abstraction) + ADR-0041 (Google provider). Implements the intent of ADR-0008 (R2/R3 calendar write split) and reuses ADR-0005 (reminders soft-migration).

## Context

ADR-0008 specced the calendar write-approval split as an extension to the
**governor policy** schema:

```yaml
- route: calendar/write
  approval: { mode: auto-with-undo-window, window_seconds: 600,
              escalate_to: prompt, escalate_when: { attendees_present: true } }
```

But the IRIS governor as built (`iris_harness.governor`) is a **coarse boolean route
guard**: `GovernorRoutePolicy` has `requires_approval: bool` and the engine
checks `metadata.approval_granted` — there is no conditional `escalate_when`,
no approval *modes*, and no undo-window primitive anywhere. The governor is a
shared kernel (coding/git, coding/mcp, llm/cloud/* …); teaching it
conditional escalation + a stateful undo window to satisfy one domain would
add cross-cutting surface and blast radius to code every route depends on.
(Other domains — finance/*, task/* — don't even register their routes in
`policy.yaml`; tool execution doesn't hard-depend on the governor.)

C2 needs the *behaviour* ADR-0008 describes — R2 auto-with-undo for
private/solo events, R3 prompt for attendee events — without destabilising
the shared governor.

## Decision

1. **Implement the R2/R3 split + undo as a calendar boundary module**
   (`iris_harness.calendar.write_governance.CalendarWriteGovernor`), not as a
   governor-policy feature. Deterministic, pure-domain, fully unit-testable
   offline — the same boundary-module pattern Finance used for its
   consequence logic. The governor remains the coarse `calendar/write` gate
   (unchanged); the *fine-grained* tier decision lives next to the domain
   that understands events.

2. **Tier from event shape (`CalendarEvent.has_attendees`).**
   `classify_write(has_attendees)` → `R3` for attendee events, `R2`
   otherwise. Updates/deletes consider the *prior* event's attendees too (so
   editing or removing an attendee event is R3).

3. **R2 = perform-now + undo token.** The write happens immediately and a row
   is logged in a new `calendar_undo` table (`token, action, event_id,
   snapshot_json, created_at, expires_at, undone_at`) with a **600s window**.
   `undo(token)` reverts within the window: create→delete, update→restore
   prior snapshot, delete→re-create prior snapshot (via the new
   `CalendarService.restore_event`, which re-materialises under the original
   id). Single-use; expired/used/unknown tokens are refused.

4. **R3 = require explicit approval.** Without `approved=True` the write is
   **not performed**; the caller gets a message to confirm. The CLI surfaces
   this as `--yes`; the `calendar-compose` tool as `approve=true`.

5. **Read/write skill split.** `calendar-events` becomes **read-only**
   (`list_events`, `list_today`) and stays default-on. The write surface
   moves to a new **opt-in** `calendar-compose` skill (`default_enabled:
   false`) — `create_event` / `update_event` / `delete_event`, each routed
   `calendar/write` and governed by `CalendarWriteGovernor`. Calendar writes
   are consequential, so the write skill is off until enabled.

6. **Event reminders reuse `notifications.ReminderStore`** with
   `target_kind="event"` (ADR-0005) — no new reminder primitive.
   `schedule_event_reminder` / `schedule_reminder_before` are thin helpers;
   the channel layer's title lookup is extended to read the event summary
   from `calendar.db` so a fired event-reminder reads "Event: <summary>".

7. **Brief "today's events" section** via a `list_today` read tool + a
   `todays_events` slot in the morning-briefing manifest.

## Alternatives considered

- **Extend `GovernorRoutePolicy` with `escalate_when` + approval modes +
  an undo store (literal ADR-0008).** Rejected for C2 — high blast radius on
  a shared kernel for one domain's needs; the governor's job is coarse
  allow/deny/approve, not domain consequence modelling. Can still be done
  later if multiple domains want conditional escalation; the boundary module
  doesn't preclude it.
- **Keep `create_event` in the default-on `calendar-events` skill.** Rejected
  — that bypasses governance (a default-on tool would write attendee events
  with no R3 gate). Writes belong behind the opt-in governed skill.
- **A new reminder primitive for events.** Rejected — `ReminderStore` already
  models `target_kind="event"` (ADR-0005); reuse, don't reinvent.
- **In-memory undo window.** Rejected — undo must survive across CLI
  invocations / processes, so it's persisted in `calendar.db`.

## Consequences

**Positive:**
- ADR-0008's R2/R3 behaviour ships, fully tested offline (11 governance
  tests), with zero change to the shared governor.
- Clean read/write separation; writes are opt-in and uniformly governed
  (CLI + skill share `CalendarWriteGovernor`).
- Undo is durable and single-use; event reminders reuse existing delivery.

**Negative / deferred:**
- The governor `policy.yaml` is not the source of truth for the calendar
  tier decision — it lives in the boundary module. Documented here so it's
  discoverable.
- R3 "prompt" is realised as "requires explicit approval" (`--yes` /
  `approve=true`), not yet an interactive channel round-trip via the
  ApprovalQueue — a later integration.
- Google write-back is still deferred: the provider is read-only (C1) and
  pushing writes needs the `calendar.events` scope (re-auth). C2 writes
  target the ICS provider + store.

## Related ADRs

- ADR-0008 (R2/R3 split — intent realised here), ADR-0005 (reminders
  migration), ADR-0040 (provider abstraction), ADR-0041 (Google provider),
  ADR-0029 (brief slots).
