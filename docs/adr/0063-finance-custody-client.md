# ADR-0063 — Finance as the first FileManager custody client (FMX1)

- **Status:** Accepted
- **Date:** 2026-06-17
- **Phase:** FMX1 (second of the FileManager convergence phases FMX0–FMX3)
- **Builds on:** ADR-0062 (FileManager custodian + catalog)
- **Supersedes:** ADR-0061's finance-local document storage + retention sweep

## Context

ADR-0062 made FileManager the custodian (it owns the bytes) but wired no
agents to it. Finance still downloaded statement PDFs to
`data/finance/statements/<inst>/<YYYY>/...`, tracked the path in
`finance_statements.file_path`, applied its own permissions, and ran its own
retention heartbeat (ADR-0061). Finance was the obvious **pilot**: it has the
richest file metadata and a working end-to-end pipeline, so migrating it proves
the custody model without a speculative rewrite of every agent.

## Decision

Make finance store and read its documents through the custodian; FileManager
owns storage, permissions, classification and retention. Finance keeps its
**domain** knowledge (a CAS is a CAS, password schemes, extraction status) but
no longer chooses paths.

1. **`Statement.file_ref`** — a new field holding the catalog `file_id`. New
   ingests set `file_ref` and leave `file_path` **None**; `file_path` is
   retained only for legacy rows. Added to the schema with a guarded
   `ALTER TABLE ADD COLUMN` migration (`_add_column_if_missing`) so existing
   `finance.db` files upgrade in place.
2. **Ingest** (`ingest_statements`) — hands the raw PDF bytes to
   `FileCustodian.store(owner_module="finance", domain_type=<document_type>,
   source=<email id>, password_status=…)` and records the returned `file_ref`.
   The old `dest.write_bytes` + `secure_*` + path-building code is gone; the
   `storage_root`/`policy` params are replaced by an injectable `custodian`.
3. **Extraction** (`extract_statement`) — resolves the physical PDF path via
   `_statement_pdf_path(statement)`: `file_ref` → `custodian.locate()`, else the
   legacy `file_path`. Unlock, text read and the deterministic table readers all
   use the resolved path, so extraction is unchanged downstream.
4. **Retention** — finance's per-agent sweep (ADR-0061) and its
   `finance_retention` heartbeat are **retired**. Finance documents now live in
   the catalog with `retention_days=365` (from `custody.yaml`'s
   `by_module.finance`), so the central `filemanager_retention` sweep owns them.
5. **Migration** (`migrate_finance_to_custody`, `iris finance documents
   migrate`) — a one-time, idempotent pass that re-stores each legacy
   `file_path` PDF through the custodian, points the row at the new `file_ref`,
   and removes the original. Dry-run supported; never aborts on one bad file.

## Why `file_ref` replaces the raw path (not alongside)

Keeping both a `file_ref` and a denormalised `file_path` invites staleness: when
the custodian purges bytes at retention, a cached `file_path` would dangle. So
custody rows carry **only** `file_ref`; the path is resolved live and returns
empty once purged (extraction then fails cleanly rather than reading a dead
path). `file_path` survives solely as a read-only legacy fallback.

## Consequences

**Positive**
- Finance documents are now first-class catalog entries: `iris files catalog ls
  --owner finance` shows type, sensitivity, password and retention; one engine
  retains them.
- Uniform at-rest handling (owner-only perms, classification, dedup) with no
  finance-specific storage code.
- Sensitivity floor applied: finance PDFs catalogued `internal` even when their
  bytes scan as `public`; encrypted statements flagged `password_status` from
  the institution scheme.

**Negative / deferred**
- `finance.storage_policy` + `iris finance documents policy|purge` remain for
  legacy/manual use but are no longer the authority; a later cleanup can remove
  them once no legacy `file_path` rows exist anywhere.
- Extraction instantiates a default `FileCustodian()` when none is injected
  (production path); fully threading a shared custodian through the finance
  pipeline is a minor later tidy.

## Verification
- `tests/unit/test_finance/test_ingest.py` (custody assertions),
  `test_custody_migration.py`, `test_extract_custody.py`; finance + filemanager
  suites = 381 tests pass; ruff/black/mypy clean.
- **Live**: `iris finance documents migrate` moved all 4 existing statement
  PDFs into custody — rows now on `file_ref` with `file_path` cleared, originals
  removed, managed copies at `0600`, catalogued `finance/document/internal`
  (password-protected ones flagged). Net worth + holdings unchanged
  post-migration (extracted data untouched).
