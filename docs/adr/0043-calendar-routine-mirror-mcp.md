# ADR-0043: Calendar C3 — meeting-prep routine, reconcile-based task/bill mirror, stdlib MCP server

Status: Accepted (2026-06-14)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. only the heartbeat wiring is here (`config/heartbeats.yaml`, `agent_metadata.py`); the calendar package is private

Context: Calendar agent C3 (final slice). Builds on ADR-0040 (provider abstraction), ADR-0041 (Google provider), ADR-0042 (write governance). Touches ADR-0005 (tasks/reminders), ADR-0006/0039 (finance bills), and the heartbeat/routine subsystem.

## Context

C3 delivers the three deferred calendar capabilities: (1) a daily
**meeting-prep** routine, (2) a **task/bill → calendar mirror**, and (3)
exposing IRIS's calendar tools **as an MCP server** (the open-source
distribution goal — IRIS consumes no external calendar MCP, it *serves* its
own). Each touches a different subsystem, and each has a design fork.

## Decision

### 1. Meeting-prep as a daily heartbeat (not a RoutineSpec)
A new `meeting_prep` heartbeat (cron `0 18 * * *`) runs
`iris_harness.calendar.meeting_prep.run_meeting_prep`, mirroring the
`FinanceMonitorHandler` shape (dataclass `__call__ → HeartbeatRun`, factory,
never crashes). It scans **tomorrow's meetings** (events with attendees) and
upserts one **dedup-keyed** prep task per meeting (`meeting_prep_key`,
`source_kind="calendar-prep"`, due at the event start). Chosen over a
user-authored `RoutineSpec` because meeting-prep is a built-in capability,
not a user-defined routine; the heartbeat is the same primitive
finance-monitor uses. The prep task description is assembled
**deterministically** from the event (time/location/attendees) — richer
synthesis (email threads + wiki entities per attendee) layers on top of
these tasks later and is not a prerequisite.

### 2. Task/bill mirror by reconciliation (not event subscription)
`iris_harness.calendar.mirror` exposes `reconcile_tasks_to_calendar` and
`reconcile_bills_to_calendar`: each run ensures every eligible source object
has exactly one event (created, or **moved** when its due date/title
changed) and **deletes** events whose source is gone or no longer eligible.
Run by a `calendar_mirror` heartbeat (`interval:900`) + `iris calendar
mirror`. Reconcile over event-subscription because:
- **Bills emit no lifecycle events** (`FinanceStore.upsert_bill` is silent),
  so subscription isn't even possible for them — reconcile is the only
  model, and using it for tasks too keeps **one** model.
- Reconcile is **deterministic and idempotent**, fully unit-testable with no
  bus wiring; it converges to the same end-state subscription would
  (move-on-due-change, delete-on-complete/drop) without per-call-site
  guarantees that every `TaskStore` is bus-connected.

Eligibility = task `open`/`doing` + `calendar_visible` + `due_at` set
(tasks already carry `calendar_visible`/`calendar_event_id`); active bills
with a `due_date` (all-day events). Mirror writes are **system-internal**
(no attendees → never an ADR-0008 prompt), so they go straight through
`CalendarService`, bypassing `CalendarWriteGovernor`.

### 3. MCP server on the stdlib (no `mcp`/`fastmcp` dependency)
`iris_harness.mcp.server` is a spec-subset MCP server — newline-delimited JSON-RPC
2.0 over stdio, **stdlib only** — exposing IRIS skill tools (default:
`calendar-events`). Implements the methods clients use to drive tools:
`initialize`, `notifications/initialized`, `ping`, `tools/list`,
`tools/call`. Run by `iris mcp serve [--skill ...]`. Chosen over adding the
`mcp` SDK because:
- It's the **distribution surface for open-source users** — a zero-dependency
  embeddable server is friendlier than pulling a heavy SDK, and avoids a
  `poetry lock` (the SDK is present transitively but undeclared).
- The protocol subset tools need is small and stable; the handler
  (`IrisMCPServer.handle(request) → response|None`) is a pure function, so
  it's fully unit-testable without real stdio.
- Tools are invoked through their LangChain `BaseTool` interface, so any
  governance they carry (calendar-compose's R2/R3) still runs inside `_run`.

## Alternatives considered

- **Meeting-prep as a RoutineSpec** driven by `routine_tick`. Rejected —
  RoutineSpec is for *user-authored* routines (approval workflow); a built-in
  capability is a heartbeat.
- **Event-driven mirror** (subscribe to `task.created/updated/completed`).
  Rejected as the primary mechanism — doesn't work for bills (no events),
  and couples correctness to every `TaskStore` being wired to the default
  bus. Could be added later for lower latency on top of reconcile.
- **Emit bill lifecycle events from `FinanceStore`.** Deferred — larger
  change to finance for marginal benefit when daily reconcile suffices.
- **Adopt the `mcp` SDK.** Deferred — heavier dep + lock churn for a feature
  whose value is being lightweight and embeddable. The stdlib handler can be
  swapped for the SDK later without changing the tool-loading layer.

## Consequences

**Positive:**
- Three capabilities ship, each deterministic and offline-testable
  (meeting-prep idempotent; mirror create/move/delete idempotent; MCP handler
  pure). No new third-party dependency.
- One mirror model for tasks and bills; system-internal writes don't trip
  the user-facing governance prompt.
- IRIS's calendar is now consumable as an MCP by any client — the
  open-source goal — with governance preserved.

**Negative / deferred:**
- Mirror latency is the heartbeat cadence (15 min), not instant; an
  event-driven fast path is a future add.
- Meeting-prep synthesis is deterministic (event fields only); LLM synthesis
  from email threads + wiki entities is future work.
- The MCP server is a protocol *subset* (tools only — no resources/prompts);
  fine for tool serving, extend if needed.
- Google write-back remains deferred (read-only provider; needs
  `calendar.events` scope) — mirror/compose target the ICS provider.

## Related ADRs

- ADR-0040/0041/0042 (calendar C0–C2), ADR-0005 (tasks/reminders),
  ADR-0006/0039 (finance bills), ADR-0008 (write governance — mirror bypasses
  it as system-internal).
