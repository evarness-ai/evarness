# ADR-0008: R2/R3 split for calendar writes — private events auto, attendee events prompt

Status: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §3.3, §4.1.

## Context

The upgrade's three-tier approval policy (R1/R2/R3) classifies
`calendar/write` as **R3** (external side effect, requires explicit
approval) on the principle that writing to Google Calendar mutates
state visible to third parties.

The upgrade also introduces the email/task → calendar bridge: dated
tasks and bills mirror to calendar as visual time-blocks
(`calendar_visibility: always` for bills, `if_due_at_set` for
followups, etc.). For a busy user this means many R3 prompts per day
for events that are just personal visualizations — defeats the
feature.

But not every calendar write has the same external impact. A
private event with no attendees is materially different from a meeting
invite that emails other people.

## Decision

`calendar/write` route is **conditionally R2 or R3** based on event
shape:

| Event kind | Approval mode |
|---|---|
| **Private, no attendees** (`visibility=private`, `attendees==[]`) | **R2** — auto-with-undo-window (10 minutes) |
| **Has attendees** | **R3** — prompt via interactive channel |

Implementation: `policy.yaml` route entry with `escalate_when` predicate:

```yaml
- route: calendar/write
  approval:
    mode: auto-with-undo-window
    window_seconds: 600
    escalate_to: prompt
    escalate_when: { attendees_present: true }
```

The `gcal-compose.create_event` tool checks at the boundary; the
governance kernel evaluates `escalate_when` against the request
metadata and routes to the appropriate mode.

This is a small but precise extension of ADR-Q12 (three-tier approval).
The original rule "calendar/write is R3" was right at the route level;
this adds a route sub-discriminator without weakening the rest of the
R3 stance.

## Alternatives considered

1. **All calendar writes R3 (no split).** Rejected: prompts on every
   bill mirror; users will disable the calendar-mirror feature within
   a week. Defeats the value.
2. **All calendar writes R2 (no prompt ever).** Rejected: meeting
   invites to other people are a genuinely consequential action;
   silent auto-creation is unsafe.
3. **Per-skill approval policy.** Rejected: the same skill
   (`gcal-compose`) handles both private mirrors and attendee invites;
   policy must distinguish per-call, not per-skill.
4. **Confirm-batched mode for mirrors** (collect proposed events for a
   day, prompt once). Cleaner UX but adds a delay between the
   triggering task and the calendar event appearing. Reconsider if
   the auto-with-undo notification volume proves annoying.

## Consequences

**Positive:**
- Bill mirrors land on calendar silently within a 10-min undo window;
  user sees one notification per event, can revert if wrong.
- Meeting invites still require explicit approval; the consent
  boundary that matters most is preserved.
- The R2/R3 split has a precise objective definition (no judgment
  call: attendees present → R3); easy to reason about and audit.

**Negative:**
- The `escalate_when` predicate is a small new mechanism in the policy
  schema. ~30 LOC. Worth the precision.
- Notification volume for auto-with-undo can become noisy if many
  bills land at once. Mitigation: bills firing in the same routine
  pass batch into one "created N calendar events: <list>" notification.

**Reversibility:** high. Flipping the calendar/write back to plain R3
is a one-line policy edit.

## Related ADRs

- ADR-Q12 (three-tier approval classification, recorded in the
  canonical doc) — this ADR refines its rule for one route.
- ADR-0009 — the trust model that makes "auto-with-undo" viable: even
  a buggy skill cannot do worse than a 10-minute window of unwanted
  events, immediately undoable.
