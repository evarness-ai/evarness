# Approval-Gated Send — a run that pauses for a human

## Concept

Most governance nodes make a decision and move on: the guard blocks, the judge
flags, the policy gate denies. But some actions shouldn't be decided by the
harness at all — sending an email on your behalf, moving money, deleting files.
Those need a **person**, at run time.

The `approval_gate` node does something no other node does: it **pauses the run**
instead of finishing it. The run stops with status `paused`, records which gate
is waiting and what it's asking, and returns. Nothing downstream executes — the
send never happens. Later, a human **resumes** the run with a decision, and the
engine **replays** from the start: because a sim run is deterministic (same
graph + fixture + seed produce the same event stream), replay reproduces every
event up to the gate byte-for-byte, then continues past it with the decision in
hand. There is no frozen mid-run state to serialize — determinism *is* the
resume mechanism.

## Two layers of safety on one action

This harness gates a `email.send` — a **write** tool. Watch how two different
safety mechanisms stack:

1. **Static, author-time:** `email.send`'s manifest declares `side_effects:
   write`, so it *requires approval by default*. The tool node had to set
   `approve_side_effects: true` for the author to allow this tool class into the
   harness at all. That's a design-time decision, baked into the graph.
2. **Dynamic, run-time:** the `approval_gate` in front of it pauses for a
   *person* to approve *this specific send*, every time the harness runs.

The first says "this kind of action is allowed in this harness." The second
says "do this particular one, right now?" A production personal agent needs
both.

## Run it, then resume

- **Run** the harness → it **pauses**. Status `paused`, an `approval_requested`
  event, and a pending prompt ("Send this email on your behalf?"). No
  `tool_called` — the send did not happen.
- **Resume — approve** → the run replays, the gate emits `approval_granted`, the
  send tool fires (`tool_called email.send`), and the run completes.
- **Resume — reject** → the gate emits `approval_rejected` and the run blocks.
  The send tool is never called. A rejected action leaves no trace of having
  happened, because it didn't.

From the CLI:

```
evarness run graph.json --fixture fixtures/send.yaml
#   → PAUSED at n3: Send this email on your behalf?
evarness run graph.json --fixture fixtures/send.yaml --approve n3=approve
#   → completed, email sent
evarness run graph.json --fixture fixtures/send.yaml --approve n3=reject
#   → blocked, nothing sent
```

Over HTTP: `POST /api/harnesses/{id}/runs` pauses and returns `pending`; then
`POST /api/harnesses/{id}/runs/{run_id}/resume {"decision": "approve"}` resumes
the same run in place.

## Knobs to try

- `approval_gate.require_when` — set it to `classified` or `personal_or_secret`
  and put a `data_classifier` upstream: the gate then pauses **only** when the
  content is sensitive, and waves public content straight through
  (`approval_skipped` in the trace). Human attention is a budget; spend it where
  it matters.
- Move the gate **after** the tool instead of before: now the send happens and
  the human approves the *result* before it reaches output — a review gate
  rather than an authorization gate. Different placement, different meaning.
- Set the tool's `approve_side_effects` back to `false`: the run now blocks at
  the tool itself (the static gate), before the human gate ever matters —
  showing the two layers are independent.

## When to use

- Any side-effecting action (send, post, pay, delete) that a person should
  authorize
- A review checkpoint where a human signs off on generated content before it
  ships
- Compliance flows that require a recorded human decision (the approval events
  land in the audit sink)
