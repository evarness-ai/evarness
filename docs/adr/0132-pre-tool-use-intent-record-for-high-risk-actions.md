# ADR-0132: A durable PRE_TOOL_USE intent record for high-risk/destructive actions

Status: Accepted (2026-10-05)
Relates to: `kernel/governance/side_effects/store.py` (`SideEffectLedger`, the
mechanism this ADR extends), `kernel/governance/hooks/types.py` (`HookPoint`),
`docs/oss-plan-review.md`, the L4.5 Harness Hardening milestone.

## Context

The kernel made a conscious choice: audit failure should not break enforcement.
Best-effort audit is the right default for reads and routine model calls. For
irreversible side effects (send email, delete a file, a governed write), it creates a
real gap: the provider can succeed, and then the process can die before the
`POST_TOOL_USE` hook's audit write lands. IRIS has acted; the evidence of that act does
not exist.

This is not a greenfield problem. `SideEffectLedger`
(`kernel/governance/side_effects/store.py`) already exists and already solves a
closely related problem: it is an append-only ledger, written from a `POST_TOOL_USE`
hook, that records a side effect plus a verification probe, so a resume flow can later
decide to skip, re-execute, or enqueue an approval for a side effect whose outcome was
never confirmed. What it does not yet do is write *before* the tool call — so the exact
crash window the review describes (provider succeeds, process dies before
`POST_TOOL_USE` fires) still produces an audit ghost: an action with no record at all,
not even a pending one.

## Decision

1. **Three audit risk classes**, fired from existing hook points — no new hook
   machinery:
   - **Low risk** (reads, routine model calls): best-effort audit, unchanged.
   - **Write** (reversible writes): best-effort audit, unchanged — this ADR does not
     touch this class.
   - **High-risk/destructive** (classification already exists on the tool/capability
     side — e.g. `send_email`, a governed file delete, a mailbox write): gets a durable
     **pre-execution intent record**, written from a new `PRE_TOOL_USE` hook into
     `SideEffectLedger`, before the tool call runs. The record carries the same shape a
     `POST_TOOL_USE` row already carries (`run_id`, `step_id`, `tool`, a
     `verification_probe`) but with `status="pending"` and no `completed_at` — which is
     exactly the shape `SideEffectLedger.pending(run_id)` already knows how to read on
     resume.

2. **The `POST_TOOL_USE` hook updates the same row** (by `side_effect_id`) to
   `completed`/`not_completed`/`error` instead of inserting a fresh one, closing the
   loop the existing resume flow was designed around.

3. **Classification reuses what the kernel already has.** The kernel already
   distinguishes effect and side-effect at the capability/tool level (`core-boundary.md`:
   "effect, side effect" are things the kernel understands, not business meaning); this
   ADR does not invent a new taxonomy, it adds one consequence (durable pre-record) to
   the existing "high-risk" classification.

## Alternatives considered

- **A distributed-transaction / two-phase-commit system.** Rejected explicitly — this
  is a tiny write-ahead ledger extension, not a transaction coordinator. The review's
  own framing ("almost a tiny write-ahead ledger... don't build a distributed
  transaction system") is the design constraint, not just color commentary.
- **Make all audit writes durable pre-execution.** Rejected: that reintroduces the
  exact cost/latency trade the kernel's best-effort choice was made to avoid, for the
  large majority of calls (reads, routine model calls) where losing a race against a
  crash has no real consequence.
- **A brand-new ledger instead of extending `SideEffectLedger`.** Rejected: the
  existing ledger's row shape, probe mechanism, and resume-time `pending()` query
  already do almost everything this ADR needs; a parallel ledger would duplicate it
  for no benefit.

## Consequences

- Positive: a crash between "provider succeeded" and "audit write landed" for a
  high-risk action now leaves a `pending` row an operator (or the resume flow) can find
  and reconcile, instead of nothing.
- Negative: high-risk tool calls take on one additional synchronous, durable write
  before execution — acceptable latency for calls that are already rare and
  user-visible (send email, destructive file ops), not for the hot path.
- Reversible: this is an additive change to an existing, append-only ledger; nothing
  about the low/write risk classes changes.

## Files

- `src/iris_harness/kernel/governance/side_effects/store.py` — `SideEffectLedger`,
  extended with the pre-execution insert + post-execution update.
- `src/iris_harness/kernel/governance/hooks/` — the new `PRE_TOOL_USE` hook
  registration for high-risk tools.
- `docs/architecture/unified-governance-layer.md` §0 — update the "what's shipped" map.
