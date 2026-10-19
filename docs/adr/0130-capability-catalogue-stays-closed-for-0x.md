# ADR-0130: Capability catalogue stays closed for 0.x

Status: Accepted (2026-10-05)
Relates to: ADR-0129 (`HarnessServices` freeze), `core-boundary.md`,
`docs/oss-plan-review.md`, the L4.5 Harness Hardening milestone.

## Context

`src/iris_harness/foundation/capabilities.py` defines a sophisticated capability model
(`provides`/`uses`/`requires`, unmask grants, method effects, content classification,
fan-out for multiple providers of one capability) — and, as of this ADR, zero
`CapabilitySpec` registrations exist anywhere outside tests. The module's own docstring
still reads "Empty until rollout step 4 adds the first capabilities." The architecture
is well tested but not yet battle-tested by a real, independently packaged plugin.

The catalogue is centrally registered: every capability name a plugin might `provide`
or `use` must exist in core's catalogue. That gives IRIS core complete visibility into
every possible cross-plugin interface — valuable for governance, since the kernel can
reason about every capability's effect and classification without trusting a plugin's
say-so. The cost is that a third-party plugin author cannot invent a new capability
(e.g. `weather.forecast`, `notes.search`) without a PR against the core repo, even
though they implement it entirely in their own plugin.

## Decision

1. **Keep the capability catalogue closed through the 0.x line.** Core registers every
   capability's schema (name, contract, effect, classification); a plugin, bundled or
   third-party, provides the implementation.

2. **The L4.5 milestone's capability production exercise registers exactly one real
   capability, `weather.forecast`, in core**, implemented by the external
   `iris-weather-plugin` repo and consumed by a second external "trip advisory" plugin.
   This is the first production (non-test) use of the catalogue, and it is deliberately
   done through the closed-catalogue path — a core PR to name the capability, two
   external PRs to implement and consume it — so the friction of the 0.x model is felt
   firsthand before it's revisited.

3. **Revisit before 1.0.** Once there is real usage data (how often would a third party
   actually want to invent a capability vs. extend an existing one), decide whether a
   namespaced, decentralized scheme (e.g. plugin-prefixed capability ids a plugin can
   register without a core PR) is worth the governance cost of capabilities core cannot
   fully reason about ahead of time.

## Alternatives considered

- **Open/decentralized capability registration now.** Rejected for 0.x: the catalogue
  has never been used by a real plugin yet (A2 in the external review) — opening it up
  before the closed model has even been exercised once would be solving a problem with
  no evidence behind it, and it weakens the governance guarantee ("core knows every
  possible cross-plugin interface") before that guarantee has proven its worth.
- **Leave the decision implicit.** Rejected: the review correctly flagged that the
  policy was never stated explicitly, only implied by the code's current shape. An
  undocumented default is not a decision; a future contributor could "fix" the
  friction by quietly opening the catalogue without anyone having weighed the
  trade-off.

## Consequences

- Positive: the capability model gets its first production exercise without
  simultaneously deciding how (or whether) to decentralize it; governance keeps full
  visibility into cross-plugin interfaces through 0.x.
- Negative: a third-party plugin author who wants a genuinely new capability during
  0.x must file a core PR just to name it — documented explicitly in
  `core-boundary.md` so it isn't a surprise.
- Reversible: nothing about the closed model is structurally locked in; the 1.0
  revisit is a scheduled decision point, not a one-way door.

## Files

- `src/iris_harness/foundation/capabilities.py` — the catalogue this ADR keeps closed.
- `docs/architecture/core-boundary.md` — documents the hybrid schema/implementation
  split this ADR produces.
