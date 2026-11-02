# ADR-0141: A durable workflow store that fails closed and never repeats a write

Status: Proposed (2026-11-02), not implemented. Work package 5 of the Evarness Next
specification (`docs/architecture/evarness-next-spec.md`, section 7 and X4).
Relates to: `memory/state/store.py` (the checkpoint store, keyed by `(run_id, step_id)`),
`runtime/continuations.py` (one open question per session), `kernel/governance/side_effects/`
(the ledger, `ProbeResult`, ADR-0132), `kernel/governance/approvals/schema.sql`
(`checkpoint_id`, `timeout_at`, `policy_on_timeout`), the tables `workflow_runs` and
`workflow_steps` in `memory/state/` (new).

## Context

Long-running work exists in pieces: the checkpoint store holds what a run looked like at
a step, the continuation registry holds one open question, the side-effect ledger holds
a write-ahead row for a destructive action (ADR-0132), and the heartbeat scheduler decides
when work starts. Nothing holds what state a run is in. A process restart mid-workflow,
two workers claiming the same work, a payment submit the worker died after: each is
answered today by whichever piece happens to know, and the answer to the last one must
never be "run it again".

## Decision

A durable workflow store (decision D16), two tables beside the checkpoint store, written
by the tool runner: `workflow_runs` (spec 7.1) and `workflow_steps` (spec 7.2). The
heartbeat scheduler decides when work starts; the store decides what state it is in; a
worker supervisor claims, runs, retries and cancels.

Run states (spec 7.3): `pending`, `claimed`, `running`, `waiting_approval`,
`waiting_input`, `suspended`, `uncertain`, and the terminal `completed`, `failed`,
`cancelled`. Claiming is one SQL statement: `pending` to `claimed` where the worker
claim is null or expired and the bot's active runs are below its `max_parallel_runs`.
Two workers cannot both win. A claim lasts five minutes by default and is renewed each
step; the supervisor marks an expired claim `suspended`, then `pending`. ("Lease" is
kept for credentials.)

A step is written `pending` before the tool runs and carries the step's declared
`effect`, the ledger row for every `write` and `destructive`, and whichever of
`approval_id`, `grant_id`, `lease_id` authorised it. An undeclared effect is refused
(decision D15); an MCP tool without a declared effect is denied (spec X4).

Resume rules the kernel and the runner enforce:

- W1. A step with status `ran` is never run again.
- W2. A step with status `ambiguous` (the write-ahead row exists, no completion) is
  checked with the tool's `verify:` probe. `completed`: mark `ran`. `not_completed`: mark
  `failed`, the run may retry. `ambiguous`, which is also what no probe, an unknown probe
  and a probe that raises return: the run becomes `uncertain` and waits for the owner.
- W3. A run that resumes re-checks every grant and lease it used. An expired or revoked
  one stops the run at `waiting_approval`.
- W4. Writing the workflow row and the ledger row is part of the step. If the store or
  the ledger is unavailable, a `write` or `destructive` step fails closed before the tool
  runs.

## Alternatives

- Extend the checkpoint store with a status column. A checkpoint is a snapshot at a
  step; a run's state outlives any one step and is what the supervisor claims.
- A workflow engine as a dependency. Its retry semantics would sit above the ledger
  and could repeat a write the ledger says may have landed.
- Best-effort state, as the audit log is for reads. ADR-0132 already decided that an
  irreversible side effect gets a durable record before it runs; W4 extends that to the
  run that contains it.

## Consequences

- The ledger's own statuses (`pending`, `completed`, `not_completed`, `ambiguous`,
  `error`) and the step's (`pending`, `ran`, `failed`, `ambiguous`, `skipped`) are two
  vocabularies; W2 maps them, and the mapping is tested.
- `step_id` stays the INTEGER sequence the ledger, the checkpoint store and the approval
  queue carry; the workflow step's own key is a text id with `seq` alongside.
- A `waiting_approval` run leaves on the card's answer or on `timeout_at` with the card's
  `policy_on_timeout`, default `fail_closed`.
- Whether `max_parallel_runs` above four is ever allowed on one laptop is open (spec
  section 12); measure with five bots first.
- Closing a run's `task` grants (ADR-0139) and revoking its leases (ADR-0140) is part of
  finishing the run; the specification names no component for it. A revoked bot cancels
  its runs (ADR-0138).
