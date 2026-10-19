# ADR-0107: The PostStep evaluator does not run on the streaming loop

Status: **Accepted (2026-09-11)** — decided by the owner, shipped the same day. A scoped
form of option A: enforce on streaming only the signals with no local equivalent, reuse the
existing guards for loop containment, and define the interruption UX.
Relates to: ADR-0077 (converge on one governed agentic loop), ADR-0106 (session-owned continuations), the unified governance layer doc

## The finding

`AgenticCore` has two ReAct loops. Only one of them fires the evaluator.

- `_fire_post_step` has **exactly one call site**: `core/agentic_core.py:920`, inside
  `_loop` (shared by `run()` and `resume_from_checkpoint`).
- `run_stream` (`agentic_core.py:1276-1462`) has `_governance_pre_llm` and the
  architecture-disclosure screen, and **no PostStep at all**.
- `bootstrap.py:2140` drives `run_stream` for the **first task of every turn**;
  `bootstrap.py:2101` drives `core.run()` only for subsequent wave tasks.

The REPL and the web UI both post to `/chat/stream`. So on the path users actually
take, the first — usually only — task of every turn runs without the PostStep
evaluator, while a second-wave task of a multi-step plan runs with it. The governance
posture differs by *position in the plan*, which nobody chose.

The kernel itself is on by default (`kernel_from_env`, secure-by-default per exp-007
GAP-13), and `_core_for` passes it to every core it builds. So this is not "governance
is off in dev" — the evaluator is live, wired, and simply never consulted on the
streaming path.

**This is not a documented trade-off.** `docs/architecture/unified-governance-layer.md`
line 183 lists `PostStep` → `Evaluator, CheckpointWriter` as shipped, with no caveat
that it applies to one loop only. Anyone reading the docs would conclude streamed
turns are evaluated. They are not.

### How exposed are we, honestly

Less than "no governance on the production path" would suggest, and the difference
matters for the decision.

**Still enforced on the streaming loop:** the whole PreLLM chain — data classifier,
egress gate, redaction, credential broker, persona surface, tool policy, and the
command/fs/network jails — plus the architecture-disclosure screen on the final
answer. Nothing reaches a model or leaves the box ungoverned.

**Partly duplicated by local guards.** `run_stream` carries its own `max_iterations`,
`timeout_seconds`, `stall_limit` and malformed-step counters. The three signals in the
default registry — `StepCapSignal`, `ActionRepeatSignal`, `ToolFailureStreakSignal` —
are loop-hygiene checks that overlap these substantially. A runaway loop is still cut
off on the streaming path; it is cut off by local logic rather than by a governed,
audited verdict.

**Genuinely missing on the streaming loop:**

- `ClassificationViolationSignal`, `CostBudgetSignal`, `GoalDriftSignal` and the
  semantic `LoopDetectSignal` — the ones with no local equivalent (each opt-in, so
  whether they are live depends on the profile).
- The **audit trail**. A PostStep verdict is a recorded governance decision; local
  counters are not. A streamed run that misbehaved leaves no evaluator row.
- The **flagged-run thought writer**, which only fires through `LoopDetectSignal`.
- Any **halt at all**, which is what ADR-0106's Tier B needs — you cannot pause a loop
  that has no pause point.

## Why this matters beyond governance

ADR-0106 M5.C5b wants `ask_user` to pause the loop, checkpoint, and resume when the
human answers. The pause has to hang off *something*. On `_loop` that something is the
PostStep halt path, which already writes a checkpoint and breaks the loop cleanly
(`agentic_core.py:920-941`). `run_stream` has no equivalent, so Tier B on the
streaming loop needs either this decision taken, or a second, parallel pause mechanism
built beside it — which is how two loops drifted apart in the first place.

## Options

### A. Fire PostStep in `run_stream` too

Bring the streaming loop to parity: evaluate after each Thought→Action→Observation,
honour `deny` / `require_approval`, write the checkpoint, end the stream with the
evaluator's user-safe message.

- **For:** closes the asymmetry; makes the governance doc true; gives Tier B its pause
  point; one posture regardless of position in the plan.
- **Against:** **changes behaviour on every streamed turn.** Turns that complete today
  can halt mid-stream. Partial output has already been yielded to the user when the
  halt fires, so the UX is "answer stops half-way and is replaced by a governance
  message" — worse than the sync path, where nothing had been shown yet. Also adds an
  evaluator call per iteration on the latency-sensitive path.

### B. Unify the two loops (ADR-0077's unfinished business)

`run_stream` and `_loop` are the last real divergence left from ADR-0077's "one
governed loop". Make `_loop` yield, and let `run()` drain it the way `chat()` already
drains `chat_stream()`.

- **For:** the asymmetry cannot recur; one body to govern, one to test; the
  `test_no_bypass.py` AST assertions collapse to a single target.
- **Against:** much the largest change here, and it inherits A's behaviour change
  wholesale. Both loops are load-bearing on every turn.

### C. Evaluate but do not halt (shadow mode) on streaming

Fire PostStep in `run_stream` for its audit and signal value, log and record the
verdict, but never interrupt a stream.

- **For:** closes the *observability* half with no UX change; the missing signals and
  the audit trail come back; reversible; measures how often a halt would have fired
  before deciding whether it should.
- **Against:** leaves a real posture difference — a streamed turn the evaluator wanted
  to stop still finishes. Documents as "evaluated, not enforced", which needs saying
  plainly rather than being quietly true. Gives Tier B nothing.

### D. Leave it, document it

Record in the governance doc that PostStep covers the sync loop only, and rely on the
PreLLM chain plus the local guards for streamed turns.

- **For:** no behaviour change, no latency cost; honest.
- **Against:** the posture still varies by position in the plan; Tier B stays blocked;
  the opt-in signals stay unreachable where they matter most.

## Recommendation

**C first, then A** — measure, then enforce.

The reason is that nobody currently knows how often a streamed turn *would* halt. That
number is the whole decision: if it is near zero, A is a safe cleanup; if it is not,
then A is a significant UX change to the main path and deserves the mid-stream halt
UX to be designed rather than inherited. Shadow mode answers the question in days of
real use and closes the audit and signal gap immediately, which is the half with no
downside.

B is the right end state and should stay on the roadmap as ADR-0077's unfinished
business, but it is not the way to *start* — it couples a large refactor to a live
behaviour change.

For ADR-0106 Tier B: C does not unblock it. If `ask_user` matters sooner than this
decision, the alternative is to give the pause its own explicit mechanism in both loops
rather than riding on the evaluator halt — worth costing separately, since "a second
parallel mechanism" is exactly the shape that produced this ADR.

## Decision

**Option A, scoped — enforce what is missing, reuse what is there.** The owner took
this directly rather than measuring first, and the scoping is what makes that safe:
the signals that can halt a streamed turn are only the four with no local equivalent,
so the blast radius is the set of cases nothing was catching, not the set of cases the
local guards were already catching quietly.

1. **`run_stream` fires `_fire_post_step`** after each observation — the same point
   `_loop` fires it.
2. **Only `STREAMING_ENFORCED_SIGNALS` can halt it**: `classification_violation`,
   `cost_budget`, `goal_drift`, and the semantic `loop_detect`. A terminal verdict
   from `step_cap`, `action_repeat` or `tool_failure_streak` is logged and ignored —
   `run_stream`'s own `max_iterations`, `stall_limit` and malformed-step counters
   already contain runaway loops, and two mechanisms halting one run at two
   thresholds helps nobody. **Reuse the duplicates; add only what is missing.**
3. **Enforcement reads structured metadata**, not prose. `_enforced_signal_names`
   walks `audit_metadata["signals"]` — matching on the human-readable reason string
   would break the first time someone reworded it. Malformed metadata fails *open*,
   back to the local guards, rather than halting a run on a parse accident.
4. **The interruption UX is defined**: when the harness stops a task it *summarises
   what ran, says why it stopped, and stops*. No automatic retry, no bare verdict.
   The user decides what happens next. Both loops share
   `_evaluator_block_message`, so a halt reads the same whichever answered.
5. **`tests/security/test_no_bypass.py` now asserts PostStep in both loops**
   structurally, so a third loop — or a refactor that drops the call — cannot land
   silently. This is the test that would have caught the original gap.

### What this does not settle

- **Option B (unifying the two loops)** remains ADR-0077's unfinished business and the
  right end state. This decision narrows the gap between the loops; it does not close
  the fact that there are two.
- **The shadow-mode measurement (C)** was skipped. If halts on the streaming path turn
  out to be more frequent or less welcome than expected, the scoped signal set is the
  dial to turn: narrowing `STREAMING_ENFORCED_SIGNALS` is a one-line change with a
  test pinning its contents.
- **ADR-0106 Tier B is unblocked** by this: `run_stream` now has a halt path that
  writes a checkpoint, which is what `ask_user` needs to pause against.

## As built

`STREAMING_ENFORCED_SIGNALS`, `_enforced_signal_names` and `_completed_work` in
`core/agentic_core.py`; the halt branch in `run_stream` beside the existing local
guards; `_evaluator_block_message` rewritten to summarise and defer. Tests in
`tests/unit/test_core/test_streaming_poststep.py`, including that both loops
interrupt with the same explanation.

The governance doc's PostStep row still needs its caveat removed now that the claim
is true of both loops — carried as a follow-up rather than done blind here.
