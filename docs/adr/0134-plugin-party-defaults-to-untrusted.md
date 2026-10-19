# ADR-0134: `PluginManifest.party` defaults to `untrusted`

Status: Accepted (2026-10-05); decisions 3, 4 and 6 amended 2026-10-06 (iris-harness #125)
Amends: ADR-0133 (the default value only; the field, its value set and its independence
from `trust` stand)
Relates to: iris-harness issue #97 and PRs #123 (the default) and #125 (the notice, the
explicit examples and scaffolds), `runtime/plugin_host/manifest.py`,
`runtime/plugin_host/loader.py`, `sdk/stable_tier.yaml`,
`docs/architecture/plugin-contract.md`

## Context

ADR-0133 added `party` (`first-party` | `trusted-third-party` | `untrusted`) with a
default of `first-party`, chosen so no bundled manifest needed a change. The default is
the wrong way round. A third-party plugin that omits the field reads as first-party, so
the day something enforces `party` (refuse or warn on an in-process `untrusted` plugin),
the plugins that most need the check pass it without declaring anything. A safety field
whose default is the safe-looking value fails open.

Nothing reads `party` to make a decision today. This ADR changes what an undeclared
manifest says; it changes no behavior.

## Decision

1. `party` defaults to `untrusted`. The allowed values and the field's place in the
   manifest (and in `stable_tier.yaml`) are unchanged.
2. A plugin this project ships declares `party: first-party` explicitly: the seven
   `plugins_builtin/*` and the three `iris_personal/plugins/*` manifests.
3. Examples and the `iris plugin new` scaffolds are written for outside authors, so they
   do not claim `first-party`. They declare `party: untrusted` explicitly, with a short
   comment, so copying one teaches the field and a fresh scaffold mounts without the
   notice in decision 6. (Originally they inherited the default silently; amended by the
   owner on 2026-10-06 and implemented in #125.)
4. `party` is surfaced wherever `trust` is: the plugin inventory (`GET /plugins`,
   `/plugins/{name}`), `--dump-config` (text and JSON), `iris plugins` and
   `iris plugins show`, the registry snapshot and the web Plugins screens. A plugin with
   no manifest shows no party.
5. **Nothing enforces `party` yet.** No mount, load or governance decision reads it.
   Enforcement is later work and gets its own ADR.
6. The loader says so when it falls back to the default: one WARNING per mount when a
   manifest does not declare `party`, naming the plugin and the fix. That includes the
   manifest synthesised for an entry-point plugin that ships none, the usual third-party
   case. A plugin supplied from code (`testing.plugin()`, `add_in_process`) is the
   caller's own and gets no notice, and discovery-only `--dump-config` never mounts. The
   notice is not enforcement: the plugin still mounts. (Added 2026-10-06, #125.)

## Alternatives considered

- **Require the field (no default).** Rejected. It breaks every existing third-party
  manifest at load time for a field nothing enforces yet, and the `entry_point` path
  deliberately loads a plugin that ships no manifest at all ("name + entry point is the
  minimum contract"), so there would be no manifest to require it in. A required field
  also makes authors guess a value to get past the error, which is worse than the
  fail-closed default saying the honest thing. A default of `untrusted` gives the same
  safety property without a breaking change.
- **Keep `first-party` and rely on enforcement to be strict later.** Rejected: that is
  the fail-open this ADR removes.

## Consequences

- A manifest, or a synthesized one (an entry-point plugin with no manifest, an
  in-process plugin registered by name), now reports `untrusted`. Display only, plus the
  mount-time warning in decision 6 for a manifest that omits the field.
- Anyone copying a bundled manifest must keep its `party: first-party` line on purpose;
  copying an example or scaffold carries an explicit `party: untrusted` until they or
  their operator say otherwise.
- The warning is per mount, so a plugin that omits `party` logs it on every start.
  Declaring the field is the fix; there is no way to silence it otherwise.
- Reversible: one default, one line per bundled manifest.
