# ADR-0028: `email-followup` skill — implementation shape

Status: Accepted (2026-06-10)
Context: Personal-assistant upgrade Phase 2 Track 2B. Parent ADRs:
ADR-0005 (Task vocabulary, followup-as-Task-variant), ADR-0014
(TaskStore + `resolve_wait` semantics), ADR-0022 (Tier-3-only-on-demand).

## Context

The canonical doc §3.1 specifies `email-followup` as a skill that
turns inbound personal mail requiring a reply into a tracked Task
with `wait_for` set, and auto-resolves the wait when the awaited
reply arrives. The original design implied auto-fire detection on
every classified email.

ADR-0022 (May 2026) materially changed the cost model: Tier-3-local
(25 GB peak RAM) is invoked only from explicit user-invoked CLIs;
per-email LLM workload on the auto-fire path was removed in favor of
pure-kNN classification. That stance ripples into Track 2B —
"does this email need a reply?" is itself a Tier-3-local
question, so detection cannot auto-fire either.

This ADR records the implementation calls made to fit Track 2B
inside the ADR-0022 cost envelope while preserving the auto-resolution
half (which is cheap).

## Decision

Ten implementation decisions:

1. **Split the skill into two halves with different cost profiles:
   CLI-driven detection, subscriber-driven auto-resolution.**
   Detection costs one Tier-3 call per email and is reached only
   through `iris email detect-followups`. Auto-resolution is a
   thread_id lookup against `data/tasks.db` triggered by
   `email.new_arrived`; no LLM call, runs continuously inside
   `IrisRuntime.startup()`. The two halves share only the
   `Reminder` (sic — Task) and the `followup_key` dedup contract.
   Rejected: auto-fire detection on `email.classified` — would
   re-introduce the per-email Tier-3 load ADR-0022 spent its
   budget removing.

2. **Reuse the `LlamaServerClient` transitional shim** from
   `iris_harness.email.discovery` rather than wiring through
   `tier_router.get_llm_config("classify")`. Matches the existing
   triage and discovery call sites; preserves ADR-0022 §5's
   deferral of Track 1H (canonical tier-router wiring) until a
   second consumer (Track 1J or 1K) needs the canonical surface.

3. **Skip threadless emails.** Emails arriving without a
   `thread_id` (rare on Gmail, common on raw IMAP without
   threading) cannot anchor auto-resolution — there is no
   identifier the inbound subscriber can match against. The
   detector returns `FollowupOutcome(action="skipped:no-thread")`
   before any LLM call. Rejected: creating a Task with
   `wait_for=None` (degrades the contract — every followup task
   has a real wait condition) or a synthetic anchor like
   `from_address` (false-positives on automated mail).

4. **Dedup contract: `followup_key(provider, thread_id)`,
   exactly one followup task per thread.** `detect_and_persist`
   calls `TaskStore.get_by_dedup_key` BEFORE invoking the LLM,
   short-circuiting on `skipped:already-exists` and saving the
   Tier-3 call. Rejected: dedup by `(account_id, from_address)`
   — would collapse distinct threads from the same sender into
   one followup; dedup by `email.id` — would create one followup
   per message in a thread.

5. **Auto-resolution matches on thread_id alone.** Any new
   message arriving in the same thread flips the followup's
   `wait_for_resolved_at`, regardless of whether the new message
   is from the original sender, the user, or a third party CC'd
   in. Rejected: gating resolution on `from_address` equality —
   the wrong semantic when the conversation has moved on
   (someone else replying, user's own outbound, multi-party
   thread); the user's perspective is "this thread moved", not
   "this exact person responded".

6. **Auto-resolution does NOT auto-complete the task.** Per
   ADR-0014 #10, `TaskStore.resolve_wait` sets
   `wait_for_resolved_at` but leaves `status='open'`. The user
   sees the resolved followup in the brief's "Replies arrived"
   slot and decides whether to `iris task complete` or re-open
   the conversation with another `wait_for`. Rejected:
   auto-completing — silently closes work the user may still owe
   (e.g. a reply they need to send back).

7. **LLM contract: strict JSON output** —
   `{"needs_reply": bool, "rationale": str, "confidence": float}`.
   Soft-fail on JSON decode error or LLM connection error returns
   `FollowupDecision(needs_reply=False, rationale="...-error: ...")`
   so the batch CLI logs and continues to the next email rather
   than aborting mid-pass. Rejected: free-form text answer parsed
   by regex (too brittle); structured tool-call output (requires
   tier-router wiring deferred per ADR-0022 §5).

8. **Confidence threshold default 0.6, exposed via
   `--confidence`.** The LLM self-reports `confidence`; we gate
   on it as a cheap calibration knob without forcing the prompt
   to be calibrated. Rejected: hardcoded 0.5 (no operator
   control); learned threshold (no signal volume yet —
   revisit when Track 1J's self-learning substrate spans
   followups).

9. **Detection prompt takes no classification context input
   in MVP.** The `category_path` parameter exists for forward
   compatibility but is not threaded into `_build_user_prompt`.
   The prompt asks the LLM to judge the email on its own merits
   from sender + subject + snippet. Rejected for MVP: feeding
   `category_path` into the prompt — would bias toward
   "personal" categories needing replies and "shopping" not,
   which is the kind of hand-coded heuristic ADR-0017's
   self-learning ethos rejects.

10. **Subscriber wired in `_register_default_heartbeats`
    next to the wiki-ingestion subscriber.** Uses
    `iris_harness.eventbus.get_default_bus()` rather than receiving the
    bus via DI. Matches `subscribe_email_classified_to_wiki`'s
    call site exactly — the convention is "wire all subsystem
    subscribers in one stanza in bootstrap.py for grep-ability".
    A future DI refactor (deferred per ADR-0013) lifts both
    subscribers at once.

## Alternatives considered

1. **Auto-fire detection on `email.classified`.** Original
   design intent. Rejected by ADR-0022's cost envelope.

2. **Pre-filter sender allowlist before invoking the LLM**
   (e.g. "only known correspondents from the wiki get the LLM
   call"). Rejected: hardcoded heuristic; conflicts with
   "agentic / model-driven design over hand-coded heuristics"
   project stance.

3. **Use Tier 2 (Ollama qwen2.5:7b) instead of Tier 3 local.**
   Cheaper per-call. Rejected: ADR-0022 §A explicitly evaluated
   Tier 2 for the triage picker and concluded the quality wasn't
   verified for the picker task; same uncertainty applies to
   "needs reply?" judgment, and the gain from cheaper inference
   doesn't outweigh the risk of misclassified personal mail.

4. **Per-tool prereqs on the manifest** (e.g.
   `list_open_followups` declares `data/tasks.db` but
   `detect_followups` does not). Rejected: skill loader is
   skill-level, not tool-level; per-tool prereqs would need a
   schema extension warranting its own ADR. Workaround: the
   tools auto-create their DBs via `ensure_schema` on first
   call.

5. **Auto-resolution that completes the task.** Convenient
   default. Rejected per ADR-0014 #10 — silently closes
   followups the user may need to act on after the wait.

## Consequences

**Positive:**

- The cost-model envelope from ADR-0022 stays intact through
  Phase 2. Tier-3-local still spins up only for explicit user
  invocations; the auto-fire path adds zero LLM load.
- The dedup contract via `followup_key` means re-running
  `iris email detect-followups` is a no-op for already-tracked
  threads — operators can re-run freely without burning
  duplicate Tier-3 calls.
- 16 tests in `tests/unit/test_email/test_followup.py` cover
  JSON parsing, confidence clamping, soft-fail on LLM error,
  idempotent upsert, threadless skip, resolve-on-thread-match,
  and four no-op edge cases.

**Negative:**

- Detection is manual-only. Operators who never run
  `iris email detect-followups` will accumulate untracked
  replyable threads. The brief surfaces this — an empty
  "Awaiting reply" section on a busy inbox is a hint to run
  detection — but there is no automated nudge.
- Thread-id-only auto-resolution misfires when the user
  themselves sends a reply: the followup resolves before the
  awaited counterparty responds. Acceptable because (a) the
  task is then a "remind me if they don't reply by `due_at`"
  pattern that's served by adding a Reminder (ADR-0030), and
  (b) the user is the source of truth on whether they consider
  the followup done.
- Detection prompt is content-only (no classification context).
  May misfire on edge cases where category is the more
  reliable signal. Revisit once the prompt has been observed
  against the user's real inbox.

**Reversibility:** medium-high. The CLI is one self-contained
function; the subscriber is one self-contained registration in
bootstrap.py; the dedup contract is one helper. Adding
auto-fire detection later (when ADR-0022's cost gate relaxes)
is a one-line subscriber addition that calls
`detect_and_persist` on `email.classified`. Switching
auto-resolution to require `from_address` match is a single
check in `_resolve_thread_followup`.

## Related ADRs

- ADR-0005 (parent) — Task vocabulary; followup as Task
  variant with `wait_for`.
- ADR-0014 (parent) — TaskStore `resolve_wait` semantics and
  the no-auto-complete decision this ADR inherits.
- ADR-0017 — dynamic email classification that produced the
  classified mail this skill scans.
- ADR-0022 — resource-aware triage revision whose cost envelope
  shapes Track 2B's CLI-only stance.
- ADR-0027 (sibling) — `iris task` CLI that surfaces the
  followup tasks this skill creates.
- ADR-0029 (sibling) — brief integration that consumes
  `list_open_followups` from this skill.
