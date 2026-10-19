# ADR-0129: HarnessServices field freeze + a pinning test

Status: Accepted (2026-10-05)
Relates to: ADR-0130 (capability catalogue stays closed for 0.x), `core-boundary.md`,
`docs/oss-plan-review.md` (external architecture review, 2026-10-05), the L4.5 Harness
Hardening milestone.

## Context

`HarnessServices` (`src/iris_harness/runtime/harness_services.py`) is the typed object
a plugin receives at mount time. It has grown field by field, each addition locally
reasonable: `events`, `submit_activity`, `react_handler`, `react_stream_handler`,
`current_query`, `current_session_id`, `deliver_in_chat`, `embed`, `lessons`,
`continuations`, `classify_intent`, `skill_registry`, `conversation_in_flight`,
`default_channel`, `heartbeat_diagnostics`, `tools` — sixteen heterogeneous handles as
of this ADR.

Collectively this is a service locator: a plugin that wants one thing ends up holding
a reference to half of the runtime. That weakens the plugin boundary this project
otherwise protects carefully (caller-identity stamping, the governed tool runner, the
no-privileged-import contracts in `src/CLAUDE.md`). Every new field makes the next one
look free, and there is no mechanical check today that stops a careless addition.

The target shape — a narrow `PluginAPI` surface (`tools`, `capabilities`, `events`,
`jobs`, `inference`, `context`) that most plugin interaction should flow through — is
not something to design blind. `docs/architecture/OSS-PLUGIN-HARNESS-PLAN.md` already
describes the SDK/runtime split (`sdk/` is the author-facing half, `runtime/plugin_host/`
the host side); this ADR does not redesign that split. It stops the bleeding while a
real external plugin (the Weather plugin, L4.5 milestone) produces evidence about what
a `PluginAPI` consolidation actually needs to cover.

## Decision

1. **No new `HarnessServices` field without proving it cannot be expressed through an
   existing `PluginAPI`/SDK abstraction.** The PR description must say what was tried
   first (a tool, a capability, an event) and why it didn't fit.

2. **A pinning test**, mirroring `tests/unit/test_stable_tier.py`'s treatment of SDK
   names: `tests/unit/test_harness_services_fields.py` snapshots the exact field set of
   `HarnessServices` and fails if it changes. Adding a field requires touching the
   snapshot in the same PR, which is the forcing function that makes rule 1 visible in
   review instead of assumed.

3. **Defer the `PluginAPI` consolidation design** until the Weather plugin (built
   entirely outside this repo, against the installed wheel) surfaces which of these
   sixteen fields an external, non-privileged plugin genuinely needs and in what shape.
   Designing the target abstraction from the existing field list risks encoding the
   sprawl into the "clean" version.

## Alternatives considered

- **Redesign `PluginAPI` now, from the current field list.** Rejected: the existing
  fields were added to solve in-tree problems (bundled reference plugins, which can
  informally reach further than a real third party ever should). Consolidating now
  risks shipping an abstraction shaped by convenience rather than by an external
  plugin's actual needs.
- **Freeze with no enforcement, rule only.** Rejected: this project's own "protect,
  don't redesign" areas (stable SDK, plugin caller identity) are strong precisely
  because they're pinned by a test, not by reviewer memory.

## Consequences

- Positive: API sprawl stops accumulating while the Weather plugin exercise runs;
  the eventual `PluginAPI` design is evidence-driven, not speculative.
- Negative: a plugin author (including bundled reference plugins) who needs a
  genuinely new capability before the consolidation lands must justify it explicitly
  rather than just adding a field — mild friction, by design.
- Reversible: the freeze is a process rule plus a snapshot test; lifting it is a
  one-line test update plus removing this ADR's rule 1 from review practice.

## Files

- `src/iris_harness/runtime/harness_services.py` — the frozen surface.
- `tests/unit/test_harness_services_fields.py` — new pinning test.
- `docs/architecture/core-boundary.md` — cross-references this ADR.
