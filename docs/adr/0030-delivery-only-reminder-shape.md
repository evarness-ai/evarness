# ADR-0030: Delivery-only Reminder primitive + channel routing — implementation shape

Status: Accepted (2026-06-10)
Context: Personal-assistant upgrade Phase 2 Track 2D. Parent: ADR-0005
(Task vocabulary + soft migration of `src/iris_harness/reminders.py`).

## Context

ADR-0005 locked the new vocabulary: `Reminder` is the delivery layer
(notification-at-a-time pointing to a Task/Goal/Bill/Event) with no
status of its own; status lives on the target. The 974-line legacy
`src/iris_harness/reminders.py` (status-bearing rows + ICS files + markdown
ledger) stays untouched until callers drain organically.

Phase 2 needs the new Reminder primitive available now — followups
(Track 2B) are the immediate consumer ("remind me if no reply by
due_at") and bills (Phase 4) will be the second. The legacy module
cannot serve that role because its `ReminderRecord` carries the
status field ADR-0005 specifically removed.

This ADR records the shape calls made to ship the new primitive
without touching the legacy module, plus the channel-routing
subscriber that turns `reminder.fired` events into user-facing
notifications.

## Decision

Ten implementation decisions:

1. **New package `src/iris_harness/notifications/`** (not
   `src/iris_harness/reminders_v2/` or `src/iris_harness/tasks/reminders.py`).
   The primitive will absorb Bill (Phase 3) and Event (Phase 6)
   targets, so a tasks-subpackage home would force a future
   rename. The legacy `src/iris_harness/reminders.py` cannot be reused
   as the module name. `notifications/` is the clean semantic
   for "delivery-time event about a target" without overloading
   either existing name.

2. **Schema lives in `data/tasks.db`** (table
   `notification_reminders`), not a new `data/notifications.db`
   or `data/reminders.db`. Phase 2 only ever points at Tasks
   and Goals — both in tasks.db — so a separate file would mean
   a cross-file FK for no benefit. Table name prefixed with
   `notification_` to coexist with the legacy concept without
   risk of collision. Indexes: `(target_kind, target_id)` for
   visibility queries; partial on `remind_at WHERE fired_at IS
   NULL AND dismissed_at IS NULL` for the tick path.

3. **`Reminder` model carries no status field.** Three terminal
   columns instead: `fired_at`, `dismissed_at`, plus the
   absence of either = `pending`. Rejected: a single
   `status: Literal["pending", "fired", "dismissed"]` field —
   would mirror the legacy module's exact shape and re-introduce
   the conflation ADR-0005 set out to remove. The two-timestamp
   shape lets queries cheaply distinguish "fired but not
   dismissed" (delivered) from "dismissed but not fired"
   (cancelled before delivery).

4. **`fire` is idempotent on already-fired rows, raises on
   dismissed rows.** Re-firing a fired reminder is a delivery
   retry (delivery dedup is the channel's job, not the store's)
   and returns the row unchanged without re-emitting the event.
   Firing a dismissed reminder is a logic error worth raising
   for. Rejected: idempotent on both (would silently deliver
   reminders the user explicitly cancelled).

5. **`cancel` is idempotent on already-dismissed rows, no-op
   on fired rows.** Re-cancelling a dismissed reminder is
   safe. Cancelling a fired reminder doesn't undeliver — the
   notification already went out — so it returns the unchanged
   row. The CLI surfaces this distinction with an
   "already fired — no change" message instead of silent
   success.

6. **`reschedule` rejects terminal (fired or dismissed) rows.**
   Forces create-new + cancel-old instead of reviving a
   terminal row. Reason: a fired reminder's row carries the
   timestamp of when delivery happened, which is observable
   history; mutating its `remind_at` would rewrite that
   history. A new row is cheaper than that footgun.

7. **CLI `iris reminder add` auto-resolves the target prefix
   against tasks first, then goals**, with `--kind` forcing
   one or the other when a prefix matches both. Rejected:
   requiring `--kind` on every add (operationally hostile when
   90% of reminders are about tasks) or trying multiple kinds
   in parallel (the disambiguation message is clearer than a
   silent "first match wins").

8. **`--kind bill` and `--kind event` raise `Phase 3+` /
   `Phase 6` deferral messages.** The model's `TargetKind`
   already includes them so future phases need no model
   change, but the CLI explicitly refuses to create rows
   pointing at non-existent target stores. Rejected:
   accepting the kinds silently — would store rows with no
   target store to dereference, and `reminder.fired` consumers
   would have to handle ghost targets.

9. **New heartbeat `notification_reminder_tick` registered
   alongside (not replacing) the legacy `reminder_tick`.**
   Schedule: `interval:60` matching the legacy. Heartbeat
   handler constructs a `ReminderStore` with the runtime's
   default bus and calls `tick()`. Heartbeats.yaml description
   is explicit that the two are distinct so operators don't
   conflate. Rejected: replacing the legacy heartbeat with
   the new one — ADR-0005's soft-migration stance is "both
   coexist until callers drain".

10. **Channel routing via a separate subscriber module
    `iris_harness.notifications.channels.subscribe_reminder_fired_to_channels`.**
    The store fires + emits the event; the subscriber turns
    `ReminderFiredPayload` into a `ChannelMessage` and
    dispatches via `ChannelGateway.send`. Channel selection:
    `payload.channel` if registered AND not the literal string
    `"default"`; otherwise fall back to the runtime's
    configured `default_channel`. Body enrichment: looks up
    the target's `title` from `data/tasks.db` to render
    `"Task: <title> — <note>"` instead of generic `"Reminder:
    <note>"`. Soft-fails per-message — a connector raising
    does not crash the subscriber.

## Alternatives considered

1. **Reuse the legacy `src/iris_harness/reminders.py` module.**
   Rejected by ADR-0005 — the module's status field is the
   exact conflation we're moving away from, and the soft-
   migration stance requires both models to coexist.

2. **One unified table for reminders across all schemas
   (a new `data/iris.db.notifications` table).** Cleaner for
   future cross-domain queries. Rejected: Phase 2 targets are
   all in `data/tasks.db`; one more file boundary buys
   nothing today and is one more migration risk later.

3. **Store `status: ReminderStatus` instead of the two
   timestamps.** Mirrors the legacy shape exactly. Rejected
   per decision #3.

4. **Auto-complete the target task when its associated
   reminder fires.** Rejected: a reminder fires to *prompt*
   the user, not to *do* the work for them. The user
   completes tasks; reminders surface them.

5. **Channel routing inside the store's `fire` method**
   (i.e. `ReminderStore` calls `channels.send` directly).
   Rejected: couples the store to the channel layer; the
   store knows nothing about runtime configuration. The
   event-bus indirection lets tests assert against the event
   payload without instantiating a ChannelGateway and lets
   future subscribers (audit log, telegram dedup) attach
   without touching the store.

6. **Heartbeat that fans the firing out to channels
   itself** (same shape as the legacy `_build_reminder_tick_handler`
   which calls `runtime.channels.send` inline). Rejected:
   would skip the event-bus indirection and lose the "store
   fires → bus carries → subscriber delivers" separation. The
   legacy handler does it that way because the legacy module
   pre-dates the event bus; the new path doesn't have that
   constraint.

## Consequences

**Positive:**

- The new primitive is available to every downstream
  producer (followups today, bills in Phase 4, calendar prep
  in Phase 6) with one stable contract:
  `ReminderStore.create(target_kind=..., target_id=...,
  remind_at=...)` + subscribe to `reminder.fired`.
- The legacy module continues to function — its callers
  keep working unchanged — and the legacy heartbeat keeps
  delivering legacy-shaped reminders. No operator-visible
  flag day.
- The two-timestamp shape supports observability queries
  out of the box: "reminders fired in the last 24h",
  "reminders cancelled before delivery", "pending reminders
  past their remind_at" (= the system is failing to tick).
- 19 store + channel tests cover idempotence on every
  terminal transition, reschedule rejection of terminal
  rows, target-title enrichment, soft-fail on connector
  failure, and end-to-end `store.fire()` → bus → connector.

**Negative:**

- Two heartbeat handlers (`reminder_tick` and
  `notification_reminder_tick`) running on the same 60 s
  interval. Trivial cost (both finish in <10 ms when the
  queue is empty); the duplication is the price of
  soft-migration. Cleaned up when the last legacy caller
  drains.
- The CLI's `--kind bill` / `--kind event` rejection means
  Phase 3 and Phase 6 have to land their respective stores
  AND a per-kind target resolver before this CLI accepts
  the kind. The deferral message points at the correct
  phase so the operator isn't surprised.
- Channel selection rule "use payload.channel if registered,
  else default" silently swallows a typo in the payload's
  channel name. The subscriber logs the fallback at
  WARN, but a stricter mode (raise on unknown channel)
  would be more debuggable; deferred until a real misroute
  is observed.

**Reversibility:** high. The package is one folder with no
cross-cutting touches; the heartbeat registration is one
line in `_register_default_heartbeats`; the subscriber
wiring is two lines next to it. Removing the new primitive
restores Phase 1 state exactly; the legacy module is
untouched throughout.

## Related ADRs

- ADR-0005 (parent) — Task vocabulary + soft migration of
  the legacy reminders module. The "Reminder is delivery,
  not status" decision originates there; this ADR makes it
  real.
- ADR-0013 — event bus promotion convention. This module's
  `REMINDER_FIRED` topic lives with the producer per that
  convention.
- ADR-0028 (sibling) — `email-followup` skill that produces
  the followup tasks reminders will most often target.
- ADR-0029 (sibling) — brief integration that could surface
  recently-fired reminders in a future slot once a real
  use case appears.
- A future ADR will handle the legacy reminder_tick
  retirement once its callers have drained.
