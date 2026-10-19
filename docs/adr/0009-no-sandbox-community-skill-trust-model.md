# ADR-0009: No sandboxing for community skills — explicit trust model + governance enforcement

Status: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §4.4.

## Context

The upgrade commits IRIS to an open-source distribution story:
community-authored skill packs that users install from GitHub. The
question is how IRIS treats untrusted skill code at install time and
at runtime.

`src/iris_harness/sandbox/` exists in the codebase for the coding agent's
tool-execution boundary — it sandboxes commands the LLM proposes to
run, not arbitrary Python modules. Sandboxing a Python module that
imports from `iris.*` would mean rebuilding the runtime; not realistic.

Plain refusal to run community code defeats the open-source story.

## Decision

**Community skills run in-process with the same privileges as IRIS
itself.** No process isolation, no Python import restrictions, no
filesystem jail.

Trust is established through **explicit user consent at install time**
and **enforced through governance at runtime**, not through code
isolation. Specifically:

1. **At install time** (`iris skill add <git-url>`):
   - Clone the skill into `~/.iris/community-skills/<name>/`.
   - Validate `iris_compatibility` against the running IRIS version.
   - Display a **capability diff**: which governor routes, which
     credentials, which OAuth scopes the skill declares. Pretty-printed.
   - **Require explicit user confirmation** before symlinking into
     `config/skills/<name>/`.
   - Skill's Python dependencies are **warned but not auto-installed**;
     user runs `pip install` knowingly.

2. **At runtime:**
   - The governance kernel enforces route policies *external* to skill
     code. A malicious skill cannot bypass approval gates because the
     gates are not in the skill.
   - `iris skill audit <name>` lists which routes the skill invoked in
     the last N days.
   - `iris skill disable <name>` immediately stops loading the skill.

3. **Documentation honesty:** the contributor guide states plainly:
   *"Community skills are arbitrary Python code. Review the source before
   installing. The governance kernel limits what they can do without
   your consent — but it cannot stop them from reading your filesystem
   or running arbitrary computation. Install only from trusted sources."*

Trust attribute in manifest:

```yaml
trust_level: community | first-party
```

Drives install-time prompt severity, not runtime enforcement (runtime is
the same for both).

## Alternatives considered

1. **Subprocess isolation per skill.** Adds IPC overhead per tool call,
   complicates state sharing (DB connections, event bus, harness
   reference). Rejected: cost dominates benefit at personal scale.
2. **Restricted Python (RestrictedPython, ast inspection, import
   firewalls).** Brittle, frequently bypassable, hostile to
   contributors. Rejected.
3. **Container sandbox (Docker/Firejail/AppArmor).** Real isolation,
   real complexity, kills the macOS-native UX. Rejected for MVP.
4. **No community skills at all; first-party only.** Defeats the
   open-source mission. Rejected.
5. **Central skill registry with code review gate.** User becomes the
   curator/maintainer/security-reviewer. Rejected (Q19).

## Consequences

**Positive:**
- The install experience is honest: users know what they're consenting
  to.
- Skills can use any Python library, any IRIS internal, any system
  capability — same as a first-party skill. No second-class citizens.
- Implementation cost is small (~150 LOC for `iris skill add` + diff
  renderer + audit CLI). No runtime overhead.
- Matches established norms for the `pipx`/`npm`/`vim-plugin` class of
  ecosystem.

**Negative:**
- A malicious or buggy skill *can* read the user's filesystem, exfiltrate
  data, etc. Mitigation is install-time diligence, not runtime
  enforcement. Documented in contributor guide.
- A future security incident may force a partial sandbox (e.g., per-skill
  filesystem path allowlist). Premature work to do that now would be
  wasted; deferring is correct.
- IRIS cannot offer the "browse and one-click install" experience some
  ecosystems have. The git-clone + capability-diff flow is deliberately
  slower.

**Reversibility:** moderate. Adding subprocess isolation later is
feasible (each skill becomes a worker process with an RPC interface)
but a real refactor. The decision to ship without sandboxing assumes
the personal-Mac single-user threat model continues to hold.

## Related ADRs

- ADR-0003 (Keychain credentials) — credentials are not available to
  skill code directly; the shared `credentials.py` module is the
  access point, audit-logged.
- ADR-0008 (R2/R3 calendar split) — the runtime governance model that
  makes "auto-with-undo" safe even when called by community code.
