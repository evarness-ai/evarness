# ADR-0062 — FileManager as custodian: the File Catalog + custody API (FMX0)

- **Status:** Accepted
- **Date:** 2026-06-17
- **Phase:** FMX0 (first of the FileManager convergence phases FMX0–FMX3)
- **Supersedes (partially):** the per-agent storage model where each agent owns
  its own files on disk (e.g. ADR-0061's finance-local document storage —
  migrated to this custodian in FMX1).

## Context

FileManager (FM0–FM5) is an **access-control + governance + audit** layer: it
knows the *rules* (allowed roots, secret-aware reads/redaction, R2/R3 governed
mutations, append-only audit) but keeps **no record of what files exist**. It
acts only on files handed to it, live.

Meanwhile every agent stores files **independently**: finance downloads
statement PDFs to `data/finance/statements/...` and tracks them in `finance.db`
(with its *own* retention policy, ADR-0061); RAG, calendar (ICS), wiki and
reminders each write their own trees. There is no single authority that can
answer *"what files does IRIS hold, where do they live (OS / iCloud / Drive),
what type are they, which are finance, which are secret, which need a
password?"* The same file knowledge is duplicated per agent and inconsistent.

The user's directive: **all file handling must go through FileManager** — it
must know what each file is, where it's stored, whether it's password-protected,
its sensitivity (finance/secure vs general vs OS), and its retention. And it
must be **configurable, extensible and interoperable** (the open-source story:
users may swap in Plaid or other providers and need to tune behaviour without
code).

## Decision

Make FileManager the **custodian** of files: it owns the bytes, and every agent
stores through it. Two new pieces (this phase, no integrations yet — finance
migrates in FMX1):

### 1. File Catalog (`data/filemanager_catalog.db`)
The authoritative record — one row per file regardless of which agent put it
there: `file_id`, `sha256`, `storage_path`, `location_tier`
(`managed`/`os_local`/`icloud`/`gdrive`), `owner_module`, `media_kind`
(document/image/video/audio/archive/data/other), `domain_type` (owner-defined,
e.g. `cas`, `bank_statement`), `classification` (secret/personal/internal/
public), `password_status`, `original_filename`, `byte_size`, `retention_days`,
`source`, timestamps, `deleted_at`. Owner-only (0600). Soft-delete: purging
bytes clears `storage_path` and sets `deleted_at` but **keeps the row** — the
metadata survives the file.

### 2. Custodian API (`FileCustodian`) — the interop surface
`store(content, *, owner_module, original_filename, domain_type, source, …)
→ FileHandle`. The agent provides *content + intent*; the custodian decides
**everything physical**:
- **Path** — `data/filemanager/store/<owner>/<YYYY>/<sha12>_<name>`. Agents
  never choose paths.
- **Permissions** — owner-only file + dirs, from policy.
- **Media kind** — magic bytes first, then a configurable extension map
  (`filetype.py`).
- **Sensitivity** — content scan (the shared `DataClassifier`) raised to the
  owner's configured floor.
- **Password** — encryption sniff (PDF `/Encrypt` today; dispatch table is
  extensible).
- **Retention** — resolved from policy (`by_domain_type` > `by_module` >
  default).

Content-addressed + **idempotent**: the SHA-256 is the identity, so storing the
same bytes twice returns the same handle (re-ingestion never duplicates). The
agent keeps only the opaque `file_id`; `read(file_id)`/`locate(file_id)` give it
the bytes/path back for its own local processing.

### 3. Configurable policy (`config/filemanager/custody.yaml`)
`store_root`, `file_mode`/`dir_mode`, retention (`default_days` + `by_module` +
`by_domain_type`), per-module sensitivity floor, and the extension→media-kind
map — all data-driven. Overridable via `IRIS_FILEMANAGER_CUSTODY_CONFIG`.
**Extensible** (new kinds/rules in YAML, no code) and **interoperable** (any
agent or third-party integration stores via the one API + policy).

### 4. Central retention engine
`FileCustodian.enforce_retention()` sweeps the whole catalog and purges managed
bytes past each file's window — **one engine for all agents**, replacing
per-agent retention (finance's ADR-0061 sweep is retired in FMX1). Wired as the
daily `filemanager_retention` heartbeat (03:45) + `iris files catalog sweep`.

## Why custodian (not registry/observer)

We chose full custody over a lighter "catalog-only observer". The directive is
*single authority*, and custody is the only model where FileManager actually
**enforces** path/perms/retention/sensitivity rather than merely describing what
agents already did. The risk (rewriting a working finance pipeline) is bounded
by phasing: FMX0 ships the engine with zero integrations; FMX1 migrates finance
as the proof; other writers follow only as needed.

## Consequences

**Positive**
- One queryable authority: `iris files catalog ls/show/stats` answers "what /
  where / type / sensitivity / password / retention" across all agents.
- Files stored consistently: owner-only perms, dedup, classification, retention
  — uniformly, not re-implemented per agent.
- Configurable + extensible + interoperable by construction.

**Negative / deferred**
- Agents must migrate to the custody API (finance = FMX1; RAG/calendar/wiki/
  reminders later). Until migrated they keep their own storage.
- Owners still receive a resolved `path` from the handle (FMX0 keeps direct
  reads working); a fully opaque, handle-only read path is a later tightening.
- At-rest **encryption** is still owner-only perms + OS disk encryption — app-
  level encryption remains a future knob (carried over from ADR-0061).
- `location_tier` non-`managed` (os_local/icloud/gdrive observer entries) and
  media/photo cataloguing arrive in **FMX2**; Apple Photos library in **FMX3**.

## Verification
- `tests/unit/test_filemanager/test_filetype.py`, `test_custody_policy.py`,
  `test_catalog.py`, `test_custodian.py`, `test_catalog_cli.py` — 102
  filemanager tests pass; ruff/black/mypy clean.
- Live: a finance PDF stored via the custodian → `media_kind=document`,
  `classification=internal` (finance floor), perms `0600`, `retention=365`
  (shipped config), catalogued + readable back; encrypted-PDF sample flagged
  `password_status=protected`.
