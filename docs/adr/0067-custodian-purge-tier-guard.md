# ADR-0067 — FileManager never deletes files it doesn't own (purge tier guard)

- **Status:** Accepted
- **Date:** 2026-06-18
- **Builds on:** ADR-0062 (custodian + catalog), ADR-0064 (indexer),
  ADR-0065 (Photos), ADR-0066 (RAG catalog)

## Context

Granting IRIS **Full Disk Access** (needed to index iCloud Drive / the Apple
Photos library) widens what FileManager can *reach*. That raised a safety
question: can cataloguing ever delete a user's real files?

Audit of every delete path in `src/iris_harness/filemanager` + `src/iris_harness/rag`:

- **Indexer (FMX2), Photos (FMX3), RAG ingest (FMX4)** — strictly read-only:
  `open(..., "rb")`, `stat`, and a read-only (`mode=ro&immutable=1`) SQLite
  open. No `unlink`/`rmtree` anywhere.
- **Governed delete (`FileWriteGovernor`)** — soft: snapshots to trash, R3 +
  human-approved, agent can't self-approve, reversible via undo. Its only
  `unlink` reverts an *undo of a create* (a file IRIS itself just made).
- **`FileCustodian.enforce_retention()`** — filters `location_tier="managed"`
  and observer entries carry `retention_days=0`, so the daily sweep can never
  touch in-place files.
- **`FileCustodian.purge()`** — **the gap.** It unlinked `entry.storage_path`
  for *any* tier. For an indexer/Photos/RAG entry that path is the user's real
  original, so `iris files catalog purge <id>` would have deleted it.

## Decision

`purge()` may only ever delete bytes FileManager **owns** — the `managed` tier
(files the custodian itself wrote under `data/filemanager/store/`). For any
in-place tier (`os_local`, `icloud`, `photos`) it **refuses** and never calls
`unlink`. To remove a stale catalog row for an in-place file, a new `forget()`
drops the row **without touching the file**. Deleting a real file remains
possible only through the governed path (soft, undoable, human-approved).

The CLI mirrors this: `iris files catalog purge` refuses non-managed entries
with guidance; `iris files catalog forget` drops a row, file untouched.

## Consequence

The guarantee is now structural, not incidental:

> **FileManager never deletes a file it did not itself create.** Indexing,
> Photos cataloguing and RAG registration are read-only; retention only reaps
> the custodian's own managed bytes; `purge` is tier-guarded; and the only way
> to delete a real user file is an explicit, human-approved, reversible
> (trash + undo) governed operation.

Full Disk Access therefore broadens *read/catalogue* reach only — it grants no
new ability to destroy data.

## Verification
- `tests/unit/test_filemanager/test_custodian.py`:
  `test_purge_refuses_in_place_files_and_never_unlinks` (real file survives, not
  marked deleted), `test_forget_drops_row_without_deleting_file`,
  `test_retention_never_touches_in_place_files` (long window set, tier filter
  still excludes it). 128 filemanager tests pass; ruff/black/mypy clean.
