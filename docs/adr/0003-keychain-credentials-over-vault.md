# ADR-0003: OAuth tokens in the macOS Keychain via `keyring`; no custom vault

Status: Accepted (2026-05-24)

**Status pass (2026-10-18):** the decision holds; the code now lives in `kernel/governance/vault/credentials.py`; amended by ADR-0032, which adds an optional Fernet backend behind EVARNESS_SECRET_BACKEND with keyring still the default

Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §4.3.

## Context

The upgrade adds OAuth-protected connectors (Gmail, Google Calendar,
later Outlook/iCloud). Each connector needs to store access tokens,
refresh tokens, and OAuth client configuration. The user explicitly
asked to "keep it simple — no refresh-token-in-vault."

Paia drifted toward a Governor-Credential-Broker pattern with vault
backends. Useful for multi-tenant or shared-host deployments; overkill
for a single-user Mac.

The naive simple option — plain JSON files at
`~/.iris/workspace/credentials/<provider>.json` with chmod 600 — was the
initial proposal and was rejected by the user (skeptical of plain-text
secrets on disk even with file-mode protection).

## Decision

Store OAuth **tokens** in the macOS Keychain via the `keyring` Python
library. Store **non-secret OAuth config** (client_id, scopes,
last-auth timestamp, account_email) in
`~/.iris/workspace/credentials/<provider>.config.json`, with the
directory chmod 700 as defense in depth.

```
keyring entries (macOS Keychain):
  service="iris-gmail"     account="<address>"  password=<access_token>
  service="iris-gmail"     account="<address>"  password=<refresh_token>
  service="iris-gcalendar" account="<address>"  password=<refresh_token>
```

One shared module `src/iris_harness/identity/credentials.py` wraps `keyring`.
Skills never touch Keychain directly.

IRIS is configured as a **LaunchAgent** (per-user) on boot, not a
LaunchDaemon, so the login Keychain is unlocked.

## Alternatives considered

1. **Plain JSON files chmod 600.** Rejected by user; secrets live as
   plaintext on disk, vulnerable to backup/sync accidents.
2. **Governor credential broker / vault.** The paia drift. Multi-tenant
   machinery for a single-user use case. Rejected as over-engineering.
3. **Encrypted file with app-managed key.** Adds key-management
   problem. Keychain already handles AES at rest; redundant.
4. **Environment variables.** Re-auth via dotfile edits is hostile UX
   for tokens that refresh hourly.
5. **Touch ID gate on every read.** Possible (`pyobjc` LocalAuthentication),
   but creates prompt friction for every background routine. Defer.

## Consequences

**Positive:**
- Tokens never touch the filesystem. Backup tools, `tar`, Time Machine,
  Dropbox sync cannot accidentally exfiltrate them.
- First Keychain access prompts user for "Always Allow"; subsequent
  reads are silent. The prompt itself is the security feature.
- ~25-line credentials module vs ~80 lines of defensive file-mode
  validation.
- `keyring` is cross-platform — Linux Secret Service, Windows Credential
  Manager — so contributor portability is preserved.

**Negative:**
- One new runtime dependency (`keyring>=24`, Apache-2.0, mature).
- Python interpreter path changes (e.g., `poetry update`, `brew upgrade
  python`) can invalidate the Keychain ACL → user re-prompts "Always
  Allow" once. Documented but acceptable.
- Headless Linux servers without a desktop session fall back to
  `keyring`'s plaintext backend; this is an explicit downgrade and
  logged. Personal Mac is the target environment; out-of-target
  deployments are not supported by the design.
- Single-account model per provider (one credential bundle per
  `(provider, address)`). Multi-account is supported via multiple
  Keychain entries keyed by address.

**Reversibility:** high. The shared `credentials.py` module is the only
abstraction. Swapping the backend (e.g., to a vault broker for
multi-user) replaces one module's implementation without touching skill
code.

## Future-state note

If IRIS ever goes multi-user (web app, household deployment), the
file-/Keychain-based store has to be replaced with a vault/broker. That
is a real refactor, not a swap. This ADR is correct for *personal use
on one Mac* and is explicitly not the production-grade story.
