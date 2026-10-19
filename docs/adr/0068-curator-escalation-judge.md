# ADR-0068 — Curator-driven tier-escalation judge (model cascade)

- **Status:** Accepted (fully implemented L1-L5 incl. egress-gated cloud escalation; enforce/priors/cloud off by default)
- **Date:** 2026-06-18
- **Design doc:** `docs/architecture/escalation-judge.md`
- **Builds on:** the Phase-5 Response Curator judge pattern
  (`docs/architecture/grounding-judge.md`), the tier router
  (`src/iris_harness/llm/tier_router.py`), and the learning subsystem
  (`docs/architecture/learning-subsystem.md`)

## Context

Tier selection is static today: `IntentRouter` classifies a turn and
`TierRouter.get_llm_config(intent)` returns a fixed tier — a prediction made
*before* any output exists. There is no correction path when a cheap Tier-1
(local) response turns out to be inadequate. We want a **reactive, model-driven**
escalation: after Tier-1 answers, judge it, and if it falls short, decide the
next course — a stronger tier, a clarifying question, or a tool — instead of a
fixed mapping. This is a model cascade (FrugalGPT economics): cheap by default,
spend a stronger tier or the user's attention only by exception, which fits a
local-first system.

Two placements were considered: an LLM judge at the **router** (pre-call) vs. at
the **curator** (post-call).

## Decision

Add a **tier-escalation judge as a Response Curator head**, post-generation. Its
verdict is a **diagnosis**, not a scalar score:
`{accept | escalate(tier) | clarify(question) | reroute(tool)}`, because a bigger
model only fixes a *capability gap* — an *ambiguity* needs the user, a *grounding
gap* needs a tool. Key decisions (full set in the design doc §4):

1. **Curator-time, not router-time** — no pre-call LLM tax on every turn; the
   router instead learns escalation priors from the curator's recorded history.
2. **Reuse the existing retry spine** — `JudgeSignal`/`JudgeBundle`
   (`retryable`/`retry_budget`/`retries_used`) carry it; add `max_escalations`
   and honor the governance cost cap. The runtime general handler executes the
   action (re-invoke at the escalated tier / emit the clarify turn / add a tool
   step); `curate()` owns only the decision.
3. **Egress gate is a hard constraint** — cascade is `T1 → T2 → T3 local`; cloud
   escalation only for egress-eligible content. Personal/secret never goes off-box.
4. **Judge ≥ generator tier; deterministic checks first;** the model never both
   generates and unilaterally promotes its own output.
5. **Compose with the resource governor** — escalation is an upshift the
   governor's downshift can veto under resource pressure.
6. **Shadow-first** — `config/escalation.yaml` `mode: shadow` decides+logs but
   never acts until shadow data shows escalation improves outcomes net of cost,
   same shadow → validate → enforce discipline as the governance enforce flips.
7. **Clarify is confidence-gated** — "agent asks, user decides," but only above a
   confidence floor; prefer escalate/reroute when the answer is knowable.

## Consequence

- Most traffic stays on cheap local Tier-1; cost/latency are spent by exception.
- The curator's escalation decisions + outcomes become first-class learning
  signals — the flagship consumer of the enriched outcome signals — which later
  make the router predictive (start-tier tuning via `learning/strategies.yaml`).
- No parallel tier/retry machinery: escalation rides `JudgeBundle` accounting and
  `TierRouter.get_llm_config`.
- Risks (reward hacking, cost runaway, over-asking, privacy leak) are bounded by
  D3–D8; the egress invariant is a security test, not a guideline.

## Status / next

**Accepted; shipping incrementally.** Landed: L1 (observability backbone, #176),
L2 (escalation judge in **shadow**, #177), the §4.2 measured outcomes (#178/#179)
that make shadow precision measurable, and the full **enforce-mode action path**
(L3 + L4) — **escalate** (`capability_gap` -> stronger local tier), **reroute**
(`grounding_gap` -> same tier with a grounding/tool directive), and **clarify**
(`ambiguity` -> ask the user, confidence-gated). All bounded by `max_escalations`
+ resource-governor veto, with the egress invariant held by construction
(local-only). **L5 predictive priors** also landed — the router mines
`escalation_shadow` history into per-intent start-tier priors so reliably-
escalating intents start higher (reactive → predictive). **Cloud escalation**
also landed — when `cloud.allow` is on, a cloud tier is a valid escalation target
only for content that classifies as egress-eligible (`secret` never; fail-closed;
the kernel egress gate is the backstop). The design is now fully implemented.
Acting, priors, and cloud escalation are all off by default (`mode: shadow`,
`priors.enabled: false`, `cloud.allow: false`) — same shadow → validate → enforce
discipline as the governance enforce flips. See
`docs/architecture/escalation-judge.md`.
