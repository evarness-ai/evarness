# ADR-0065 — FileManager Apple Photos library indexer (FMX3)

- **Status:** Accepted
- **Date:** 2026-06-17
- **Phase:** FMX3 (last of the FileManager convergence phases FMX0–FMX3)
- **Builds on:** ADR-0062 (catalog), ADR-0064 (media indexer)

## Context

The FMX2 indexer catalogues files on disk (local + iCloud Drive), but the user's
photos/videos in the **Apple Photos app** live in a private bundle —
`~/Pictures/Photos Library.photoslibrary` — not as ordinary files. The user
explicitly wanted these handled too. Two hard problems:

- **TCC / Full Disk Access** — macOS gates the library; opening
  `database/Photos.sqlite` without permission fails with "unable to open
  database file" (confirmed live on this machine).
- **Private, versioned schema** — the asset table (`ZASSET`; older
  `ZGENERICASSET`) and its columns differ across macOS releases.

## Decision

Add a read-only Apple Photos reader + indexer that catalogues assets **in
place** as a new `photos` location tier — defensively, so neither permission
denial nor schema drift can crash IRIS.

1. **`PhotosLibrary.read_assets()`** — opens `Photos.sqlite`
   **`mode=ro&immutable=1`** (no locking; safe while Photos.app runs), detects
   the asset table and *which* of the wanted columns exist (PRAGMA), selects
   only those (absent → `NULL`), excludes trashed assets, and maps each row to a
   `PhotoAsset` (uuid, filename, media kind, dimensions, Core-Data→ISO created
   date, favorite, resolved `originals/<dir>/<file>` path + existence).
2. **TCC handled, not fatal** — an open failure raises `PhotosAccessError` with
   remediation ("grant Full Disk Access"); the CLI prints it and exits 2, the
   heartbeat returns **SKIPPED** (never floods logs).
3. **`index_photos()`** — catalogues each asset as `location_tier="photos"`,
   `owner_module="user"`, `source="apple_photos"`, `domain_type=photo|video`,
   `classification="personal"` (photos are personal data, ADR-0002),
   `retention_days=0` (never auto-purged). Identity is `photos_<uuid>` (no
   content hash, no `stat` per original); `media_meta` carries dimensions,
   capture date, favorite, uuid, and whether the original is downloaded locally.
4. **Read-only & non-destructive** — originals are never opened, copied or
   modified; only the metadata DB is read.
5. **Automatic + manual** — `filemanager_photos` heartbeat (daily 04:30, skips
   without library/access); `iris files photos status | index [--library]
   [--max]`. Bounded by `max_assets` (default 20000).

## Consequences

**Positive**
- Photos/videos are now in the catalog: `iris files catalog ls --tier photos
  --kind image`; favorites + capture dates live in `media_meta`.
- Robust to the two real-world failure modes (TCC, schema drift) by design.
- No new dependency; no data movement.

**Negative / deferred**
- Requires the user to grant Full Disk Access for the real library (by design —
  the synthetic-DB tests prove the logic without it).
- Schema is reverse-engineered; a future macOS could rename columns — mitigated
  by per-column detection, but new releases may need a tweak.
- Albums, people/faces, geo (EXIF), live-photo/edited-vs-original pairing, and
  per-asset byte size are **deferred** (metadata-only first pass).

## Verification
- `tests/unit/test_filemanager/test_photos.py` (7 tests, synthetic
  `Photos.sqlite`): reads assets, video kind + trashed exclusion, missing-column
  tolerance, `ZGENERICASSET` fallback, missing-DB → `PhotosAccessError`,
  catalogs as `photos`/`personal`, idempotent re-index. 125 filemanager tests
  pass; ruff/black/mypy clean.
- **Live**: `iris files photos status` against the real library returned the
  expected TCC remediation message (Full Disk Access not granted) — the graceful
  path, not a crash.
