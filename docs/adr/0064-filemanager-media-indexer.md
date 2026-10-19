# ADR-0064 — FileManager media + iCloud Drive indexer (FMX2)

- **Status:** Accepted
- **Date:** 2026-06-17
- **Phase:** FMX2 (third of the FileManager convergence phases FMX0–FMX3)
- **Builds on:** ADR-0062 (custodian + catalog), ADR-0063 (finance custody)

## Context

FMX0/FMX1 catalogue files IRIS *stores* (the `managed` tier). But the user's
real files — documents, and especially **images/photos** on **iCloud Drive** —
live in place; the user asked that FileManager know about those too ("images,
photos… should also be handled by filemanager"). The catalog already has the
columns (`location_tier`, `media_kind`); what was missing is a way to populate
in-place observations.

## Decision

Add a **root indexer** that walks the user's allowed roots (local folders and
iCloud Drive) and records each file in the catalog **without moving it**.

1. **`FileIndexer.index_roots()`** — walks `RootRegistry` roots, and per file
   records: media kind (magic bytes + the configurable extension map),
   sensitivity (content scan for text-like `document`/`data` files under 5 MB),
   password state (PDF `/Encrypt`, head+tail only via `is_pdf_encrypted_path`),
   and `media_meta` (mtime + image dimensions). Entries are tagged
   `location_tier = icloud` (under `~/Library/Mobile Documents/com~apple~CloudDocs`)
   or `os_local`, and `owner_module = "user"`.
2. **Identity = path** — in-place files are *not* content-hashed (too costly for
   large media); `file_id = "os_" + sha1(path)`, so re-indexing upserts the same
   row. `sha256` is left empty for these entries.
3. **Never purged** — observer entries get `retention_days = 0` and the central
   retention sweep only touches the `managed` tier, so the user's own files are
   never auto-deleted.
4. **Safety reused** — every path goes through `resolve_within_roots`, so the
   protected denylist (`.env`, keys, `~/.ssh`, …) is never catalogued, even
   inside a root.
5. **Image awareness without a dependency** — `filetype.image_dimensions()`
   parses PNG/GIF/JPEG headers in pure Python (no Pillow). HEIC dimensions need
   a real decoder and are deferred (the file is still catalogued as an image).
6. **Bounded** — a `max_files` cap (default 5000) that is **logged** when hit
   (no silent truncation), and per-file error isolation (one bad file never
   aborts the sweep).
7. **Automatic + manual** — `filemanager_index` heartbeat (daily 04:15, no-op
   without roots) keeps the catalog fresh; `iris files index [--root] [--max]
   [--no-scan]` is the manual trigger. New `media_meta` catalog column (added via
   a guarded `ALTER TABLE` so an existing catalog upgrades in place).

## Consequences

**Positive**
- FileManager now answers "what images / documents do I have, on iCloud vs
  local?" — `iris files catalog ls --kind image --tier icloud`.
- iCloud Drive is a first-class catalog tier; images carry dimensions.
- Zero data movement and zero new runtime dependency.

**Negative / deferred**
- In-place entries aren't content-hashed, so cross-tier dedup against managed
  copies isn't automatic (acceptable: different provenance).
- HEIC/RAW dimensions + EXIF (capture date, geo) need a decoder — deferred.
- Apple **Photos** library (the `.photoslibrary` bundle, separate from iCloud
  Drive files) is **FMX3**.
- Deletions on disk aren't yet reaped from the catalog (rows go stale until a
  future `last_seen`-based prune); re-index refreshes survivors.

## Verification
- `tests/unit/test_filemanager/test_indexer.py` (kinds/tiers, secret
  classification, encrypted-PDF flag, protected-file skip, re-index idempotency,
  max-files cap, CLI) + `test_filetype.py` (PNG/GIF/JPEG dimensions,
  `is_pdf_encrypted_path`); 118 filemanager tests pass; ruff/black/mypy clean.
- Live (isolated): indexed a sample tree → `photo.png` catalogued
  `image/os_local` with `1280x800`, a document `public`, and a file containing a
  fake AWS key correctly `secret`.
