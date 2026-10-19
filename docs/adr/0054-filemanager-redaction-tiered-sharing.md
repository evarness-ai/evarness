# ADR-0054: FileManager advanced security — redaction-on-share + sensitivity-tiered reads + secret sweep

Status: Accepted (2026-06-14)
Context: FileManager agent FM3 (final FileManager phase). FM0 withheld `secret` content from the model; FM3 generalises that into a sensitivity→handling policy, adds content redaction, and a sweep to find confidential files. The broader FM3 wish-list (interactive approval round-trip, MCP file exposure, Drive write-back/sync) is intentionally scoped out — see below.

## Decision

1. **Redaction reuses the governance patterns.** `redact_text` replaces every
   credential + PII match (`regex_packs`) with `[REDACTED:<name>]` — the same
   patterns the rest of IRIS recognises. Deterministic, pure.

2. **Sensitivity-tiered share-safe reads** (`FileManagerService.read_shareable`):
   - `secret` → **withheld** (content `None`).
   - `personal` → **redacted** (PII replaced; usable but safe).
   - `internal`/`public` → returned as-is.
   The `file-read` skill's `read_file` now returns this share-safe view, so a
   tool result feeding a (possibly cloud) model never carries raw secrets and
   never carries raw PII — the practical realisation of ADR-0002's
   sensitivity→local-only policy at the file boundary.

3. **Secret sweep** (`find_sensitive` / `iris files secrets`) — walk every
   allowed root, classify each file, and report the `secret`/`personal` ones
   with their matched patterns. "Where are my confidential files?" — the
   discovery half of protecting them. Audited.

4. **CLI** — `iris files redact <path>` (share-safe content) and
   `iris files secrets` (sweep), alongside the existing read/scan/cat.

## Alternatives considered

- **Auto-vault detected secrets** (extract a file's secrets into the keyring,
  replace with `vault://` handles). Deferred — rewriting a user's file to swap
  in vault handles is invasive and easy to get wrong; detection (sweep) +
  redaction-on-read deliver the protection without mutating source files.
  Manual vaulting already exists via `iris vault`.
- **Flag personal content but return it raw** (FM0 behaviour). Replaced —
  redacting PII before it reaches the model is strictly safer and still useful.
- **Interactive agent→human R3 approval round-trip (ApprovalQueue).** Deferred —
  a substantial governance integration (async pause/resume); FM1's
  human-CLI-only destructive ops remain the safe stance until then.
- **Expose file-read / Drive over the MCP server.** Deferred deliberately —
  the file skill is opt-in and the most privacy-sensitive surface; exposing it
  over MCP by default would broaden the attack surface. Left off the MCP
  defaults.
- **Entropy/ML secret detection.** Deferred — regex packs cover known
  credential/PII shapes; good enough for the tiered gate.

## Consequences

**Positive:**
- The confidential-document story is complete: **detect** (sweep), **protect**
  (withhold secrets, redact PII before the model), all confined + audited.
- One sensitivity→handling policy, reusing the governance classifier + patterns
  IRIS already trusts.
- 6 FM3 tests (redaction, tiered withhold/redact/passthrough, recursive sweep);
  verified live.

**Negative / deferred:**
- No auto-vaulting / source-file rewriting (manual `iris vault` only).
- Destructive ops remain human-CLI-only (no agent-mediated R3 approval yet).
- File-read and Drive are not MCP-exposed (by security choice).
- Drive write-back, Drive→local sync, trash auto-purge, recursive dir ops, and
  entropy-based detection remain future work.

## FileManager — complete (FM0 → FM3)
FM0 secure read foundation → FM1 governed reversible mutations → FM2 Google
Drive (read) + iCloud Drive root → FM3 redaction + tiered sharing + secret
sweep. Deny-by-default, confined, secret-aware, audited throughout.

## Related ADRs

- ADR-0051/0052/0053 (FM0–FM2), ADR-0002 (sensitivity → local-only — realised
  here at the file boundary), ADR-0008 (R2/R3 governance), the governance
  classifier/regex packs + redaction hook (reused).
