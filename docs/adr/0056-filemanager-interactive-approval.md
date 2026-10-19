# ADR-0056: FileManager interactive approval — agent requests, human approves destructive ops

Status: Accepted (2026-06-14)
Context: FileManager agent FM4. FM1 made destructive ops (delete/move/overwrite) R3 and, to avoid an LLM self-approving (`approved=true`), kept them **human-CLI-only** — the agent could only *describe* a deletion, not initiate it. FM4 closes that gap with a request→approve round-trip, so the agent can drive destructive ops end-to-end while a human stays in the loop.

## Decision

1. **Agent requests; it can never execute or self-approve.** New
   `FileWriteGovernor.request(kind, path, …)` parks a destructive op
   (delete/move/overwrite) in an **op-aware pending store** (`pending_mutations`:
   kind, path, dst, content) and returns a request id. The op does **not**
   run. The `file-compose` skill exposes `request_delete` / `request_move` /
   `request_overwrite` — the model's only path to a destructive op, and they
   only park.

2. **A human approves, which executes — reversibly.** `approve(request_id)`
   (the CLI `iris files approve <id>`, human-initiated) loads the parked op and
   runs it via the existing FM1 governor methods with `approved=True`, so it
   stays **soft + undoable** (trash snapshot + undo token). `deny(request_id)`
   rejects without executing. `iris files approvals` lists what's pending.

3. **Confinement enforced at request time.** `request` runs the path through
   the FM0 safety gate up front, so an out-of-root / protected-path request is
   rejected immediately (not parked and rejected later). Single-use: a
   request can be approved or denied once.

4. **Op-aware store, not the generic governance queue.** The governance
   `ApprovalQueue` stores *signals*, not file payloads; executing a parked file
   op needs the path/dst/content, so FM4 uses a self-contained
   `PendingMutationStore` (the FileManager boundary pattern, like the undo
   store), referencing the governance queue as the generic analogue.

## Alternatives considered

- **Let the request tools self-approve (`approved=true`).** Rejected — that's
  exactly the FM1 hole; an LLM would authorise its own deletes.
- **Reuse the governance `ApprovalQueue`/`ApprovalGate`.** Rejected for storage
  — it can't hold the op payload needed to execute on approval. The flow is the
  same ethos (enqueue → human responds), but op-aware.
- **A blocking async `ApprovalGate.await_approval` round-trip inside the tool.**
  Rejected — a synchronous park-and-return is simpler, works for CLI + chat, and
  doesn't hold an agent turn open waiting on a human.
- **Keep destructive ops human-CLI-only (FM1).** Superseded — the agent should
  be able to *initiate* (propose-and-park) destructive ops; the human just
  approves, rather than retyping the whole command.

## Consequences

**Positive:**
- The agent can now manage files end-to-end — including destroys — with a human
  one-command approval, and every approved op is still reversible (undo token).
- The LLM still cannot destroy a file on its own: it can only park a request.
- Confinement/denylist/audit apply at request *and* execution. 8 new tests.

**Negative / deferred:**
- Approval is out-of-band (a separate `iris files approve`), not an in-turn
  interactive prompt; a real-time channel round-trip (Telegram/CLI confirm) is
  future work.
- Pending requests don't expire/auto-purge; stale ones linger until approved/
  denied (a TTL is future work).
- Recursive directory delete/move and Drive write-back remain out of scope.

## Related ADRs

- ADR-0052 (FM1 governed mutations — the ops approved here), ADR-0051 (FM0
  confinement — enforced at request), ADR-0008 (R2/R3 governance), the
  governance ApprovalQueue (generic analogue). FM4 completes the FileManager's
  "agent creates, human destroys" model into "agent requests, human approves".
