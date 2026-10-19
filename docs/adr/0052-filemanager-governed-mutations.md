# ADR-0052: FileManager governed mutations — R2/R3 + soft, reversible ops

Status: Accepted (2026-06-14)
Context: FileManager agent FM1. Adds the destructive surface (write/overwrite/move/delete/mkdir) on top of FM0's read foundation (ADR-0051), gated by the ADR-0008 R2/R3 model and the calendar write-governor pattern.

## Decision

1. **R2/R3 tiers by destructiveness.**
   - **R2** (create new file, mkdir): performed immediately, recorded with a
     600s undo token (undo removes what was created).
   - **R3** (overwrite, move, delete): **not** performed unless
     ``approved=True``; the caller is told to confirm.

2. **Destructive ops are soft + reversible.** Overwrite snapshots the prior
   bytes into a trash dir before writing; delete *moves* the file to trash
   rather than unlinking; move records the destination. `undo(token)` within
   the window restores the prior state (copy-back / move-back / remove). Files
   are never hard-destroyed inside the undo window.

3. **FM0 safety still gates every mutation.** Each path goes through
   `resolve_within_roots` (deny-by-default confinement, symlink-escape,
   protected denylist) and every mutation — and denial — is audited. Writing
   to `.env`, `~/.ssh`, outside a root, etc. is impossible.

4. **The agent can create, only the human can destroy.** The opt-in
   `file-compose` skill exposes **only R2** tools to the model (`create_file`
   new-only, `make_directory`). Overwrite/delete/move are **not** model-
   callable — they're human-initiated via `iris files write --overwrite
   --yes` / `rm --yes` / `mv --yes`. This closes the hole where an LLM could
   set `approved=True` itself and self-authorise a destroy.

5. **Files only for delete/move.** Recursive directory delete/move is deferred
   (higher blast radius); FM1 deletes/moves single files and only *creates*
   directories.

## Alternatives considered

- **Expose delete/move/overwrite as model tools with an `approve` arg.**
  Rejected — an LLM would just pass `approve=true`, defeating the gate.
  Destructive ops stay human-CLI-only until a real interactive-approval
  round-trip (ApprovalQueue) exists.
- **Hard delete with a content snapshot in sqlite.** Rejected — large files
  bloat the DB; move-to-trash is simpler, reversible, and bounded by disk.
- **No undo (rely on approval alone).** Rejected — approval prevents *accidental*
  destruction but not *mistaken* approval; a 600s undo + soft trash is the
  safety net, matching the calendar R2 undo ethos.
- **Recursive directory ops now.** Deferred — too blast-prone for this slice.

## Consequences

**Positive:**
- A real "manager": create/overwrite/move/delete, all confined, audited, and
  reversible; destructive ops require explicit human approval and can be undone.
- Agent-mediated file creation is safe (R2, undoable, in-root); destruction is
  human-gated — a strong security posture for the most sensitive agent.
- 14 mutation tests (R2 create/mkdir + undo; R3 overwrite/delete/move approval +
  soft-restore; confinement/protection; undo window/single-use). Verified live.

**Negative / deferred:**
- Recursive dir delete/move not supported.
- R3 is human-CLI approval, not yet an interactive agent→human round-trip
  (ApprovalQueue) — so the agent can propose but not execute a destroy.
- Trash isn't auto-purged after the undo window (retained; a cleanup job is
  future work). The trash dir holds prior bytes of overwritten/deleted files —
  gitignored, local.

## Related ADRs

- ADR-0051 (FM0 secure read foundation — confinement reused), ADR-0008 (R2/R3
  governance), ADR-0042 (CalendarWriteGovernor — the pattern mirrored here),
  ADR-0002 (local-first privacy).
