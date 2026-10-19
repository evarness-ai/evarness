# ADR-0011: Keychain credentials module — implementation shape

Status: Accepted (2026-05-25)
Context: Personal-assistant upgrade Phase 0 track 3. Companion: ADR-0003 (parent design); `personal-assistant-upgrade.md` §4.3.

## Context

ADR-0003 established **what** to do for personal-assistant OAuth
credentials: store tokens in the macOS Keychain via the ``keyring``
library, non-secret config in plaintext JSON under
``~/.iris/workspace/credentials/``, single shared entry-point module
so connector skills never touch ``keyring`` directly. The shape of the
API and a handful of pragmatic implementation choices were left to the
implementation phase.

Phase 0 track 3 implemented the module
(``src/iris_harness/identity/credentials.py``, commit ``3e2497a``). Five
implementation decisions were resolved during that work that aren't
in ADR-0003 and would otherwise be invisible to future readers: this
ADR records them.

## Decision

Five implementation decisions:

1. **String-based token API** (not a typed ``TokenBundle`` Pydantic model).
   ``save_token(provider, account, value: str)`` accepts any opaque
   string; ``load_token`` returns ``str | None``. Callers (gmail-inbox,
   gcal-events, etc.) decide whether the value is a raw access token,
   a JSON-encoded ``{access, refresh, expires_at}`` blob, or anything
   else. A typed ``TokenBundle`` may be added later as an additive
   convenience if multiple connectors converge on a shared shape, but
   the credentials module itself stays string-typed.

2. **No master-key / file fallback when keyring is unavailable.**
   On hosts without a keyring backend (headless Linux without DBus,
   for example), operations raise the underlying ``keyring`` exception.
   Caller's responsibility to surface that to the user. This is the
   opposite stance from ``src/iris_harness/governance/vault/store.py`` (which
   does have a master-key fallback for its different use case);
   ADR-0003 explicitly rejected the vault pattern for the
   personal-assistant scope, and adding a fallback would re-introduce
   it.

3. **``list_providers()`` is filesystem-only.** It enumerates
   ``*.config.json`` files in the credentials directory and returns
   provider names. It does NOT enumerate keychain entries (``keyring``
   has no portable "list all" API across backends). Config-file
   presence is therefore the source of truth for "this provider is
   configured." If a token is missing for a provider whose config
   exists, that's a re-auth path the caller handles.

4. **``chmod 700`` only on POSIX.** Windows uses ACLs rather than POSIX
   modes; the ``os.chmod`` call would silently no-op or raise depending
   on the Python build. ``os.name == "posix"`` gates the call so
   cross-platform behaviour stays predictable. Windows users get
   default-NTFS permissions, which on a single-user account already
   restrict access to the user's home directory; defence in depth is
   weaker but not absent.

5. **Two-store architecture: keychain for tokens, JSON for non-secret
   config.** The two stores are kept structurally separate (different
   API calls, different backends) rather than collapsing config into a
   single keychain blob. Rationale: non-secret config benefits from
   being plain-text inspectable (``cat
   ~/.iris/workspace/credentials/gmail.config.json``) for debugging,
   while secrets must stay in the keychain. Mixing them would force
   either secrets-in-file or non-secrets-in-keychain — both worse
   defaults than the split.

## Alternatives considered

1. **Typed ``TokenBundle`` Pydantic model in the credentials module.**
   Stronger types and clearer caller contract; downside is every
   caller must use the same shape even when the connector's OAuth
   library prefers something different (e.g., google-auth's
   ``Credentials`` object). Rejected for MVP; revisit if a real
   convergence emerges.

2. **Master-key fallback to encrypted file (vault-pattern).** Would
   make headless deployments work without keyring. Rejected: violates
   ADR-0003's explicit non-goal for the personal-assistant scope;
   "single Mac" is the target. Headless deployments are a future
   concern.

3. **``list_providers()`` enumerating both filesystem AND keychain.**
   Could detect "ghost" providers (token in keychain but no config
   file). Rejected: ``keyring`` has no portable list API; would
   require backend-specific code. Single source of truth (filesystem)
   is simpler and ghost-token cleanup can be a CLI command later
   (``iris auth clean-orphans``).

4. **Encrypting the config JSON files at rest.** Adds key-management
   complexity for marginal gain — config files contain no secrets by
   design. ``chmod 700`` on the directory + FileVault on the disk
   covers the realistic threat. Rejected.

5. **Storing OAuth client_id + client_secret in keychain.** Tempting
   because client_secret is *technically* a secret. Rejected: Google
   docs explicitly state that for installed/desktop apps the
   client_secret is "best-effort" since it's distributed in client
   binaries; treating it as a secret would create false security
   theatre. Keep both client_id and client_secret in config JSON for
   simplicity; rotate via the Google Cloud Console if compromised.

## Consequences

**Positive:**
- Module is ~105 LOC. Connector skills (gmail-inbox, gcal-events) get
  a four-function API that's trivial to wrap.
- Filesystem layout is human-debuggable: cat the config JSON to see
  which providers are connected, when they were authorised, what
  scopes were granted.
- Storage backend is swappable: the four function signatures stay
  stable even if we later route through a different keychain library
  or add fallback paths.

**Negative:**
- Headless servers without keyring are unsupported. Operations will
  fail loudly rather than degrade gracefully. Acceptable for the
  single-Mac target; flagged for future revisit if/when IRIS goes
  multi-user.
- The string-based API gives no compile-time check that callers
  store/retrieve compatible shapes. A first-time bug ("save_token
  stored JSON, load_token treated as raw") is possible. Mitigated
  by per-connector documentation and integration tests.
- Two-store architecture means callers must remember which API to
  use for which fact (token vs config). Convention: anything that's
  a "secret" rotates via ``save_token``; everything else via
  ``save_config``.

**Reversibility:** high. The four-function API is small enough that
swapping implementations (e.g., introducing a ``TokenBundle`` model,
adding a master-key fallback, merging the two stores) is mechanical
work bounded to this module plus its caller sites.

## Related ADRs

- ADR-0003 (parent) established the keychain-over-vault stance.
- A future ADR-0009-style implementation ADR for the
  ``iris auth gmail`` CLI flow (Phase 7) will build on this module.
- ``governance/vault/store.py`` continues to handle the
  vault-master-key concern separately; see ADR for the original vault
  design.
