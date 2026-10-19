# ADR-0058: FileManager pending-approval TTL — stale destructive requests expire

Status: Accepted (2026-06-14)
Context: FileManager agent FM5. FM4 (ADR-0056) added the agent-requests / human-approves round-trip: a destructive op (delete/move/overwrite) is parked in `PendingMutationStore` until a human runs `iris files approve <id>`. But parked requests never expired — a stale request from days ago could still be approved verbatim, and `iris files approvals` listed it indefinitely. FM5 adds a time-to-live so old requests can't be silently executed and drop out of the pending view.

## Decision

1. **Pending requests have a TTL.** `FileWriteGovernor` gains
   `pending_ttl_seconds` (default `DEFAULT_PENDING_TTL_SECONDS = 86400`, 24h). A
   request is expired once `created_at + ttl < now`. This mirrors the existing
   FM1 **undo-window** expiry (`undo` already rejects when
   `now > expires_at`); FM5 reuses the same `created_at + window < now` style,
   computing expiry from the stored `created_at` rather than persisting a
   separate `expires_at` column.

2. **Expired requests can't be approved.** `approve(request_id, now=…)`, after
   confirming `status == "pending"`, rejects an expired request with
   `_not_performed("R3", "request <id> expired")` and does **not** execute the
   op. `now` is injectable (defaults to `datetime.now(UTC)`); `created_at` is
   parsed from its ISO string with `datetime.fromisoformat`.

3. **Expired requests are hidden from the pending list.** `list_pending(now=…)`
   filters out rows whose `created_at + ttl < now`. The signature stays
   backward-compatible (`now` optional, defaults to `datetime.now(UTC)`), so the
   CLI's `list_pending()` keeps working unchanged.

4. **Optional purge.** `purge_expired(now=…) -> int` marks stale pending rows as
   `status = "expired"` (new `PendingMutationStore.mark_expired`) and returns the
   count, so a sweep can durably retire stale requests rather than relying only
   on the read-time filter.

## Alternatives considered

- **Persist a separate `expires_at` column** (as the undo store does). Rejected
  — `created_at` is already stored; deriving expiry from it + a configurable TTL
  avoids a schema change and keeps the TTL adjustable per-governor.
- **Hard-delete expired rows.** Rejected — marking them `expired` preserves the
  audit/history of what was requested; the read filter already hides them.
- **Expire at request time / background timer.** Rejected — a read-time + on-approve
  check (plus an optional explicit purge) needs no scheduler and is deterministic
  under injected `now` for tests.

## Consequences

**Positive:**
- A stale parked request can no longer be approved and silently executed; it
  also disappears from `iris files approvals`. Closes the FM4 deferred TTL item.
- `now`-injection keeps it fully deterministic to test (no real-clock flake).

**Negative / deferred:**
- No background purge scheduler — `purge_expired` is opportunistic; expired rows
  linger as `pending` in storage until a purge runs (but are filtered + un-approvable).
- TTL is a single flat window for all kinds (no per-kind / per-path policy).

## Related ADRs

- ADR-0056 (FM4 interactive approval — the request/approve flow this bounds),
  ADR-0052 (FM1 governed mutations — whose undo-window expiry this mirrors),
  ADR-0008 (R2/R3 governance). FM5 makes the "agent requests, human approves"
  loop self-cleaning: requests not approved within the window expire.
