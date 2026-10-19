# ADR-0051: FileManager secure foundation — deny-by-default confinement, secret-aware reads, audit

Status: Accepted (2026-06-14)
Context: FileManager agent FM0 (6th PAIA agent), declared by the user as the highest-priority, most-security-critical agent — it touches real files including confidential documents and (later) cloud drives. This ADR sets the security model the whole agent is built on.

## Decision

1. **Deny-by-default access.** The FileManager can reach **nothing** until the
   user explicitly registers a root (`iris files add-root <path>`). No roots →
   every operation is denied. Roots are persisted (`data/filemanager.json`) as
   absolute resolved paths; a protected/secret location can never be a root.

2. **Realpath-jail confinement (resolved-to-resolved).** Every path is fully
   resolved (following all symlinks) and must land inside a resolved root. This
   catches **path traversal** (`../` lands outside) and **symlink escapes** (a
   symlink whose target leaves the root resolves outside) in one check, and is
   robust on macOS where `/var`,`/tmp` are symlinks to `/private/*` (an earlier
   lexical-vs-resolved comparison falsely denied legitimate paths there — fixed).

3. **Protected-path denylist, enforced even inside an allowed root.**
   `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, IRIS's own `~/.iris`/config/data,
   and secret filenames/extensions (`.env`, `.netrc`, `id_rsa`, `*.pem`,
   `*.key`, `*.kdbx`, `vault.db`, …) are refused wherever they appear. So
   pointing the FileManager at `$HOME` never exposes the user's keys.

4. **Secret-aware reads; confidential content stays local.** Reads classify
   content with the governance credential/PII regex packs (`DataClassifier`):
   `secret` / `personal` / `internal` / `public`. The local service returns
   content (it's the user's own machine), but the **skill `read_file` tool —
   which may feed a cloud model — never returns `secret`-classified content**
   (ADR-0002), and the CLI `cat` withholds it without `--show-secrets`.

5. **Everything is audited.** An append-only `file_ops` log records every
   read/list/search/stat and every **denial** (op, path, result, classification),
   giving a tamper-evident trail over the most sensitive surface.

6. **Opt-in, read-only first.** The `file-read` skill is `default_enabled:
   false` — file access is the most privacy-sensitive capability, so it's off
   until the user turns it on. FM0 ships only safe reads; mutations are gated
   in FM1.

## Alternatives considered

- **Allow the whole home dir by default.** Rejected — far too broad for the
  most sensitive agent; deny-by-default + explicit roots is the safe posture.
- **Lexical + resolved dual confinement (FSJail's technique).** Adapted, then
  simplified to resolved-only after it broke on macOS `/var`→`/private/var`;
  resolved-to-resolved is the correct realpath jail and loses no safety (a
  symlink/traversal escape still resolves outside the roots).
- **Reuse the FSJail governance hook directly.** Rejected — it's coupled to
  workspace-root/persona machinery for code execution; the FileManager needs a
  standalone, user-root-scoped confinement primitive. We reused its *technique*
  (symlink-resolved confinement) not its hook.
- **Let the skill return secret content and rely on redaction downstream.**
  Rejected — withholding at the read boundary is simpler and strictly safer;
  confidential bytes never enter an LLM payload.
- **Build a new secret detector.** Rejected — reuse the governance regex packs
  / `DataClassifier` so files classify exactly like the rest of IRIS.

## Consequences

**Positive:**
- A genuinely safe file surface: nothing reachable without explicit consent,
  no escaping a root, secrets never leave the box, full audit trail.
- Built on proven primitives (governance classifier, ADR-0002 local-only).
- 26 tests incl. traversal, symlink-escape, protected denylist, deny-by-default,
  and the secret-withholding skill gate; macOS realpath behaviour verified live.

**Negative / deferred:**
- Read-only — create/move/delete (governed R2/R3 + undo) are FM1.
- Secret detection is regex-pack-grade (no entropy/ML); good for known
  credential/PII shapes, not exhaustive.
- Google Drive (FM2) and advanced secret handling — vaulting, redaction-on-
  share, sensitivity-tiered cloud routing (FM3) — are later phases. iCloud
  Drive is already covered as a local root.

## Related ADRs

- ADR-0002 (privacy-aware local-only — secrets never to cloud), ADR-0008 (R2/R3
  governance — applied to FM1 mutations), ADR-0003 (Keychain — FM2 gdrive
  OAuth), the FSJail plugin (confinement technique reused). PRD-03 §3.2
  (FileManager as a PAIA agent).
