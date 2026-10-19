# ADR-0076: In-chat approval for consequential actions

Status: Accepted (2026-06-24)
Relates to: ADR-0075 (calendar write authority — R3 attendee invites), ADR-0073 (Pending Actions / Action Center)

## Context

ADR-0075's governance classifies an attendee calendar invite as **R3** — it must
not be created/sent without explicit approval. The first cut told the user to go
run `iris calendar add … --yes` in the CLI. That breaks the conversation and isn't
channel-aware. We want the approval to happen **in the same chat**, with an
approve/reject control, and to behave consistently across web, CLI, and Telegram
(the "WebUI is just a renderer" rule: the affordance lives in the harness).

A forgotten approval must not vanish — it should persist and surface in the
Action Center like any other pending action. And power users want a way to skip
the gate entirely.

## Decision

A generic **in-chat confirmation** affordance owned by the harness:

- When a turn produces an R3 result, the harness **stashes** the pending action
  (the exact governed write to re-run) keyed by session, and returns a normal
  `ChatResult` whose text asks for approval **and** carries structured
  `metadata.pending_confirmation = {id, kind, summary, detail, options:[approve,reject]}`.
- The **next turn** is intercepted first (`_handle_confirmation_turn`): a plain
  `approve`/`reject` (synonyms parsed by `_parse_confirmation_decision`) resolves
  it — approve re-runs the stashed write with `approved=True` through the **same
  governed path**; reject discards.
- **Channel-agnostic by construction:** the resolution is plain text, so it works
  in CLI/Telegram by typing "approve". Web/Telegram may render buttons, but a
  button is just a control that **sends that text** — no channel-specific logic in
  the harness.

**Persistence in the Action Center (ADR-0073):** stashing also upserts a Task
(`source_kind="calendar-approval"`, dedup-keyed by the confirmation id) carrying a
`copy_command` action with the exact `iris calendar add … --yes` command. So an
un-approved invite is visible in the dashboard and survives a restart (the
in-memory stash is only for the immediate next-turn convenience). Resolving in
chat completes (approve) or drops (reject) that Task.

**Override:** `IRIS_CALENDAR_AUTO_APPROVE_INVITES=1` bypasses the gate — attendee
events auto-create with `approved=True`, no confirmation. Off by default (invites
are consequential).

## Consequences

- One approval UX across all channels; the calendar agent is the first user, but
  the mechanism (`_pending_confirmations` + `_handle_confirmation_turn`) is generic
  and can back any future consequential action by stashing a different `kind`.
- Forgotten approvals are durable and actionable from the Action Center.
- Actually *emailing* attendees/pushing invites still depends on the provider
  (Google/Apple write-back) — this ADR governs the approval, not the delivery.

## Follow-ups

- Web UI: render approve/reject buttons from `metadata.pending_confirmation` (they
  POST the text "approve"/"reject"). Telegram: inline buttons (typed reply already
  works). Both are pure renderers over the harness contract above.
