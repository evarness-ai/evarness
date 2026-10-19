# ADR-0072: Judge-gated, optional feedback & clarify loop

Status: Accepted (2026-06-21) — all four slices implemented on
`fix/inbox-digest-and-chat-history`; flag-gated (`IRIS_FEEDBACK_CAPTURE`,
`IRIS_FEEDBACK_CLARIFY`), off by default. Realises issue 0006 **decision #2**.
Builds on ADR-0068 (curator-driven escalation judge) and the learning backbone
(ADR-0068 §4.2 measured outcomes). Complementary to ADR-0071, which decided *what to
retrieve*; this decides *when to ask the user* and *how to capture their feedback*.

Context: an answer can be wrong, ambiguous, or miss the user's intent — the
"pending payments → no results" dead-end was one symptom. Today the loop is
one-directional:

- The per-turn judge (`JudgeBundle` / `SignalVerdict ∈ {pass,warn,retry,halt,
  skipped}` in `core/response_curator.py`) and the escalation judge
  (`EscalationAction ∈ {accept,escalate,clarify,reroute}`, `core/escalation.py`)
  already exist, and `_maybe_escalate` can return a `clarify_question`
  (`bootstrap.py:6120`, rendered by `_make_clarify_handler` at `:4486`). But that
  clarify path fires on routing uncertainty, not as a gentle "did this cover it?"
  at the *answer* boundary.
- The learning store (`LearningSignalCollector.record_response /
  record_escalation_shadow / record_metric`) ingests only **measured** outcomes.
  There is **no explicit user-feedback signal**, and **no UI affordance** to give
  one. So the system can't learn from "that wasn't what I meant."

The user's instinct — "ask a feedback/clarifying question, maybe force a rating" —
is right about the *gap* but the "always / forced" framing would backfire (see
Alternatives). The crux: make it **judge-gated and optional**.

## Decision (proposed)

A layered loop. Each layer is independently shippable and flag-gated; the grounded
answer is never blocked.

1. **Dead-end gate (DONE).** 0-hit / tool-error / low-confidence retrieval already
   offers next steps instead of dead-ending (issue 0006; `search_inbox`). Keep as
   the cheapest layer.
2. **Judge-gated clarify at the answer boundary.** When the existing per-turn judge
   self-assesses low confidence — a `JudgeBundle` `warn`/`retry`, an escalation
   `clarify`/`reroute` verdict, a 0-hit retrieval, or low intent-router confidence —
   append **one** short clarifying / "want me to go deeper?" question via the
   existing clarify renderer. Hard cap: ≤1 per turn; never on a clearly-good or
   trivially-simple answer ("what time is it"). No new judge pass — it reads the
   bundle the curator already produced.
3. **Non-blocking feedback capture (web UI).** Every assistant message carries an
   always-present, never-required thumbs up/down plus an optional "not what I meant" that
   pre-fills a refine. A click POSTs a new `LearningSignalCollector.record_feedback`
   signal (rating + the turn's correlation ids: session/turn/trace). No modal, no
   gate, no required rating.
4. **Close the loop.** Explicit feedback becomes a first-class learning signal
   alongside measured outcomes — consumed by the analyst / escalation priors /
   crystallizer so routing and retrieval improve over time.

Gating (off by default, consistent with the repo): `IRIS_FEEDBACK_CLARIFY` (layer 2)
and `IRIS_FEEDBACK_CAPTURE` (layer 3). Capture before clarify, so signal exists to
tune the clarify thresholds against.

## Why judge-gated + optional (not always / not forced)

| Dimension | Always-ask / forced rating | Judge-gated + optional (chosen) |
|---|---|---|
| UX | Nags on obvious answers; users disengage | Asks only when genuinely unsure / high-stakes |
| Signal quality | **Forced → satisficing → noisy** training data | Volunteered → correlates with real (dis)satisfaction |
| Latency / cost | Extra turn + judge every time | Piggybacks the existing `JudgeBundle`; clarify is rare |
| Loop risk | More turns → more misroute exposure (issue 0002) | Fewer, context-grounded follow-ups |

Aligns with [[feedback-agentic-over-heuristic]] (a judge decides *when*, not a
blanket rule) and reuses shipped machinery rather than adding a parallel system.

## Privacy

Feedback is local: stored in the learning store next to measured outcomes; never
egresses. It records a rating + correlation ids + optional short free-text, not
message bodies. Honors [[feedback-never-commit-emails]] — no email content persisted
by this path.

## Alternatives considered

- **A. Always ask / force a rating before continuing.** Rejected: fatigue +
  satisficing (noisy data, worse than none) + friction.
- **B. Implicit signals only** (rephrase detection, dwell-time, abandonment).
  Complementary and worth adding later, but lower-signal and inferential; explicit
  thumbs up/down is cleaner and unambiguous, so do it first.
- **C. A dedicated second quality-judge pass per turn.** Rejected: redundant latency
  / token cost; the curator's `JudgeBundle` already yields the needed verdicts.
- **D. Clarify on every turn regardless of confidence.** Rejected — that's "always
  ask" wearing a clarify hat.

## Consequences

- **Positive:** recovers from misunderstanding in-line; gives the learning loop its
  first *explicit* signal; small, additive surface; reuses the escalation judge +
  clarify handler.
- **Negative / cost:** new web-UI affordance + a feedback signal/table/endpoint; a
  per-turn gate decision (cheap — reads the existing bundle); risk of over-asking if
  thresholds are loose → start conservative and tune against captured signal;
  channel asymmetry (thumbs up/down is natural in the web UI; CLI/voice degrade to an inline
  text affordance or skip layer 2).
- **Reversibility:** high — both layers are flag-gated and additive; disable to
  revert with no schema/behaviour left behind beyond an unused table.

## Implementation status

1. **DONE** — `LearningSignalCollector.record_feedback` (the `user_feedback` signal,
   no new table) + `POST`/`GET /api/feedback`. Live-verified.
2. **DONE** — Web UI: non-blocking thumbs up/down + "not what I meant" on
   `ChatMessage` → `POST /api/feedback`; gated by `IRIS_FEEDBACK_CAPTURE` via
   `/capabilities`; absent when off. Live-verified.
3. **DONE** — Judge-gated clarify at the answer boundary: `_clarify_warranted` (pure,
   conservative) + a one-line nudge appended in `_finalize_chat` (covers sync chat +
   streaming + the recorded turn). `IRIS_FEEDBACK_CLARIFY`, off by default.
   **Caveat (verified live):** intentionally rare — genuine ambiguity already routes
   to the dedicated clarify *agent* (suppressed here), and the strong trigger (a
   judge `warn`/`retry`) only appears when the grounding/faithfulness judges are
   enabled (they report `skipped` by default). So the nudge is a backstop for
   judge-flagged answers; enable the grounding judge to exercise it often.
4. **DONE** — Loop integration: `FeedbackSummary` folds the `user_feedback` signal
   into the `IntelligenceReport` (totals / satisfaction / per-intent), so the agentic
   analyst — which reasons over that report — now weighs self-reported dissatisfaction
   alongside the measured matrix. A "User feedback" panel on the Learning screen
   surfaces it. Deeper consumers (escalation-priors / crystallizer retraining on
   feedback) remain a follow-up — the analyst path is the proportionate close-the-loop.

## Related ADRs

- ADR-0068 (curator escalation judge) — supplies the `clarify` verdict + judge bundle
  this gate reads.
- ADR-0069 (System Health harness) — the read-only-by-default Web UI pattern to mirror
  for the feedback affordance + screen.
- ADR-0071 (semantic retrieval) — reduces *how often* a clarify is needed; this ADR
  handles the residual ambiguity and the learning signal. Issue 0006 decision #2.
