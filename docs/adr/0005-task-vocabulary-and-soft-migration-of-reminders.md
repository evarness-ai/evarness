# ADR-0005: Task vocabulary + soft migration of the legacy reminders module

Status: Accepted (2026-05-24)

**Status pass (2026-10-18):** the decision holds; the code now lives in `evarness/services/tasks/`. the soft migration finished: legacy reminders.py is gone, delivery-only reminders are `services/notifications/` per ADR-0030; `followup_key` moved to the email plugin

Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §1, §3.4.

## Context

The user uses "to-dos, reminders, follow-up" interchangeably in
conversation. The existing codebase compounds the confusion:
`src/iris_harness/reminders.py` (974 lines) carries a status field
(`ReminderStatus = Literal["not started", "in progress", "completed"]`)
that belongs to a *task*, not a notification. The module also writes
ICS calendar artifacts and a markdown delivery ledger — three concerns
in one place.

The personal-assistant upgrade introduces email-derived tasks, bill
reminders, goal tracking, and meeting-prep tasks. Without disentangling
the vocabulary now, every new subsystem inherits the conflation and the
morning brief becomes incoherent.

## Decision

**Adopt a precise vocabulary** (see `personal-assistant-upgrade.md` §1):

- **Goal** — long-horizon outcome with success criteria.
- **Task** — actionable unit with status, optional `due_at`,
  optional `wait_for` (followup), optional `parent_task_id` (subtask),
  optional `dedup_key` (cross-producer identity).
- **Followup** — a Task variant (Task with `wait_for` set). Not a
  separate noun.
- **Reminder** — notification-at-a-time pointing to a Task/Goal/Bill/Event.
  Has no status of its own; the target carries the status.
- **Bill** — a finance-domain object (separate concern), can have
  Reminders pointing at it.

**Soft migration:** introduce a new `src/iris_harness/tasks/` module + `data/tasks.db`
for the new vocabulary. **Do not touch `src/iris_harness/reminders.py`.** New
code paths use Task/Reminder per the new vocabulary; legacy callers of
`reminders.py` (status-bearing rows) continue to function unchanged.
Status fields on the legacy `Reminder` model are deprecated organically
as callers migrate over months. No big-bang refactor blocks the
email/finance work.

**Three task-grouping mechanisms** (each solves a distinct problem):

1. `dedup_key` — pre-write deduplication across producers. Helpers in
   `src/iris_harness/tasks/dedup_keys.py`.
2. `parent_task_id` — explicit hierarchy. Tree-only, no DAG. Manual
   grouping via `iris task group <a> <b> --under "title"`.
3. Wikilink overlap — loose clustering by topic. Free from the wiki.

## Alternatives considered

1. **Hard refactor of `reminders.py` upfront.** Rewrite to delivery-only,
   migrate existing rows. Rejected: 974-line module touches enough
   existing callers that the refactor delays the email/finance work it
   was supposed to enable.
2. **Unified task model: one `tasks` table with `kind: goal | todo |
   followup | bill_reminder` discriminator.** Rejected: each kind has
   genuinely different semantics; nullable-field bag is the wrong
   shape.
3. **Treat everything as a Reminder.** Rejected: loses queryable
   structure ("show my open followups" becomes a filter chain across
   markdown rows).
4. **Followup as a peer noun to Task.** Rejected: every task-producing
   skill would have to choose between `tasks.create()` and
   `followups.create()` with arbitrary boundary; one table with one
   `wait_for` field is simpler.

## Consequences

**Positive:**
- The morning brief renders Goals, Tasks, and Reminders distinctly,
  each doing one thing well.
- Cross-skill tasks de-duplicate via `dedup_key` ("Pay Chase" created
  by both finance-bills and email-triage collapses to one row).
- Followups auto-resolve when the awaited event arrives (email reply
  from sender) — observable closure semantics.
- Open-source contributors writing domain skills get a clean primitive
  to create user-visible work.

**Negative:**
- Two co-existing reminder-shaped models (legacy `Reminder` with
  status, new `Reminder` as delivery-only) during the migration
  window. Documented; UI/CLI surfaces the new one preferentially.
- `src/iris_harness/tasks/` adds ~250 LOC. Worth it as the lingua franca
  between domain skills.
- The migration window is open-ended (months). Legacy `Reminder` is
  not "finished" until the last caller drains.

**Reversibility:** high during the migration window — both models
coexist. Low after legacy callers drain (we'd have to re-introduce
status to `Reminder`). No reason to reverse expected.
