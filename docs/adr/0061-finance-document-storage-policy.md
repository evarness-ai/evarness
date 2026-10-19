# ADR-0061: Finance document storage & retention policy

Status: Accepted (2026-06-17)

**Status pass (2026-10-18):** the code this record describes is in the private domain tree, not in this repository. retention moved to `filemanager_retention` under ADR-0063 (`config/heartbeats.yaml` says `finance_retention` was retired)

Context: Downloaded statement PDFs and the extracted `finance.db` are high-sensitivity. Today they're gitignored (never committed) and bank-password-protected PDFs stay encrypted at rest, but protection is otherwise inherited from the source PDF + the OS: files are written world-readable (`0644`), the extracted DB is plaintext, unprotected source PDFs (e.g. a margin report) sit in the clear, and there is no retention. This ADR makes at-rest handling **policy-driven and configurable** in the finance agent.

## Context

`iris_harness.finance.ingest` writes raw attachment bytes to
`data/finance/statements/<institution>/<YYYY>/<sha12>_<file>` and parses them
into `data/finance.db`. Audit of the on-disk state:

- ✅ `data/finance/` and `data/*.db` are gitignored; secrets live in the OS
  keychain / an optional Fernet vault (ADR-0032). Extraction is Tier-3 **local
  only** (ADR-0002/0006) — documents never leave the machine.
- ⚠️ Files are `0644` (readable by other local users); the **extracted DB is
  plaintext**; an **unencrypted source PDF stays in the clear**; documents are
  **kept forever**.

The weak points are local at-rest exposure and unbounded retention. The user
wants these governed by a **configurable policy**, with **1-year doc retention**.

## Decision

A single declarative policy, `config/finance/storage_policy.yaml`, loaded into a
`StoragePolicy` and enforced deterministically:

```yaml
storage:
  file_mode: "0600"            # owner-only on statement files + finance.db
  dir_mode:  "0700"            # owner-only on the statements directories
  retention_days: 365          # delete raw statement PDFs this long after download (0 = keep forever)
  purge_after_extraction: false  # if true, delete the raw PDF once extraction completes (keep only the DB)
  db_encryption: "none"        # future knob: "sqlcipher" (see Consequences)
```

1. **Owner-only permissions (enforced).** On write, statement files get
   `file_mode` and their directories `dir_mode`; `FinanceStore.ensure_schema`
   restricts `finance.db` to owner-only. Closes the `0644` local-read gap.

2. **Retention (enforced, default 1 year).** `enforce_retention(store, policy,
   now)` deletes the **raw PDF** for any statement older than `retention_days`
   (by `created_at` = download time), and — if `purge_after_extraction` — for any
   statement whose extraction is `completed`. The **Statement row and the
   extracted data are kept**; only the raw document is removed and its
   `file_path` cleared (so the system knows the source is gone). Runs daily via a
   `finance_retention` heartbeat and on demand via `iris finance documents purge`.

3. **`purge_after_extraction` defaults OFF.** Keeping the raw PDF for the
   retention window preserves **re-extraction** (parser improvements re-run
   against the original) — something this build relied on repeatedly. Users who
   want minimal residency can switch it on.

4. **Encryption at rest = OS disk encryption + owner-only perms, today.**
   App-level crypto (SQLCipher for the DB, Fernet for PDFs, reusing the ADR-0032
   vault key) is a **declared but not-yet-enforced** policy knob (`db_encryption`)
   — called out so the policy surface is honest about what is and isn't enforced.

## Consequences

- At-rest exposure drops from "world-readable, forever, plaintext DB" to
  "owner-only, 1-year retention, raw docs purgeable" — configurable per install.
- Re-extraction stays possible within the retention window (default on).
- **Not yet enforced:** DB/PDF app-level encryption. `db_encryption: "sqlcipher"`
  is reserved; until implemented, the DB relies on OS disk encryption (FileVault
  etc.) + `0600`. This is the main residual and the natural next ADR.
- Open-source friendly: a self-host can tighten (`purge_after_extraction: true`,
  shorter retention) or loosen (`retention_days: 0`) without code changes.
