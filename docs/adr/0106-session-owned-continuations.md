# ADR-0106: Session-owned continuations — every pending question has an owner

Status: Accepted (2026-09-11) — C1-C4 shipped (#414, #415, #416, #417); C5 complete: C5a #419, C5b the `ask_user` pause, C5c the mid-loop resume (2026-09-12).
Decision 3 was reshaped during C3 and the section records why. D3 stays open to revision after the owner's real-world test.
Relates to: ADR-0076 (in-chat approval for consequential actions), ADR-0077 (one governed agentic loop), PR #412 (the incident fix), OSS plan M5
Supersedes nothing. Extends the Phase-3 checkpoint spine rather than replacing it.

## Context

### The incident

In session `854830335275` the planner agent ended a research turn with *"Would you
like to proceed with this plan?"*. The user answered **"yes go head with the plan"**.
The turn was answered in 0.3 ms by the file_organizer's `organize_confirmation`
intercept, which tried to execute an organize plan proposed 69 days earlier, by a
different session, against `~/Downloads`. Only the executor's TOCTOU re-validation
kept the moves from running.

PR #412 fixed the proximate causes (an over-broad `\bplan\b` anchor, a cross-session
auto-target, an unenforced TTL). It did **not** fix the structural cause, which is
this:

> **A user's "yes" has no owner.** The system models a follow-up answer as a property
> of stored state — *"does an approvable thing exist?"* — rather than a property of
> the conversation — *"who just asked me something?"*

### Why the architecture permits it

1. **Intercepts run before the system knows what the turn is.**
   `runtime/turn/pipeline.py:36` orders the stages `intercept → classify → route →
   plan → execute → curate → record`. `intercept` is stage 1; `classify` — which
   decides intent — is stage 2. `pipeline.py:65` breaks the loop on an intercept hit,
   so stages 2–7 never run. Every deterministic short-circuit therefore fires with no
   intent, no route, and no agent selected.

2. **Intercept handlers are blind by construction.**
   `bootstrap.py:5345` calls each handler as
   `handler(message, session_id=session_id, span=span)` (plus `channel` when
   declared). Raw text and a session key — no conversation history, no prior turn's
   intent, no record of which agent spoke last. An intercept *structurally cannot
   know* that the planner had just asked a question.

3. **The chain is 22 handlers, first match wins.**
   `config/intercepts.yaml` declares 22 and `bootstrap.py:5345` dispatches them in
   order until one returns non-`None`. The file_organizer plugin owns nine, at
   positions 2–10.

4. **The correct pattern already exists — one position above the bug.**
   Intercept #1, `confirmation` (`bootstrap.py:4690`), checks session state *first*:

   ```python
   pending = self._pending_confirmations.get(session_id)
   if pending is None:
       return None                    # nothing pending HERE — decline
   decision = _parse_confirmation_decision(message)
   ```

   It declined the incident turn correctly. But `_pending_confirmations` is written
   in exactly one place in the codebase — `bootstrap.py:5242`, the calendar
   attendee-invite path. It is one feature's private mechanism, not a contract any
   other agent or intercept honours. The planner registered nothing.

### What already exists (and is not wired up)

This is the important finding. The mechanism this ADR needs is largely built:

| Piece | Where | State |
|---|---|---|
| Durable checkpoint store | `governance/checkpoint/store.py` — SQLite, 7-day TTL, pinning, `signal` column, 1 MiB payload cap | **Shipped** |
| Chat resume payload | `governance/checkpoint/chat.py::ChatCheckpointPayload` — query, steps, iteration, `halt_reason`, memory_context | **Shipped** |
| Checkpoint write on halt | `core/agentic_core.py:933`, inside `_loop`, on evaluator `deny` / `require_approval` | **Shipped** |
| Loop resume | `core/agentic_core.py:693::resume_from_checkpoint` — rebuilds history, re-enters `_loop` at iteration N+1 | **Shipped** |
| Inspection CLI | `cli/run_inspect.py` — `iris run inspect <run_id>` | **Shipped** |
| **Anything calling `resume_from_checkpoint` in production** | — | ~~**Does not exist.** Only `tests/unit/test_core/test_agentic_core_resume.py` calls it.~~ **Closed by C5c** — the react handlers resume through `resume_seed` + `run_from_seed` / `run_stream(resume=)`. |

Three asymmetries make it unusable as-is:

- **Checkpoints are keyed by `run_id`, not `session_id`.** The schema
  (`store.py:256`) is `PRIMARY KEY (run_id, step_id)` with no session column. You
  cannot ask "what is this session waiting on?", which is precisely the question the
  intercept chain needs answered.
- **The streaming loop has no checkpointing at all** — and, found later while scoping C5,
  no PostStep evaluator either, so it has no halt to checkpoint on. That is its own
  governance decision: [ADR-0107](./0107-poststep-on-the-streaming-loop.md). `AgenticCore` has two loops:
  `_loop` (shared by `run()` and `resume_from_checkpoint`) and `run_stream`, which
  carries neither `_write_checkpoint` nor `_fire_post_step`. Production streams —
  `bootstrap.py:2140` drives `run_stream` for the first task of every turn, and
  `bootstrap.py:2101` drives `core.run()` only for subsequent wave tasks. So the path
  users actually hit is the one with no resume support. (CLAUDE.md's coverage rule:
  the REPL and web UI use `/chat/stream`.)
- **Halts only ever mean governance.** The single checkpoint-write site fires on an
  evaluator verdict. There is no halt reason meaning "I asked the human something and
  I am waiting."

`ask_user` appears in prompt text and docstrings (`core/task_spec.py:177` and `:265`,
`llm/tier_router.py:39`, `runtime/tool_call_parsing.py:89`) as a *policy* the tier
router enables or disables. It has never been implemented as a loop-pausing action.

## Decision

Introduce a **session-owned continuation registry** in the core: one durable,
session-keyed record of *who asked the open question and how to resume them*. Build
it on the existing checkpoint spine rather than a new store.

### 1. The continuation record

A continuation is created when an agent or intercept ends a turn awaiting a specific
answer. It carries:

| Field | Meaning |
|---|---|
| `session_id` | The owning conversation. **The only key that grants a claim.** |
| `owner` | Who asked — agent type (`planner`, `research`) or intercept name (`organize_confirmation`). |
| `kind` | `question` (free-text answer expected) or `approval` (yes/no expected). |
| `question` | The text put to the user, for re-grounding on resume. |
| `run_id` / `step_id` | The checkpoint to resume, when the owner is a loop run. Null for a re-prompt continuation (see tier A below). |
| `intent` | The intent to re-enter with, so resume skips re-classification. |
| `created_at` / `expires_at` | TTL, enforced **on read** (the PR #412 lesson). |
| `status` | `pending` / `answered` / `superseded` / `expired`. |

Stored in `checkpoints.db`, not a new database. ADR-0105's rule applies: fix
coherence, do not add stores.

**Refined while building M5.C1 — a separate table, not columns on `checkpoints`.**
This section first said "continuation columns" on the existing row. Writing it showed
why that does not fit: a checkpoint is loop state keyed by `(run_id, step_id)`, a
continuation is conversational ownership keyed by `session_id`, and a Tier-A
continuation has no run at all. Sharing the row would mean synthetic run ids, and
would make every existing `list` / `get_latest` / `sweep_expired` query filter out
rows it was never written to expect. Same database file, two honest shapes: a
`continuations` table carries the ownership record and points *at* a checkpoint
through nullable `run_id` / `step_id` when a halted run backs it. `checkpoints` still
gains a nullable `session_id`, which is what lets `by_session()` answer "what was
this conversation doing?".

Decision 5 (one pending per session) is implemented as a **partial unique index**
(`... ON continuations(session_id) WHERE status = 'pending'`) rather than a
convention, so a caller that forgets to supersede gets `ContinuationConflictError`
instead of silently creating the ambiguity this ADR exists to remove.

### 2. Session keying is the whole point — no global fallback

`session_id` is mandatory and there is **no cross-session resolution, ever**, not even
when exactly one continuation is pending. That "exactly one open, so it must be the
one meant" inference is what produced the incident, and PR #412 removed it from the
organize path; this ADR must not reintroduce it one layer up. A continuation the user
never saw in *this* conversation is not a continuation they can be answering.

### 3. Claiming — REVISED IN M5.C3: shield and steer, not a position-0 intercept

**What this section originally said, and why it was wrong.** The design called for a
`continuation_resume` intercept at position 0 that *claims the turn* when the message
carries no domain anchor. Building it surfaced a rule this ADR had not accounted for.
`config/intercepts.yaml` records it, from the 2026-07-06 red-team pass:

> An intercept answers BEFORE the ResponseCurator, so the faithfulness / grounding /
> output-safety / escalation judges never see an intercept-answered turn. Every
> intercept must therefore be safe by construction.

Resuming an **agent-owned** continuation means running the agent, which is model prose
by definition. An intercept that answered it would route generative output around
every safety judge. Position 0 therefore cannot answer on an agent's behalf, and
"claims the turn" does not survive contact with the rule. What shipped instead:

**Shield** (`IrisRuntime._dispatch_intercepts`). `InterceptSpec` gains a declarative
`resolves_confirmation`, carried by the five confirmation-resolving intercepts in both
`config/intercepts.yaml` and the `DEFAULT_INTERCEPTS` fallback. Dispatch skips those
when the session's pending continuation belongs to a **different owner**. A bare "yes"
is every pending decision's vocabulary, so whoever matches first wins unless something
knows who actually asked; this is that something. The owning intercept still runs, and
nothing non-confirming is ever shielded — a pending question must not stop the user
asking something unrelated.

`effective_intercept_chain` normally lets a plugin's spec win over the YAML row, but
`resolves_confirmation` is OR'd across the two: a shield must not be droppable by a
plugin that forgot to declare it.

**Steer** (the `classify` stage). After the deterministic refinements, a pending
continuation whose question the user just answered rewrites the intent to its owner's.
A bare "yes" carries no intent of its own, so classified in isolation it lands wherever
the keywords fall. This steers only — the turn still runs `route → plan → execute →
curate`, so every judge sees it, which is exactly what the intercept shape could not
offer.

**What was lost, and why it is acceptable.** The original "specificity beats pendency"
rule is not implemented: the shield is blunt, so a domain-anchored message ("approve
the organize plan") is also shielded while another owner's question is open. Two things
make that tolerable. First, the one-pending-per-session rule (decision 5) means the
*most recent* question owns the session, so the common sequences resolve correctly by
construction. Second, the failure mode is safe rather than wrong: the turn falls
through to the agent loop, or the domain intercept finds no session-scoped plan and
asks which one — never the silent cross-session execution the incident produced.
Revisit only with a real case in hand; the registry cannot know every plugin's anchors,
because plugins own theirs.

### 4. Two tiers of resume, shipped in order

**Tier A — re-prompt continuation (no loop state). SHIPPED in M5.C3/C4.** The
continuation records owner + intent + question. On a matching answer the classify
stage rewrites the intent to the owner's, and the turn runs normally from there. The
conversation history already carries the exchange, so no ReAct state need be
serialized. This closes the entire incident class and required no change to either
loop — as predicted.

Who opens one (M5.C4): an agent whose answer ends by offering to act, detected by
`offers_to_proceed()` in the `record` stage, and any plugin through
`HarnessServices.continuations`. The detector is deliberately narrow — the answer must
*end* on a question *and* that question must offer to act — because an open
continuation shields the confirmation intercepts for the next turn, and shielding on a
rhetorical flourish would be worse than the problem.

**Tier B — mid-loop interrupt/resume (the LangGraph analogue). SHIPPED in M5.C5b/C5c.**
The agent pauses *inside* the loop on an `ask_user` action, writes a checkpoint with
`halt_reason="awaiting_user_input"`, and on resume re-enters at iteration N+1 with the
human's answer injected as that step's observation.

**Tier A shipped first.** Tier B needed `run_stream` to gain checkpointing — or the
two loops to be unified — and that is a governed-loop change with an AST-level
security test pinned to it (`tests/security/test_no_bypass.py:150` asserts every
`self._llm` call in both `_loop` and `run_stream` is preceded by
`_governance_pre_llm`). It got its own slices: [ADR-0107](./0107-poststep-on-the-streaming-loop.md)
gave the streaming loop a halt, C5b hung the pause off it, C5c wired the resume. The
bypass test passed unchanged throughout, which was the point of keeping them separate.

Because the two loop bodies are *still* separate (ADR-0107 option B was deliberately
not started), C5c put the checkpoint decoding in one place — `ResumeSeed` plus
`resume_seed_from_checkpoint` — and gave each loop an entry point that starts from a
seed (`run_from_seed`, `run_stream(resume=)`). Neither loop knows how a checkpoint is
shaped. A resume that worked on only one of them is precisely the drift ADR-0107 was
written about, and duplicating the decode is how it would have happened again.

### 5. One pending continuation per session

A second question **supersedes** the first, which is marked `superseded` rather than
deleted (auditable). Multiple simultaneous pending questions in one conversation are
an ambiguity source — the exact class of defect this ADR exists to remove.

### 6. Durable, because the asymmetry caused the bug

`_pending_confirmations` is in-memory and dies with the process; the organize plan was
durable SQLite and lived 69 days. That asymmetry is *why* a stale plan outlived the
session pointer that was supposed to scope it. Continuations are durable and
TTL-bounded so both halves share one lifetime.

### 7. Tier B claims the next message unconditionally (C5c)

The two tiers claim a turn differently, and the asymmetry is forced rather than
chosen. Tier A's question is a yes/no, so `reads_as_answer` can tell an answer from a
change of subject and a non-answer leaves the question open. Tier B's question is free
text — *"Neo4j or Stardog?"* — and `reads_as_answer` deliberately returns None for
`question` continuations rather than pretend to classify one (its docstring says so,
and C3 left the judgement to the chain).

So C5c cannot find a yes/no, and picks the rule the one-pending-per-session invariant
(decision 5) already implies: **the paused run asked this conversation the most recent
question, so it owns the next message.** A user who ignores the question and changes
the subject is therefore resumed too. That is handled inside the injected observation,
not by guessing in the harness:

    Asked the user: Neo4j or Stardog?
    User answered: actually, what's my calendar today?
    If that reply does not answer the question, abandon the previous task and
    address the reply instead.

The division is the same one decision 3 settled for the steer. The harness stays
deterministic about **who** the turn belongs to — the fact whose absence caused the
incident — and leaves **"is this an answer or a new subject"** to the model, which is
the only thing that can read it. The answer still leaves through the ResponseCurator
and every judge, because the resume re-enters the loop rather than replying from the
pipeline.

Two consequences worth stating plainly:

- A resume that cannot happen (checkpoint expired under the 7-day TTL, swept, or
  undecodable) degrades to answering the reply as its own turn — the pre-Tier-B
  behaviour, so the fallback is shipped code rather than an error path.
- `kind="question"` alone does not claim anything; `Continuation.is_resumable_run`
  does. A free-text question opened with no checkpoint behind it has nothing to resume
  into, and claiming it would swallow the turn and deliver nothing.

### 8. A numbered shortlist is a `choice` continuation (2026-09-14)

Found in live testing. `read_email` answered an ambiguous ask with *"Which one should I
read? 1. ... 2. ... Reply with the number"*, and nothing recorded what it had listed.
The email agent rebuilt a reply of "1" from the *previous user turn* by regex: the number
was dropped whenever that turn did not read as a read request, so the search re-ran and
the same shortlist came back. "just go with the 1 st one" was not recognised at all, and
the model searched the inbox for "1". The same failure as the incident, one level down:
the question had no record, so its answer had nothing to resolve against.

A third kind, `choice`, carries the options in its payload (`{"choices": [...]}`) when
they are shown. It is the one kind whose payload is not an action, so it takes a payload
without an `executor_kind` and refuses one with; every other payload still needs its
executor.

- **Who opens it.** The owner, explicitly. `read_email` opens one against the turn's
  session, which tools now read from `HarnessServices.current_session_id` beside
  `current_query`. That covers both the email agent's loop and the shared tool pool.
- **Claim.** `reads_as_choice` is deterministic: the reply must reduce to exactly one
  in-range ordinal plus words that only frame a pick ("#2", "the second email", "just go
  with the 1 st one"). `classify` steers the turn to the owner, and `plan` hands it one
  task carrying the picked option on `AgentTask.selected_choice`, skipping the planner
  the way a resume does. The owner acts on what was on screen: the email agent reads the
  picked message id. The answer still leaves through `curate`, because nothing here is
  an intercept.
- **Answered by the next reply or not at all.** Unlike a yes/no, a bare number is
  everyone's vocabulary, so a stale shortlist left open would claim a "2" typed later in
  reply to something else. A reply that does not pick withdraws it: `classify` does this
  on an ordinary turn, and the `intercept` stage does it on a turn an intercept answered.
- **Explicit beats inferred.** "Which one should I read?" matches `offers_to_proceed`.
  `record` no longer opens a Tier-A inference over a continuation the same turn opened
  explicitly, otherwise it would supersede the options the next reply picks from.

The regex carry of bare numbers in the email agent was removed, not kept as a fallback.
Two resolution paths for one pick is how they drift.

### 9. A list the answer itself offers is a `choice` too (2026-09-16)

Found in live testing over Telegram. "Any breaking news today" was answered by the
general loop with five numbered headlines ending *"Want me to open any of these and
pull the full article or a short explainer?"*. Only a tool could open a `choice`, so
`record` inferred a Tier-A `approval`, which cannot read "4". With nothing to resolve
against, the model guessed item 1, called a retired tool, and the goal-drift guard
halted the run: every thought was measured against a task that read "4". The rephrase
"Just tell me more about the 4th news from your news list" carried words the pick parser
refused, so the list was withdrawn and the router sent the turn to the email agent.

- **Record decides the kind from the closing question.** `offered_choices` returns the
  items of the last list numbered 1..N (N >= 2) when the answer ends by offering to act
  *and* that last line asks for a pick ("any of these", "which one", "choose").
  `record` then opens a `choice` whose options are `{"position", "text"}` as shown.
  Anything else stays an `approval`: numbered plan steps ending "Would you like me to
  proceed with this plan?" are the incident's shape, and a "yes" to them must still
  route home. Explicit still beats inferred: a tool's shortlist opened in the same turn
  is left alone.
- **The owner gets a task, not a digit.** An option recorded from an answer holds text,
  not an id its owner can act on, so `plan` hands the owner `choice_request`: the item,
  the offer, and the user's reply. That is also what the drift guard measures against.
  A tool's options carry no `text`, so their owners still receive the reply untouched
  and read `selected_choice` (the email agent reads by message id).
- **Picks said as sentences.** The pick parser's filler now covers talking about the
  list ("tell me more about ... from your list") and the nouns a listed item goes by
  ("news", "article", "story", "link"). Because "more" is filler, a number directly
  followed by "more" is read as a quantity: "show me 5 more" is not item 5.
- **The rest of the ways the same pick is said.** Measured after the fix landed: "3"
  resolved, but "give me more details on 3", "expand on 3", "what about 3", "dig into 3",
  "can you open 3" and "the 3rd headline" did not, each on one missing word. A miss is
  not benign — the choice is withdrawn and the reply is planned as a fresh question,
  which is the failure this section exists to end. The filler set therefore also carries
  the prepositions the phrasing hangs on ("on", "into", "to"), the question framing
  ("can", "you", "what", "do", "know", "give") and the verbs that only ask for depth on
  an item already on screen ("dig", "expand", "elaborate", "summarize", "summary",
  "detail"). **Plural nouns stay out on purpose:** "give me 3 headlines" is a quantity,
  and filler "headlines" would read it as headline 3. Only the singular a picked item
  goes by is safe.

### 10. The question mark opens it; the phrasing only picks the kind (2026-09-16)

M5.C3, closed. Until now a continuation opened only when the answer matched
`offers_to_proceed` — a list of offer phrasings. That is an allowlist over natural
language, and each session that phrased a question a new way went unowned. Three
widenings in three sessions; the fourth thread broke anyway. Measured, none of these
opened anything:

```
"Do you want full articles, more sources, or a short briefing on any one story?"
"Tell me which and I'll fetch them."
"Reply with answers 1-5."
```

Every follow-up in that thread was classified from scratch. The model re-asked the same
clarifying question after the user answered it, and "Key quotes" ran an inbox search.

**The gate is now `ends_on_a_question` — the answer ends on `?`.** A language has
unbounded ways to ask and one way to punctuate. `offers_to_proceed` survives, demoted to
picking the *kind*: a numbered list plus a pick request is a `choice`, an offer to act is
an `approval`, anything else is a `question`.

**An inferred `question` claims its turn.** It could not before: `reads_as_answer` refuses
a non-approval kind and there is no run to resume, so the claim fell through and the reply
was classified in isolation. It now claims on the rule Tier B already used — *the owner
asked this conversation the most recent question, so it owns the next message* — and the
model decides whether the message answers it or changes the subject. `plan._question_task`
is the half that was missing: the owner receives `question_request` (the question plus the
reply), because "Key quotes" alone is not a task and the drift guard measuring a run
against two words halts it.

**One turn, on every path.** Like `choice`: answered by the next reply or withdrawn. A
turn an intercept answers never reaches classify, so `drop_stale_inferred` (was
`drop_stale_choice`) now withdraws both inferred kinds — without it an inferred question
survives the intercepted turn and shields the router for a second one. An `approval` is
left open as before (its yes/no vocabulary is narrow enough), and so is a resumable Tier-B
question, which owns a halted run rather than a guess.

**The other half is `ask_user`.** The real defect is that the model wrote its questions as
Final Answers when the loop offers a control-flow action that pauses the run and writes a
checkpoint. Its description discouraged exactly the case that needed it — "never use it to
sign off or offer more help" reads as "do not use it to offer a menu". It now separates a
sign-off (a pleasantry, still a Final Answer) from a question the answer ends on (ask
here), and says why: a question in a Final Answer is a question nobody is waiting on.
Belt and braces — `ask_user` is the clean path, and the inference is the deterministic
floor under it, including for SMALL-tier models where `ask_user` is disabled outright.

## Why not LangGraph

LangGraph does provide this shape out of the box — a `thread_id`-keyed checkpointer,
`interrupt()` / `Command(resume=...)`, durable execution, time-travel. The concepts
map cleanly onto what is proposed here:

| LangGraph | IRIS equivalent |
|---|---|
| `thread_id` | `session_id` |
| checkpointer | `CheckpointStore` (`checkpoints.db`) |
| `interrupt()` | `halt_reason="awaiting_user_input"` + checkpoint write |
| `Command(resume=v)` | the answer injected as the halted step's observation |

We should borrow the vocabulary and not the dependency, for three reasons:

1. **The governance kernel owns the control flow.** Adopting LangGraph moves the loop
   into a third-party graph runtime, and every hook — PreLLM, the PostStep evaluator,
   the egress gate, tier routing — would have to be re-established as graph nodes. The
   bypass test that guarantees this today is *structural*: it parses the AST of
   `_loop` and `run_stream` and asserts that a hook the call cannot skip runs before it,
   and that neither loop has grown a call site nobody reviewed. That guarantee does
   not survive the move, and re-earning it is the real cost, not the wiring.

   (Worth knowing what that guarantee was actually worth when this was written: the test
   asserted `hook_line < llm_line` and collected call sites into a *set*, so a second
   ungoverned `self._llm` inside `_loop` changed nothing it looked at. Both were fixed
   2026-09-12 — see ADR-0108 decision 9 — and verified by mutating the loops and
   confirming each mutation fails.)
2. **It contradicts OSS decision 1.** The harness is being open-sourced as a
   privileged governed core plus plugins. A heavy framework dependency in the core is
   the opposite of that shape, and release gate 2 already forbids plugins from
   reaching into runtime internals.
3. **We are ~70% there.** Store, payload, write path, resume method and inspection CLI
   all exist and are tested. What is missing is session keying, a halt reason, a claim
   rule, and the wiring — all of which are smaller than a loop migration.

Revisit only if multi-agent graph topologies outgrow what `TaskPlanner`'s DAG
(`plan.execution_groups()`) can express. That is not the current constraint.

## Consequences

**Good**

- A pending question has exactly one owner, and answering it reaches that owner.
- The 22-handler chain stops competing for orphan affirmations.
- `iris run inspect` gains a session view: "this conversation is waiting on X".
- Agents can legitimately ask clarifying questions — today an agent that asks one has
  no way to be told the answer, which is why `ask_user` stayed a policy flag.
- Removes the last cross-session inference in the approval path.

**Costs and risks**

- Position 0 in the intercept chain is load-bearing. A bug there affects every turn,
  so the claim rule must stay deterministic, cheap, and heavily tested — no model in
  the loop, matching the rest of `nlu.py`'s discipline.
- Tier B eventually forces the two-loop asymmetry to be resolved. That is arguably a
  benefit (ADR-0077 converged on one governed loop; `run_stream` is the remaining
  divergence), but it is real work.
- A durable continuation that is never answered is a small, TTL-bounded leak. Enforce
  on read, as `list_open()` now does after PR #412.

## Implementation slices

| Slice | Scope | Gate |
|---|---|---|
| **M5.C1** | `checkpoints` gains a nullable `session_id` + `by_session()` (TTL on read) with an in-place migration for pre-ADR databases; new `continuations` table + `ContinuationStore` in the same file. **Built** — `test_checkpoint_store.py`, `test_continuations.py`. |
| **M5.C2** | **Shipped** (#415). `ContinuationRegistry` in `runtime/continuations.py`: `ask()` (supersede-then-open), `pending()`, `answered()`, `drop()`, `history()`, plus `reads_as_answer()` reusing the core's existing confirmation parser rather than a third affirmation vocabulary. | `test_continuation_registry.py` |
| **M5.C3** | **Shipped** (#416), reshaped — see decision 3. Not a position-0 intercept: a dispatch **shield** keyed on `InterceptSpec.resolves_confirmation`, plus an intent **steer** in `classify`. Tier A resume. | `test_continuation_dispatch.py`, incl. the incident turn |
| **M5.C4** | **Shipped** (#417). `offers_to_proceed()` + the `record` stage for agents; `HarnessServices.continuations` for plugins (D1), with both of `organize_request`'s park sites — inline and async — as first consumer. `_pending_confirmations` **deferred**, see below. | `test_continuation_seam.py` |
| **M5.C5a** | **Shipped** (#419). Wires `CheckpointStore` into `AgenticCore` — nothing in production ever had — and stamps `session_id` onto every checkpoint, finally populating C1's column. | `test_agentic_core_checkpoint_wiring.py` |
| **M5.C5b** | **Shipped**, once [ADR-0107](./0107-poststep-on-the-streaming-loop.md) gave `run_stream` a halt to hang a pause on. Tier B: `ask_user` as a real loop pause on the conversational loop, no tier gate (D2). Does not touch the sandbox task spec. | `test_ask_user_pause.py`; `test_no_bypass.py` unchanged |
| **M5.C5c** | **Shipped** (2026-09-12). The mid-loop resume: `ResumeSeed` + `resume_seed_from_checkpoint` decode a checkpoint once and inject the human's answer as the paused step's observation; `run_from_seed` and `run_stream(resume=)` both start from it; `AgentTask.resume_run_id`/`resume_step_id` carry the point down; `classify` claims the turn and `plan` skips decomposition. See decision 7 for the claim rule. | `test_tier_b_resume.py`, `test_tier_b_resume_seam.py`, `test_continuation_dispatch.py`; `test_no_bypass.py` unchanged |

### Deferred out of M5.C4: folding in `_pending_confirmations`

C4 was specified to fold the calendar path's `_pending_confirmations` dict into the
registry. It did not, for a reason worth recording rather than re-deriving:
`_pending_confirmations` stores an **executable payload** — the governed calendar write
to re-run on approval — while `Continuation` deliberately stores *ownership*, not
payloads. Folding it in needs a payload seam, and whether the continuation registry
should carry executable state at all is its own decision, not a rider on a slice that
was already touching the turn pipeline and a plugin.

The two coexist safely: `confirmation` remains first in the chain and resolves its own
pending dict, and it carries `resolves_confirmation` so the shield covers it like any
other. Decision 6's argument still stands and still points at the eventual merge — the
in-memory/durable asymmetry is exactly what let a stale plan outlive the session
pointer meant to scope it.

## Owner decisions (2026-09-11)

### D1 — Agents and plugins both open continuations; `organize_request` is the first consumer

Opening a continuation is a `HarnessServices` seam, available to any agent and to any
plugin-registered intercept — not a core-only privilege. `organize_request`
(`plugins_builtin/file_organizer/handlers.py`) is the worked example and the first
consumer: it already parks a proposal and asks for approval, so it should declare that
it is awaiting one instead of leaving the answer ownerless. Migrating it is the
acceptance test for the seam — if the plugin that caused the incident can express its
pending question through the registry, the contract is right.

**Met in M5.C4.** Both park sites declare the question, and the confirmation handler
closes it only when the pending question is actually the organizer's — if the planner
asked more recently, the plugin must not clear it. The seam degrades to a no-op when
no runtime is wired, so a plugin built against a bare services object still works.

This keeps the registry consistent with the six registration kinds: a capability the
harness owns, reaching plugins through typed `HarnessServices` fields, never through
`extras` and never by importing runtime internals.

### D2 — No tier gate on `ask_user`; model choice stays the user's

**The harness owns sessions and turns, so the continuation is harness state, not model
state.** Swapping the model behind a session — tier escalation, a different provider,
local to cloud — does not invalidate a pending continuation, because nothing about it
lives in the model. Restricting who may pause would therefore be restricting a
*capability* on the basis of something that no longer affects *correctness*.

Model selection carries its own cost and capability trade-off, and that trade is the
user's to make, not the harness's to pre-empt.

Scope correction: the existing "ask_user is disabled for SMALL-tier models"
restriction (`core/task_spec.py:265`, described by `llm/tier_router.py:39-45`) belongs
to the **sandbox / code_exec** task spec — the `run_shell` + `/workspace` Docker
prompt — not to the conversational governed loop. Tier B's `ask_user` is a new
capability on that conversational loop, so this decision does not reopen the sandbox
spec; it declines to copy its gate. M5.C5's blast radius is correspondingly smaller
than first scoped.

The guard against a weak model asking instead of acting is not a tier gate but the
**one-pending-continuation-per-session rule** (decision 5 above): a second question
supersedes the first, so an over-asking model degrades into re-asking rather than
accumulating open questions across a conversation.

### D3 — Continuation lifetime follows the session; 7-day checkpoint TTL as backstop

Taken from D2's principle, stated here for confirmation rather than decided
unilaterally: since the harness owns session and turn lifetime, a continuation lives as
long as its session is resumable, with the existing 7-day checkpoint TTL as the
backstop and TTL enforced **on read** (the PR #412 lesson).

Resume always **re-grounds** — it restates the question before acting on the answer
("Earlier I asked whether to draft the PoC plan — taking that as yes"), so a user
returning after a gap is never silently held to an answer they have lost the context
for. That re-grounding is what makes a long lifetime safe; without it, the honest TTL
would be hours.

Open for the owner to shorten if the real-world test says a day-old "yes" feels wrong.
