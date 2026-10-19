# ADR-0108: The approval queue, connected — and a channel for the web

Status: Accepted (2026-09-12) — shipped, including the timeout notice (decision 7, added the same day after the first draft described the timeout behaviour wrongly) the payload seam + approval gate (decision 8, which closed both of the "still open" items), and the immutability invariant rewrite (decision 9).
Relates to: ADR-0076 (in-chat approval for consequential actions), ADR-0106 (session-owned continuations — Tier B supplies the resume), ADR-0107 (PostStep on the streaming loop — supplies the halt), story 12.gov-4.9 (which specified this and shipped half of it).
Supersedes nothing.

## Context

A real halted web turn — session `web-6d670ccd`, run `b7c928e2-5a09-41ee-b771-d0322391c3b3`
— ended with *"I stopped partway through — the run was paused for approval by the
evaluator."*

There was no approval. Not a lost one, not an expired one: none had ever been created,
by that run or any other.

### Three disconnections, each invisible

**1. The hook was never handed a queue.** `EvaluatorHook.__init__` has accepted
`approval_queue` and `channel_router` since the approval queue shipped
(`evaluator/hook.py:69-70`). Neither construction site in `governance/wiring.py`
(lines 214 and 229) ever passed one. So `self._approval_queue` was always `None`, the
`require_approval` branch at `hook.py:113-125` never ran, no channel was ever notified,
and the `Approval ID:` line in the halt message sat behind an
`approval_request_id is not None` that nothing could satisfy. The proof was on the
owner's own machine: **`~/.local/share/iris/approvals.db` did not exist.**

**2. No channel could reach the web.** `approvals/router.py` knew `CLIChannel`,
`TelegramChannel`, and an stderr fallback. `select()` keyed off
`sys.stdin.isatty()` — false under the API server — so a web-raised approval would have
gone to Telegram if a bot happened to be configured, and to the server's stderr
otherwise. The `channel` column has been on every row since Phase 3 and, with nothing
stamping it, had only ever held its `"cli"` default: `hook.py:120` passed the literal.

**3. The row pointed at nothing.** `checkpoint_id` was always NULL. The evaluator
enqueues at `PostStep`, which is *before* `agentic_core` writes the halt's checkpoint —
`hook.py` passes `None` and its comment says the caller will fill it in — and no caller
ever did, because `ApprovalStore` had no setter for it. An approval was a decision about
a run no surface could find again.

### And answering meant three different things

`iris approvals approve` wrote straight to `ApprovalStore`, so a CLI decision left **no
audit row**. `services/channel_gateway/handlers/approvals.py` did the same, separately.
The web had no way to answer at all. None of the three could continue the run.

## Decision

### 1. Wire the queue at both hook sites, on by default

`build_default_kernel` builds an `ApprovalQueue` + `ChannelRouter` unless
`IRIS_GOVERNANCE_APPROVALS_ENABLED=0`. On by default because the alternative is what
shipped: a halt that claimed to be waiting for something that did not exist.

`force_interactive=False` is the load-bearing argument. The router would otherwise pick
`CLIChannel` whenever stdin is a TTY, and `CLIChannel.notify` blocks on `input()` —
inside the PostStep hook, mid-turn. **There is nothing for that prompt to unblock.** The
evaluator halt is not a gate the loop waits on: it has already broken out and the turn is
about to answer. A Y/n there would hang the REPL to collect an answer arriving too late
to matter. CLI delivery is the Approval ID now printed in the halt message, plus
`iris approvals list`.

### 2. Deliver where the request came from

`AgentTask.origin_channel` carries the gateway from `TurnRequest.channel` (already a
first-class `chat()` parameter) to `AgenticCore`, which puts it on the PostStep context's
metadata; the hook stamps it on the row. `select()` then honours the origin and falls back
to the old tty/telegram/stderr chain only for a row that names nothing useful.

The origin is threaded **explicitly** rather than sniffed from the session-id prefix
(`web-6d670ccd`, `telegram:123456789`). The prefix is a convention that happens to hold;
the channel is a fact the caller already declares.

`WebChannel.notify` records rather than pushes, and that is not a stub — it is what
pull-based delivery is. The UI polls, so a row's existence *is* its delivery. The channel
earns its place by existing: it is what stops `select()` falling through to somebody
else's surface.

*Amendment, 2026-09-21:* pull-based delivery reaches the owner only while the app is open.
The owner raised a trash approval on the phone, switched apps, and looked on Telegram, as
the halt message told them to. Nothing was there. `ChannelRouter.copy_to` now sends a
web-raised approval, and its timeout notice, to the off-box channel as well, when one is
configured. The origin still decides where the approval *belongs*; the copy only makes sure
it is seen. Whichever copy is answered first settles it, and the other is refused as
already answered. Other origins are unchanged.

### 3. Close the checkpoint link where both halves are in hand

`ApprovalStore.set_checkpoint` is called by `AgenticCore` immediately after a halt's
checkpoint is written, with the approval id that came back on the `HookDecision`. That is
the one moment both halves exist. The core gets a narrow `link_approval_checkpoint`
callable, not the queue — it has no business importing the approvals package.

This widened a security invariant, so it was paid for — twice, in the end. At the time,
`test_governance_isolation.py::test_invariant_2` was an allow-list of permitted public
method *names*, and `set_checkpoint` joined it with a new test proving it writes
`checkpoint_id` and nothing else. `checkpoint_id` is a pointer to where the run lives,
not a term of the decision, which is why it was admissible at all.

Widening it a second time for `pending_for_run` (decision 8) made the shape of the
problem obvious, and **the invariant has since been re-expressed as the property its
docstring always claimed** — see decision 9.

### 4. One capability, four surfaces

`governance/approvals/service.py` holds the decision, its audit row, and the resume.
`respond_to_approval` is called by the API endpoint, the `iris approvals` commands, and
the Telegram handler alike. The CLI and Telegram paths gained audit rows and the resume
by being moved onto it; anything added later gets them by calling it.

*Correction, 2026-09-21:* the Telegram handler was moved onto it but never wired in: no
poller called it, so a typed `/approve <id>` reached the chat model as ordinary text. It
is now `services/channels/approval_commands.py`, which both Telegram pollers try before
chat, and the approval message carries buttons that send the same commands. The channel
gateway answers through `POST /governance/approvals/{id}/respond`, the one gated write the
shared service secret may make without `IRIS_WEBUI_ALLOW_WRITES` (owner's decision).

**Ordering inside it is deliberate: the decision is recorded first and separately.** It is
the durable, auditable thing a person did, and it must survive a resume that fails for any
reason. Every failure past that point degrades to "answered, not resumed" and says so —
an unlinked approval, an expired checkpoint, no runtime in this process, a model outage.

**No agent-facing tool answers an approval.** A pending approval is *visible* to the loop,
because it reaches the Action Center and the `pending_actions` tool reads that; knowing a
run is blocked is useful context. Answering is different: the queue exists precisely to put
a human between the agent and an action the evaluator would not allow, and a tool that
approved would hand the decision back to the party it was taken from.

### 5. Approving continues the run — through the pipeline, not the loop

Tier B (ADR-0106 M5.C5c) made mid-loop resume real, so approving can now do the thing story
12.gov-4.9 originally described. `IrisRuntime.resume_halted_run` satisfies the service's
`RunResumer` protocol — a protocol, because `governance` sits below `runtime` and must not
reach up.

It runs the **turn pipeline** with `RESUME_STAGES`, not the react loop directly. Calling the
executor straight would have been shorter and would have worked, and it would have skipped
`curate` — the ResponseCurator and every safety judge — on the one path whose entire premise
is that governance stopped this run once already. That is the 2026-07-06 red-team rule, and
it applies to a resume as much as to an intercept. Going through the pipeline also means
`record` files the answer in the session log, which is how it reaches the web transcript
with no second delivery mechanism to build.

`intercept` is the only stage dropped, for two reasons: it logs the user's message, and
there is no user message here — forging one would put words in the user's mouth; and it is
where the turn could be claimed by something other than the run being resumed. `classify`
returns early on a request that carries a resume point, so a resumed turn cannot close
somebody else's open question on the strength of a reply that was never sent.

The reply injection distinguishes the two resumes, and this is the one place they differ.
A Tier B turn sets `AgentTask.resume_reply` to the user's answer, which becomes the paused
step's observation. An approved *governance* halt leaves it `None`: there the paused step's
observation is a real tool result, and overwriting it would destroy the work the resume
exists to continue. The channel travels with the resume too, because a resumed turn can be
halted again and a second approval has to reach the same surface as the first.

### 6. A pending approval is Action Center state, not UI state

`collect_pending_actions` gains approvals as a third source beside task blockers and
Health. That is `action_center.py`'s own rule — *a core capability is never isolated in
one channel* — and it is what makes a blocked run visible through `GET /actions`, the ReAct
`pending_actions` tool, chat, and the CLI at once, rather than in one screen. Read-time like
Health: the item lives exactly as long as its pending row, and nothing is copied into the
task store. Listed first, because a run *waiting on the user* outranks anything the harness
merely noticed.

Its action is `copy_command`, display-only. Answering has two possible answers and is a
privileged write, which does not fit the single-CTA `/actions/{id}/invoke` lifecycle — and
these ids are not in the task store, so an invoke was never going to find them. The
surfaces that can answer use the approvals endpoint; everyone else gets the command. It is
also **not suppressible**: everything else on that list is something the harness noticed,
and "don't show me these again" on an approval would silently strand runs.

### 7. A lapsed approval says so (added 2026-09-12, same day)

The first draft of this ADR listed the timeout as still open and described it wrongly:
"the Action Center makes it *disappear*, which is worse than saying so." It did not
disappear. **Nothing in production called `expire_stale`** — not the runtime, not a
heartbeat, not a read path — so an approval never timed out at all. The row sat
`pending` past its deadline, `list_pending` kept returning it, and `policy_on_timeout`
had never once been applied to anything. A dead request showed up as live, indefinitely.

- **`approval_timeout_tick`**, every 60s, sweeps and announces. A sweep that keeps a
  store's read model true is core by the OSS boundary rule, like the four filemanager
  sweeps and `email_sweep`.
- **`expire_stale` returns the rows it transitioned**, not a count, because the caller's
  real job is to tell someone. Selecting inside the UPDATE's transaction is what makes
  that safe: the write only touches `status='pending'`, so each lapse is announced
  exactly once however many sweeps race, and the sweep is safe to call from anywhere.
- **Two notices, answering different questions.** The channel notice goes to the surface
  that was asked, through the same router the request used — a separate
  `notify_timeout`, because the request message asks for a decision that can no longer
  be made and, on Telegram, offers `/approve` commands that would now be refused. The
  in-chat notice goes to the conversation that was told "paused for approval", which is
  the only place the silence afterwards is conspicuous. It reuses the Activity spine's
  two legs (`_inject_system_notice` + `_deliver_activity_notification`) rather than
  inventing a third delivery path.
- **The row gained `session_id`**, migrated in place exactly as ADR-0106 C1 added it to
  `checkpoints`, and for the same reason: a notice has to land somewhere, and deriving
  the session from the nullable `checkpoint_id` only works for approvals that got
  linked. A row without one still gets the channel notice.
- **The audit row is now per lapse, keyed to the run that stalled.** It used to be one
  aggregate row per sweep under `run_id="system"`, so `iris run inspect <run_id>` on the
  run that never finished showed nothing about why.
- `GET /governance/approvals` reports `overdue`, so the short window between a deadline
  passing and the next sweep cannot read as live either.

The service composes the notice text and the runtime only delivers it: the wording is
part of the governance behaviour, and conversations belong to the runtime.

`policy_on_timeout` remains `fail_closed`-only in effect — the run stays halted, which
is what already happened. Nothing auto-approves on timeout, and nothing should.

### 8. The payload seam, and a caller for the gate (added 2026-09-12)

Both of this ADR's remaining open items, closed. They turned out to be one shape twice.

**`_pending_confirmations` was an in-memory dict** keyed by session, holding the action
to run on approval, read *before* the continuation shield. Two things followed. A
restart lost the action, so the user's "approve" resolved nothing and routed on as
though the question had never been asked — the asymmetry ADR-0106 decision 6 warned
about, pointing the other way: the answer was durable and the thing it would execute was
not. And it held no ownership, so any of the other 21 confirmation-resolving intercepts
could claim that "approve" first.

The seam is two nullable columns on `continuations`, migrated in place: `executor_kind`
(which registered `ConfirmationExecutor` runs it) and `payload_json`. Governance keeps
ownership and durability; the payload stays opaque to it, because what an action *means*
is the executor's business. `Continuation.is_executable` is the new question dispatch
asks, and the dict is gone.

Three details that are load-bearing rather than incidental:

- **The owner is the intercept that resolves the question, not the domain that raised
  it.** The shield compares `pending.owner` to `spec.name`, so the calendar's
  confirmation is owned by `"confirmation"`; `executor_kind` is what says the action is a
  calendar write. Getting this wrong shields the intercept against its own question —
  which it did, on the first attempt here, and four tests caught it.
- **The payload must be JSON-serialisable, and that is checked on write.** By read time
  the question has been put to the user and the answer is on its way; a failure then is
  far too late. Datetimes go in as ISO strings and the executor parses them back —
  explicit at both ends, rather than a codec guessing which strings were meant to be
  dates.
- **A payload that will not decode costs the action, not the conversation.** Reads never
  raise: a bad row would otherwise break every turn in that session instead of one
  approval.

**`ApprovalGate.await_approval` had no caller for its whole life**, because the flow it
was written for did not exist — an evaluator halt ends the turn rather than blocking, so
nothing was ever *waiting* on an answer. A resume is the one place something is, and
`iris run resume` is now that caller. Which also closes the oldest defect in this whole
sequence: **that command never resumed anything.** It verified the ledger and printed
"re-run the original command with the same run_id", while the halt message that
advertised it told the user to run it to continue. It now continues the run through
`resume_halted_run` — and refuses to, while an approval is still pending, because
resuming past one would be a way around the queue and the queue is the only thing putting
a human in the path. `--wait` blocks on the answer instead of refusing, so approving from
the web or Telegram continues the run in the terminal.

It also refuses while side effects are `ambiguous`: a replay that cannot tell whether it
already happened is how something irreversible gets done twice.

### 9. The invariant is about fields, not method names (added 2026-09-12)

`test_invariant_2` claimed, in its docstring, that "none of them writes `requested_at`,
`timeout_at`, or `policy_on_timeout` after row creation." **Nothing checked that.** What
it actually asserted was that the store's public methods appeared in a hardcoded set of
names, so the real question — may this method change what it is changing? — was answered
by whoever edited the set. That happened twice in one week (`set_checkpoint`, then
`pending_for_run`), and both times the only evidence a reviewer had was the author's
say-so in a docstring.

It now asserts the property, against the real API:

- **`_IMMUTABLE_FIELDS`** — the terms of the request: what was asked, of whom, when, and
  how long the answer had. No public method may change any of them. An agent that could
  rewrite one could reopen a closed window or re-point an approval at a different run.
- **`_DECISION_FIELDS`** — the answer and its author, movable only by `respond` (a human
  answered) and `expire_stale` (nobody did). Anything else moving them is a vote cast by
  code on a human's behalf.
- **`checkpoint_id`** is in neither set, deliberately, and has its own positive test.

Both are parametrised over every public method, exercised against a real overdue row —
overdue on purpose, or `expire_stale` would be a no-op and pass without running its
write. A new public method must add an entry to `_EXERCISE` saying *how to call it*; the
assertions then decide whether it is acceptable, rather than the author. The failure
message says so, because the tempting move at that moment is to make the test quiet.

The tests were verified by violation, not just by passing: a new unexercised method, a
`set_checkpoint` that also moves `timeout_at`, and a `set_checkpoint` that also approves
the row each fail, each in the test that should catch them. A security test that has
never been seen to fail is an assumption with a green tick next to it.

**The same exercise on `test_no_bypass.py` found the same class of gap twice over**, and
it is the more serious of the two because that file is the gate ADR-0106 and ADR-0107
both cite as their structural guarantee:

- *It could not count.* Call sites were collected into a set keyed by
  `file:scope:expression`, so a second `self._llm(...)` inside an already-listed method
  produced a label that was already present. An ungoverned LLM call could be added to the
  middle of `_loop` and every assertion passed. Sites are counted now, with a reviewed
  `EXPECTED_CALL_COUNTS` for the four methods that legitimately hold two.
- *Line order is not control flow.* `any(hook_line < llm_line)` is satisfied by a hook
  inside a branch the call never enters. `_hook_dominates` now requires the hook to be a
  statement of a block that *encloses* the call — it may be in an outer block (the real
  code has the hook in a loop body and the call inside a `try` within it), but not a
  deeper one, because anything deeper is conditional on something the call is not.

Six mutations, all caught, up from three of six before. The file's docstring now also
states what it still cannot see — a hook reached through a helper or behind a runtime
flag — instead of implying it covers that too.

## Consequences

- A `require_approval` verdict now creates something a human can answer, on the surface
  that caused it, and answering continues the run.
- `approvals.db` starts existing, and rows now actually reach a terminal state:
  `approval_timeout_tick` expires the unanswered ones instead of leaving them pending
  forever.
- A resumed turn costs a full pipeline run, including a `classify` call. Accepted: a resume
  is rare and correctness beats a saved round-trip.
- `POST /governance/approvals/{id}/respond` is synchronous, like `POST /chat`, and can take
  as long as a chat turn. The answer also lands in the session log, so a caller that does
  not wait still sees it in the conversation.

## Still open

Nothing from this ADR. The three disconnections, the timeout, the payload seam and the
gate are all closed; `iris run resume`, `iris approvals`, the API, Telegram and the web
now go through the same capability and mean the same thing by it.
