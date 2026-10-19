# ADR-0053: FileManager cloud — Google Drive (read-only) + iCloud Drive as a local root

Status: Accepted (2026-06-14)
Context: FileManager agent FM2. Adds the cloud-drive management the user asked for, on top of FM0 (read) + FM1 (governed mutations). Two very different "clouds": Google Drive (a real API) and Apple iCloud Drive (no third-party API).

## Decision

1. **Google Drive via the proven Google connector pattern.**
   `iris_harness.identity.gdrive_oauth` mirrors `gcalendar_oauth`/`gmail_oauth`
   one-for-one: installed-app OAuth, tokens in Keychain
   (`KEYRING_PROVIDER="gdrive"`), accounts in the ADR-0016 `email_accounts`
   registry (`provider="gdrive"`), authorized address resolved from Drive's
   `about().get(fields="user")`. `iris auth gdrive login|status|logout`.

2. **Read-only scope (`drive.readonly`).** FM2 only *reads* Drive. Write-back
   to Drive is consequential and separately-scoped; it's deferred. (Same
   read-mostly stance the calendar connector took.)

3. **`GDriveService` — list / search / read by file-id.** Remote files have
   IDs, not local paths, so the FM0 path-jail doesn't apply; instead Drive
   reads get the *other* FM protections: content **secret-classification**
   (secrets withheld, ADR-0002) and **audit** logging. Native Google Docs are
   exported to `text/plain` for reading; binary files use `get_media`. Pure
   dict→`DriveFile` mapping is separated from I/O so it's unit-testable with a
   fake service (no network), like the calendar Google provider.

4. **iCloud Drive is a local root, not an API.** Apple publishes no
   third-party iCloud Drive API; on macOS it's a synced folder at
   `~/Library/Mobile Documents/com~apple~CloudDocs`. "Managing iCloud Drive" =
   adding that directory as a FileManager root, so it's fully covered by the
   FM0/FM1 local path-confined, governed surface. `iris files add-icloud` is a
   convenience for it.

5. **CLI-only for FM2.** Drive access is `iris files gdrive ls|find|cat`
   (secrets withheld on `cat`); it's **not** exposed as an agent/MCP tool yet.
   A credentialed cloud read reaching the model is better gated with the
   sensitivity-tier work (FM3) than shipped here.

## Alternatives considered

- **A separate gdrive credential store / account model.** Rejected — reuse the
  `email_accounts` registry + Keychain, exactly like Gmail/Calendar; the
  `provider` column already discriminates.
- **Mirror/sync Drive into a local folder, then treat it as a root.** Deferred
  — heavy (download, dedupe, conflict handling); FM2 reads Drive directly by
  id. A sync mode can come later.
- **Request `drive` (read-write) scope now.** Rejected — least privilege; FM2
  reads. Write needs its own governance + scope upgrade.
- **An iCloud Drive "connector".** Rejected as a category error — there's no
  API; it's a local folder. Adding it as a root is the correct model and reuses
  all the FM0/FM1 safety.
- **Expose Drive as an agent/MCP tool now.** Deferred to FM3 — cloud content to
  an LLM deserves the sensitivity-tier routing FM3 adds.

## Consequences

**Positive:**
- Both clouds the user named are managed: Google Drive (read, cited by id,
  secret-aware, audited) and iCloud Drive (a first-class local root, governed
  reads + mutations for free).
- One Google connector mental model (OAuth/Keychain/registry identical to
  Gmail/Calendar); least-privilege read-only scope.
- Drive reads carry the FM secret protection (withheld from the model) and
  audit trail. 8 offline tests via a fake Drive service.

**Negative / deferred:**
- Read-only Drive (no write-back); single-account default in the CLI
  (`--account` to pick).
- Drive not yet an agent/MCP tool (CLI-only) — lands with FM3 sensitivity tiers.
- No Drive→local sync mode; reads are live per-call.
- iCloud is macOS-only (the path doesn't exist elsewhere); `add-icloud`
  reports that cleanly.

## Related ADRs

- ADR-0003 (Keychain creds), ADR-0016 (`email_accounts` registry), ADR-0041
  (gcalendar connector — the pattern mirrored), ADR-0051/0052 (FM0/FM1 — iCloud
  reuses them as a local root), ADR-0002 (local-only — Drive secrets withheld).
