# ADR-0118: A destructive tool call is approved by code, per call, through the approval queue

Status: Proposed (2026-09-21). The owner answered decisions 1-4 below in an interview on the same day. Build steps 1 to 5 are implemented. Amended 2026-09-22: a `write` may declare `approval: pinned` to take this approval path (decision 1, below); and a reversible trash of a category the owner named, whose emails code picks, runs without a card (decision 3, below).

**Status pass (2026-10-18):** Accepted; the decision is implemented (`kernel/governance/plugins/destructive_approval.py`, `plugin_host/manifest.py`). the owner's decision is implemented; status moves from Proposed to Accepted

Relates to: ADR-0110 (tool effects in the manifest), ADR-0108 (the approval queue, its channels and resume), ADR-0106/0107 (pause and resume), ADR-0068 (the escalation judge and its re-run), ADR-0067 (FileManager never deletes a file it did not create), ADR-0117 (device scopes).
Amends ADR-0110 §1 by adding a third `effect` value.

## Context

On 2026-09-21 the owner asked the phone "Can you delete my emails or not?". The email agent
correctly replied that it would not delete them, because no email tool deletes anything. The
owner's follow-up question was the real one: *if the tool existed and policy said to ask, would
governance catch it?* The answer, read from the code, is **partly**:

| What happens today | Where | Why it is not enough for a delete |
|---|---|---|
| A tool declares `effect: read \| write` and, for a write, `confirm: once \| never`. | ADR-0110, `runtime/plugin_host/manifest.py` | A delete and a new reminder are the same kind of thing. A plugin may declare `confirm: never` for a delete. |
| `confirm: once` lets the write run once the run has an `ask_user` step **after** it proposed that write. | `kernel/governance/plugins/tool_policy.py:51`, `agent/agentic_core.py:300` `_run_asked_user` | The code checks that a question was **asked**, not what the owner **answered**. The reply reaches the model as free text (`"User answered: {reply}"`, `agentic_core.py:736`). A "no" is honoured only if the model reads it that way. |
| One confirmation covers the rest of the run. | same | After one "yes" the model can call the tool with arguments that were not in the question it asked. |
| A PreToolUse `require_approval` becomes an **observation** the model reads. | `agentic_core.py:1491` | It creates no approval row and does not pause the run. Only the PostStep evaluator writes rows to the queue (ADR-0108 §1), and by then the step has already run. |
| In `enforce` mode, escalate and reroute **re-run the whole plan**. | `runtime/escalation_actions.py` `maybe_escalate` | Nothing there checks whether the first attempt already executed a write. A delete could run twice, or a second run could invent a different delete. |

What already works and is reused rather than rebuilt:

- **The approval queue** (ADR-0108): durable rows whose request terms cannot change after they
  are written. `tests/security/test_governance_isolation.py` pins that (`_IMMUTABLE_FIELDS`, §9). One answering capability, `respond_to_approval`, serves
  the API, the CLI, Telegram and the web. Approving resumes the run **through the turn
  pipeline**, so the curator and every judge still run. Since #579 an `approval.requested`
  event also pushes a banner to the phone.
- **Device scopes** (ADR-0117): `POST /governance/approvals/{id}/respond` is a gated write, so a
  device paired `read` already gets a 403 (`server/iris_api/main.py`, `_control_writes_guard`).
- **FileManager's precedent** (ADR-0067 and `filemanager/quarantine.py`): a file "delete" moves
  the file to quarantine, which keeps it for a retention window and can restore it. IRIS never
  permanently deletes a file it did not create.

## Decision

### 1. A third effect: `destructive` (owner decision 1: data loss only)

```yaml
tools:
  trash_email:
    effect: destructive
    undo: restore_email      # required when the service offers a reversible form (decision 3)
```

`destructive` means **the call removes or overwrites the owner's data**: deleting or trashing an
email, deleting a calendar event, overwriting or quarantining a file, dropping a finance record.

Irreversible **outbound** actions (sending an email, a payment, a public post) are **not**
covered by this ADR. They stay `write` until a separate decision, because the owner wanted this
change scoped to data loss.

> **Amendment (2026-09-22, owner decision 8 of the promo-classification interview): the
> `approval: pinned` write form.** Sending an email stays `effect: write` (it is not data
> loss), but it cannot be taken back, so the owner wants every send approved on the same
> pinned card. A `write` may now declare `approval: pinned` (writes only; it excludes
> `confirm`, and such a tool cannot be an `undo`). Its gate becomes `approval`, and one
> predicate (`approved_per_call`: destructive, or gate `approval`) routes it through the
> **same** mechanism as decision 2 — `validate` before queuing, `describe` for the card,
> `DestructiveApprovalHook`, one pinned row, approve executes exactly the pinned call,
> reject resumes with "nothing was changed, sent or deleted", fail-closed without a queue
> or a resumable run. Nothing was duplicated. The card carries `effect: write` (rows from
> before default to `destructive`), so it reads "acts for you" rather than "deletes data",
> and the prompt marks the tool as a write with a card, not a delete. The first-ask
> turn-back (`_turn_back_ask`) still applies to destructive tools only. First user:
> `send_email` (email_workflows): plain text, no attachments or drafts; `reply_to_id`
> threads a reply (In-Reply-To, References, Gmail threadId) and fills the recipient and a
> "Re:" subject. Gmail's `gmail.modify` grant (step 5) already covers `messages.send`, so
> no scope changed.
>
> **Two guards added after the first real send (owner, 2026-09-22).** The owner asked for
> a test email to their own address and the model wrote a blend of their two account
> names, an address that exists nowhere; the card also came from the account that had
> received mail most recently. Now `validate` refuses any To/Cc address that is neither in
> the owner's request this turn nor already in their mail (sender, recipient or cc), so an
> invented address never reaches a card. A new message goes from `from` when the owner
> names an account, else from `IRIS_EMAIL_DEFAULT_SENDER` (local setting, never in the
> repo), else the first connected account in a fixed order; a reply keeps the account
> that received the original.

`confirm` is **not allowed** on a destructive tool. Manifest validation refuses
`effect: destructive` together with `confirm`, the same way it already refuses `confirm` on a
read tool. There is therefore no way to declare `confirm: never` on a delete. Core built-in tools
follow the same rule in code: today none of them is destructive (`memory_forget` is reversible
through `user_fact_history` and stays `write`, `confirm: never`).

### 2. Every destructive call waits for a queued approval, itemised (owner decision 2)

At PreToolUse, a destructive call does not run and does not come back to the model as an
observation. `DestructiveApprovalHook` (priority 50, after every other PreToolUse deny):

1. writes **one approval row per call**, pinning the tool and its **exact arguments** as
   canonical JSON (sorted keys), so "is this what was approved?" is an exact comparison. The
   card text lists them. **A batch is one call that takes a list** (owner decision,
   2026-09-21): `trash_email(ids=[...])` is one row naming every id. The loop proposes one call
   per step, so a tool that takes one item at a time costs one approval per item;
2. tells the owner through the channel router, which also fires `approval.requested`, so the
   phone gets a banner (#579);
3. returns `require_approval` **carrying the row's id**. No new outcome was needed: a PreToolUse
   decision with an approval id is something to wait on, and the loop halts on it; the
   confirm-once decisions carry none and stay advice. That is how evaluator approvals already
   worked.

The loop then checkpoints the run with the approval id on it (ADR-0106), links the row to the
checkpoint (ADR-0108 §3), and ends the turn saying what is waiting and where to answer it. The
turn is not re-run by escalation (decision 5): that would raise a second approval.

The hook **refuses** instead of queueing when nobody could act on the answer: no approval
queue (approvals disabled), or a run that cannot pause and resume (a core built without a
checkpoint store, the approval link and the approval reader, such as a plugin's own
degrade-path loop). With no governance kernel at all, the loop refuses before asking.

The row's item list is a term of the request, so it joins `_IMMUTABLE_FIELDS` in the isolation
test: nobody, including the resume, can change what was approved after the fact.

**Approve** resumes the run through the turn pipeline (ADR-0108 §5). Before the model says
anything, the resumed loop **executes the pinned calls from the row**, not calls the model
generates again, and puts their results in as the paused step's observation. Each executed
call claims the approval, and the hook checks that claim against the row itself: the row
must be approved and pin exactly this tool with exactly these arguments. A forged id or a
changed argument is denied. The model then continues from real results. It cannot add an
item, change an argument, or say "and the other ones too" on the strength of the earlier approval.

**Reject** resumes the run too (owner decision, 2026-09-21), with the observation "the owner
rejected this: <items>". Nothing executes. The model continues and says nothing was deleted,
so the conversation closes instead of going silent. The approvals service resumes on a
rejection only for rows that pin items; a rejected evaluator halt stays halted, as before.

**Expiry** follows ADR-0108 §7: a lapsed row says so, and nothing executes. A destructive
approval stays answerable for **an hour** (owner's decision, 2026-09-21), since it is often
answered from the phone away from the conversation; evaluator halts keep the queue's
10-minute default.

This replaces "ask once, then the model decides". Consent is a field on a row, set by
`respond_to_approval`, which a human caller reaches through an authenticated surface. **No
agent-facing tool can answer it** (ADR-0108 §4 already guarantees this).

### 3. Reversible first, and still approved (owner decision 3)

Where the service has a reversible form (Gmail trash keeps mail for 30 days, FileManager
quarantine can restore), the tool **must use it**. It declares the undo tool in `undo:`, and the
approval card says how long the undo stays available. A tool that permanently deletes when a
reversible form exists is a review failure. The manifest cannot prove the service lacks one,
so this is enforced by the plugin contract test that ships with each destructive tool, not by
the loader.

Reversible deletes **still go through decision 2**. Being able to undo a mistake is a second
line of defence, not a reason to skip the first.

> **Amendment (2026-09-22, owner, promo-classification PR 3b): a reversible delete whose
> items CODE picks from a category the owner named runs without a card.** "Delete my promo
> emails for this week" matched 308 emails on the owner's real mail (about 60 promotions a
> day), and the owner asked why a card is needed at all when Trash keeps them 30 days. The
> card guards two risks: the model choosing the wrong items, and instructions hidden in
> mail steering a delete. The first is gone when no model picks the items. So the email
> plugin has two tools. `trash_email` (`effect: destructive`, unchanged) takes ids the model
> chose, and every call still waits for the card. `trash_category` is `effect: write`,
> `confirm: never`: given `{category, since_days}` it resolves the owner's word to the
> categories that exist (paths plus the Gmail tab label that maps to them), takes the newest
> 200 in the window, trashes them, and replies with the count, a sample, how many remain and
> how to undo it (`restore_email` with no ids restores the whole batch). Guards that replace
> the card: it moves nothing for a name that matches no category, or one that covers every
> category ("email"); 0 matches is "nothing to trash"; it never deletes permanently.
> **With no card, the mailbox's own bucket decides** (owner, same day, after the probe):
> when the provider has a label for the category (Gmail's Promotions tab), only mail carrying
> that label is trashed, wherever triage filed it. The first probe trashed 6 order, delivery
> and receipt emails that "promo" reached through IRIS's own `deals-promotions/amazon` path
> while Gmail had filed them as Updates. Browsing (`list_by_category`, `search_inbox`) still
> shows both. A category with no provider bucket is matched by its paths.
> Accepted residual risk (owner): injected mail can still ask for a category to be trashed
> without a card. Undo covers that for 30 days. The owner chose this over
> approve-once-per-day and over removing the card from `trash_email` too. The manifest keeps
> `undo:` on destructive tools only, so `trash_category` names its undo in its reply.

The undo tool itself is `write`, `confirm: never`: restoring what the owner just lost is what
their sentence ("undo that", "restore it") already authorises, the same argument ADR-0110 used
for `memory_restore`.

### 4. Who can approve (owner decision 4)

Any surface `respond_to_approval` already serves: a **paired `control` device** (the phone
today), the **CLI** (`iris approvals`), **Telegram**, and the web UI on a control session. A
`read` device can see the card but cannot answer it; that is already enforced (see Context).
**Face ID** is added when the Swift app lands (mobile plan Track 6, decision 15). Until then a
tap on a control device is the bar. The owner chose not to block destructive tools on Track 6.

### 5. Escalation never re-runs a turn that changed something

The ReAct loop records the declared effect of every non-`read` tool it invokes, on the sync
and the streaming path. It records the call **before** it runs, because a write that raised
half-way may still have changed something. A call governance held back never ran and is not
recorded. The react handler reports that list as `effects_executed` in the result's metadata,
and `resumed: true` when the turn continued a paused run. The executor already carries a
handler's metadata onto `AgentResult` on every path, so there is no separate field to keep in
step.

`maybe_escalate` acts on a verdict only when **every** result reports `effects_executed` and
the list is empty. That covers clarify as well as escalate and reroute: after a write, a re-run
can repeat it, and a question in place of the answer hides what was done. The check runs at
the top of **every hop**, because a re-run can itself write before the next one.

**Fail-closed:** a result that does not report `effects_executed` is treated as unknown and
not acted on. A plugin handler can write outside the loop, and a loop that wrote and then fell
back to a deterministic answer drops its report. Trusting silence would miss both. The
cost is that escalation only acts on turns the loop answered, which is where a model's answer
can fall short in the first place.

A resumed run (decision 2, and ADR-0106's `ask_user` resume) is never escalated: it exists
because a human already decided about this exact work. The curator still records the
judge's verdict on all of these turns, so shadow data keeps flowing.

This also closes the pre-existing gap for plain `write` tools. A duplicate reminder is a smaller
harm than a double delete, but it is the same bug.

### 6. Audit

Each executed destructive call writes an audit row carrying the approval id, the item, the
result, and whether an undo exists. The row is written to the side-effect ledger as well, so a
resume after a crash knows the call already ran and does not repeat it.

## Alternatives considered

- **Keep confirm-once and parse the reply for "yes".** Rejected. Deciding consent from free text
  is a keyword classifier in core, which the owner's rule of 2026-09-13 forbids, and it still lets
  the model regenerate the arguments after consent. The row pins what was consented to; a parser
  cannot.
- **An LLM judge decides whether the reply was consent.** Rejected for the same reason the
  escalation judge was just fixed (#580): the model under judgement ended up grading itself, and
  consent to data loss is not a place for a probabilistic answer.
- **One approval per item.** The owner chose itemised batches (decision 2). A per-item row is
  still what a one-call-per-step model produces, so nothing is lost for the careful case.
- **Reversible deletes stay `write`, `confirm: once`.** The owner rejected this (decision 3):
  confirm-once consent is checked by the model, and undo is a backstop, not consent.
- **Require Face ID now.** The owner rejected this (decision 4): the web app cannot do it, so
  every destructive tool would wait for Track 6.

## Consequences

- **No destructive tool exists today**, so this changes nothing a user sees until one ships. The
  first candidates are Gmail trash and calendar delete. The FileManager quarantine flow already
  asks through its own propose-then-approve intercept; moving it onto this mechanism is a later,
  separate change.
- **A new PreToolUse outcome**, `await_approval`, becomes a halt with a queue row. That is the
  first approval that pauses a run **before** a tool runs, not after. The resume has to execute
  the pinned calls, which ADR-0108 §5's resume does not do today: an approved evaluator halt
  resumes with a real tool result already in place.
- **The approval row gains an item list.** The isolation test's `_IMMUTABLE_FIELDS` grows by one
  field (ADR-0108 §9).
- **A destructive turn takes two turns.** That cost is the point.
- **Plugin authors** declare `destructive` and `undo`, and supply a one-line rendering per item
  for the card. The core never learns a plugin's vocabulary.
- **Reversible:** the new effect is additive. Removing it later means reclassifying the tools
  that use it; no stored data depends on it except the approval rows' item lists.

## Build order (a proposal, for the owner to reorder)

1. **Done.** Manifest: `destructive` and `undo`; `confirm` and `answers_directly` are refused on
   a destructive tool, and `undo` must name a `write`, `confirm: never` tool in the same
   manifest. The gate a destructive tool carries is `approval`, shown in the Plugins panel.
   Until step 3 builds the approval halt, **the loop refuses every destructive call** —
   before governance, so it holds with governance off, and after alias resolution, so a
   near-miss name cannot slip past. It tells the model nothing was changed. Declaring one
   is safe; running one is not possible yet.
2. **Done.** `effects_executed` in the loop's result metadata, and escalation skips turns that changed something
   (decision 5). This protects the `write` tools that exist today.
3. **Done.** The approval halt, itemised rows and a resume that executes the pinned calls
   (decision 2). Demoed by `tests/integration/test_destructive_approval_runtime.py`, a real
   runtime with a test destructive tool and a scripted model: the turn halts, and
   `respond_to_approval` (what the API, CLI and Telegram call) resumes it. The playground
   has no "answer the approval" step, so it was not the demo.
4. The approval card on the phone renders the item list and the undo window (decision 3).
   Prototype signed off 2026-09-21 (https://claude.ai/artifact/P7Xys8Dw98HNB2d24kk2pP), with
   the owner's answers: the item lines come from a plugin **`describe(args)`** function (it can
   look up subjects; ids mean nothing to the owner), run once and **frozen into the row** as a
   card (title, lines, declared undo tool and **`undo_window_days`**, and the owner's request);
   approving is a **red button naming the action** ("Trash 3 emails") with a confirm sheet.
   **4a (done):** `describe`, the manifest window, the frozen card (an immutable term of the
   request), plain words for every surface that reads text (`context_summary`: CLI and
   lapse notices; Telegram gets the card with Markdown escaped; the phone banner shows the
   title), `kind`/`card`/`items` on `GET /governance/approvals`, and a chat message that
   names the action and links to Activity. **4b (done):** the web card from the prototype on
   Activity (a red "deletes data" card: title, one line per item, the undo sentence, "You
   asked", the exact call behind a toggle, Reject and a red button naming the action); the
   confirm step reuses the app's confirm dialog (centred, not the prototype's bottom sheet)
   with the same words; rejecting needs no second tap. App-relative links in chat now stay
   in the app, so the halt message's link opens the card instead of a browser tab.
   **Telegram (owner's request, 2026-09-21):** the approval message carries buttons. A
   destructive one shows the action ("Trash 3 emails") and Reject; tapping the action asks
   "Trash 3 emails? Yes / Cancel" before anything runs, the Telegram form of the confirm
   sheet. Rejecting is one tap. See the ADR-0108 correction for how a tap is answered.
5. **Done.** The first real destructive tool: `trash_email` (email library, registered by
   `email_workflows`), `undo: restore_email`, `undo_window_days: 30`. Owner's decisions
   (2026-09-21): Gmail's grant moves from `gmail.readonly` to **`gmail.modify`** (trash and
   restore, never permanent delete; old tokens keep reading, trashing asks for a new consent),
   and **search results show each email's id** so the model names exact emails. The card
   lists each by subject, sender and date, looked up from the local store. A trashed message
   leaves the local store (Gmail's sync only adds mail, so it stays gone) and a `trashed`
   ledger keeps its account for `restore_email`, which with no ids restores the last batch
   ("undo that"). Demoed by `tests/integration/test_gmail_trash_approval.py` with the email
   plugin's real manifest; on the phone once the owner re-authorises (gmail-auth.md).
   **Found on the owner's first try (2026-09-21):** email requests were answered by the email
   plugin's own agent, a loop with no checkpoint store or approval wiring, so the destructive
   call was refused ("this run cannot pause") and the turn fell back to the inbox digest; a
   resume would also have been re-routed there. The step-5 tests had driven a loop directly,
   not the route the request takes. **Owner's decision: email joins the shared loop**, the
   way finance did (ADR-0077): `email` is a loop intent, and the plugin's agent is the degrade
   path through `_reassert_loop_intents`. The integration test now also drives a real runtime
   with the email plugin mounted, through the `email` agent, to `respond_to_approval`; with the
   old routing it fails with the owner's exact symptom. The real local router sends "trash the
   promo emails from this week" to `email`.
