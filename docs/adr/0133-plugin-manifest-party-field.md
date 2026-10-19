# ADR-0133: A `party` field on PluginManifest, distinct from the existing `trust` field

Status: Accepted (2026-10-05)
Relates to: `runtime/plugin_host/manifest.py` (`PluginManifest`, `TrustLevel`),
`docs/oss-plan-review.md`, the L4.5 Harness Hardening milestone (the Weather plugin
is the first manifest to declare this field).

## Context

`PluginManifest` already has a `trust: TrustLevel = "in-process"` field, where
`TrustLevel = Literal["in-process", "mcp"]` (decision 8). This is an **execution
mechanism** axis: does the plugin run in-process behind the fault boundary, or
out-of-process over the signed MCP bridge. It says nothing about **who wrote the
plugin** or how much the operator is vouching for it — every bundled reference plugin
and every third-party plugin look identical on this field today, both defaulting to
`"in-process"`.

The external review's "trust ladder" (first party / trusted third party / untrusted)
is a different, complementary axis: provenance and operator trust, not execution
mechanism. Reusing the name `trust` for both would conflate them — an operator reading
a manifest should be able to tell "this runs in-process" from "this is a third party I
chose to trust in-process" without inferring the latter from context.

## Decision

1. **Add `party: Literal["first-party", "trusted-third-party", "untrusted"] =
   "first-party"`** to `PluginManifest`, independent of `trust`. `first-party` is the
   default so every existing bundled plugin (`plugins_builtin/*`) needs no manifest
   change. A third-party plugin the operator has reviewed and chosen to run in-process
   declares `trusted-third-party`. `untrusted` is reserved for a plugin the operator
   has not personally vetted — current policy (not enforced by this ADR) is that an
   `untrusted` plugin should set `trust: "mcp"`, but the fields stay independent so a
   future isolation mechanism doesn't require re-litigating provenance semantics.

2. **The Weather plugin (L4.5 milestone) is the first plugin to declare
   `party: "trusted-third-party"`**, with `trust: "in-process"` unchanged — it is
   written and reviewed by the same maintainer, run in-process, but the manifest now
   says explicitly that this is a plugin from outside the monorepo rather than a
   bundled reference plugin, which is the whole point of building it externally.

3. **No enforcement logic yet.** This ADR adds the declaration only. Whether the
   kernel should refuse to mount an `untrusted` plugin with `trust: "in-process"`, or
   surface the combination as a warning, is future work once the out-of-process
   prototype (same milestone, lower priority) exists to compare against.

## Alternatives considered

- **Repurpose the existing `trust` field to carry both axes** (e.g.
  `Literal["first-party-in-process", "trusted-in-process", "mcp"]`). Rejected: this
  conflates two independent questions into one enum, and breaks the existing
  `trust: "mcp"` semantics for a plugin that is also first-party (the MCP bridge
  exists for isolation, not distrust, per the M2+ roadmap).
- **Defer this entirely to the out-of-process prototype.** Rejected: declaring
  provenance costs nothing and is directly relevant right now — the Weather plugin
  exercise is explicitly simulating "a plugin from outside the repo," and the manifest
  should say so.

## Consequences

- Positive: manifests gain a cheap, explicit provenance signal with zero migration
  cost (every existing plugin defaults correctly); sets up the out-of-process
  prototype and any future enforcement without redesigning the schema then.
- Negative: none — additive, optional-with-default field.
- Reversible: a new field with a safe default; dropping it later costs nothing beyond
  removing a line from manifests that set it explicitly.

## Files

- `src/iris_harness/runtime/plugin_host/manifest.py` — new `party` field on
  `PluginManifest`.
- External: `iris-weather-plugin/manifest.yaml` — first manifest to declare
  `party: trusted-third-party`.
