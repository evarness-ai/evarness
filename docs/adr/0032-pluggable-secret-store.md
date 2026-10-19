# ADR-0032: Pluggable secret store — keyring default, encrypted file vault optional

Status: Accepted (2026-06-13)
Context: Finance build phase F2. Amends ADR-0003 (Keychain credentials over a custom vault). Companion: ADR-0011 (keychain module shape), ADR-0031 (finance data layer).

## Context

ADR-0003 stored OAuth tokens in the OS keychain via the `keyring`
library and explicitly rejected a custom vault. That call was right for
tokens, and `keyring` is already cross-platform (macOS Keychain, Windows
Credential Manager, Linux Secret Service) — so "OS keychain" was never
macOS-only.

F2 needs to store a few more secrets — a PAN, a date of birth, and
per-institution statement-PDF passwords — to unlock CAS/bank PDFs during
extraction. Two pressures surfaced:

1. **Headless / server / Docker / WSL / CI** contexts have no desktop
   secret service; `keyring` errors or silently falls back to an insecure
   backend there. IRIS is meant to be self-hostable and open-sourceable,
   so a keychain-only story is too narrow.
2. The user asked specifically whether secrets could live in an encrypted
   local vault for portability across Windows and macOS.

`credentials.py` already anticipated this: its docstring says connector
skills never touch `keyring` directly "so the storage backend stays
swappable." This ADR makes that swap real.

## Decision

A small **`SecretStore` protocol** (`get`/`set`/`delete`, namespaced) with
two backends behind one factory (`get_secret_store`), selected by
`IRIS_SECRET_BACKEND` (default `keyring`):

1. **`KeyringSecretStore` (default).** Native OS keychain via the existing
   `identity.credentials` helpers — zero-config on macOS/Windows/Linux
   desktops. Finance secrets sit alongside OAuth tokens under the same
   `iris-<namespace>` service scheme.
2. **`FernetVaultStore` (opt-in).** An AES-128 (Fernet) encrypted SQLite
   file at `data/secrets.db` (gitignored). Portable — one file — and works
   headless. Values are encrypted before touching disk; namespace/key are
   stored in clear (lookup coordinates, not secrets).

**Master-key derivation for the vault** (the chicken-and-egg of "where
does the key live"):
- `IRIS_VAULT_PASSPHRASE` → scrypt-derived key (n=2¹⁴). Fully portable, no
  OS dependency — unlock with a passphrase like a password manager.
- else → a random Fernet key auto-created and kept in the OS keychain, so
  only *one small key* depends on `keyring` and all the actual secrets
  stay in the portable file.

`cryptography` (Fernet/scrypt) was already a dependency; no new deps.

**Amendment to ADR-0003:** "no custom vault" stands as the *default* for
tokens, but is narrowed to "keyring is the default backend; an
encrypted-file vault is an available backend for finance secrets and
headless deployments." Same spirit (don't roll bespoke crypto for the
common desktop case), broader reach.

## Alternatives considered

1. **Keychain only.** Simplest, but excludes headless/server/open-source
   self-hosters — the exact users IRIS courts.
2. **Vault only (drop keyring).** Portable, but loses the zero-config
   native-keychain experience and forces a passphrase prompt on every
   desktop user.
3. **Per-vault random salt for the passphrase KDF.** Marginally stronger,
   but the salt must be readable before the vault opens (chicken-and-egg);
   a constant app salt + strong passphrase + scrypt work factor is the
   standard pragmatic choice. Documented in-code so it isn't mistaken for
   an oversight.
4. **Plaintext on disk with chmod 700.** Rejected — statement passwords
   and a PAN are exactly the secrets that must be encrypted at rest.

## Consequences

**Positive:**
- One call site (`get_secret_store`); finance never imports a backend.
- Desktop users: nothing to configure. Headless/portable/open-source
  users: `IRIS_SECRET_BACKEND=vault` + a passphrase.
- Encrypted-at-rest finance secrets; verified by a test asserting the
  plaintext never appears in the vault file.

**Negative / deferred:**
- The vault's passphrase UX (prompt, cache for a session) isn't built yet;
  F2 reads `IRIS_VAULT_PASSPHRASE` from env. A CLI unlock flow can come
  later.
- No secret rotation tooling yet.

**Reversibility:** high — the protocol means new backends (age, libsodium,
cloud KMS) are additive.

## Related ADRs

- ADR-0003 / ADR-0011 — keychain credentials (amended here).
- ADR-0031 (finance data layer), ADR-0033 (statement extraction, the first
  consumer of finance secrets).
