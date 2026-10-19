# ADR-0012: Manifest schema extension — implementation shape

Status: Accepted (2026-05-25, retroactive)
Context: Personal-assistant upgrade Phase 0 track 2. Companion: ADRs 0008, 0009; `personal-assistant-upgrade.md` §4.4.

## Context

ADRs 0008 (R2/R3 calendar split), 0009 (no-sandbox trust model), and
the canonical doc §4.4 enumerate manifest fields the personal-assistant
upgrade requires:
``default_enabled``, ``iris_compatibility``, ``source``, ``maintainers``,
``trust_level``, ``calendar_visibility``, ``homepage``.

Phase 0 track 2 implemented those fields on ``SkillManifest`` plus two
new nested models, ``SkillSource`` and ``SkillMaintainer`` (commit
``5af5590``). The implementation resolved eight implementation
decisions that aren't in the parent ADRs and would otherwise be
invisible to future readers — same gap that ADR-0010 closed for the
entity-extractor spike and ADR-0011 closed for the credentials module.
This ADR records them.

Authored retroactively after the track shipped; same content as if
written before the commit.

## Decision

Eight implementation decisions:

1. **``Literal["git"]`` only for ``SkillSource.type``.** The schema
   declares git as the *single* supported source type today. Other
   transports (PyPI package, local path, URL tarball) are deferred
   until the ``iris skill add`` CLI lands in Phase 7; at that point a
   future ADR may extend the Literal additively. Today, declaring
   ``type: pypi`` in a manifest raises a Pydantic ValidationError —
   the schema is honest about what's supported.

2. **``SkillMaintainer.contact`` is optional.** A maintainer may
   appear with name only; an email/handle is encouraged but not
   required. Rationale: contributors may not want a public contact
   point, and a strict-required field would force them to invent
   placeholder values that erode signal.

3. **``homepage: str | None`` has no URL-shape validation.** The field
   is treated as an opaque string at manifest-parse time. The
   ``iris skill add`` CLI will validate URL parseability and HTTPS at
   install time. Skipping validation here keeps the schema layer free
   of network-aware code.

4. **No semver-range parsing for ``iris_compatibility``.** The field
   stores any non-empty string at manifest-parse time (e.g.,
   ``">=0.7,<2.0"``). Actual range parsing and matching against the
   running IRIS version happen at install time in ``iris skill add``
   (Phase 7). Rationale: parser libraries (packaging, semver) add a
   dep and a sharp-edge surface that's only needed for the install
   flow, not for every manifest load.

5. **``_validate_community_trust_has_source`` validator is asymmetric.**
   ``trust_level: community`` REQUIRES a ``source`` block;
   ``trust_level: first-party`` MAY declare a source but doesn't
   have to. Rationale: in-tree first-party skills sometimes have
   public repos (the skill author wants to credit the repo, even
   though the skill ships in-tree); blocking them from declaring
   ``source`` would lose information. Only the community case is a
   safety boundary (install-time UX must be able to show "you're
   about to install code from <url>"), so only that case is
   enforced.

6. **``calendar_visibility: "never"`` is the default.** Skills that
   produce tasks default to NOT mirroring those tasks onto the user's
   calendar; mirroring requires the skill author to explicitly opt in
   per ADR-0008. Rationale: privacy-respecting default — most skill
   authors won't think about calendar visibility, and "show up on the
   user's calendar by default" is a surprising side effect.

7. **``default_enabled: True`` is the default.** Most skills should
   auto-load on discovery; opt-in (``default_enabled: false``) is the
   exception for region-specific or sensitive skills like the CAS
   community skill. Rationale: making most skills opt-in would
   create install-flow friction for the common case where the user
   wants the skill they just installed to work immediately.

8. **``Literal`` types throughout, not ``str`` + custom validator.**
   ``trust_level: Literal["first-party", "community"]``,
   ``calendar_visibility: Literal["always", "if_due_at_set", "never"]``,
   ``source.type: Literal["git"]``. Rationale: Pydantic ``Literal``
   raises a clear ValidationError on typos at parse time, with the
   accepted values visible in the error message. A custom validator
   would re-implement the same check with worse error messages and
   more code.

## Alternatives considered

1. **``Union`` of source types from day one** (git, pypi, local, url
   tarball). Premature; we have one transport pattern (git clone) and
   no Phase 0 use case that needs the others. Extending later is
   additive — a new Literal value plus a discriminator validator.

2. **Required ``SkillMaintainer.contact``.** Would enforce a contact
   for community skills (auditable provenance) but forces placeholder
   values for contributors who don't want a public contact. The
   defence-in-depth gain is small; install-time prompts surface the
   maintainer info regardless.

3. **URL-shape validation for ``homepage`` (e.g., ``HttpUrl`` Pydantic
   type).** Catches typos at parse time but introduces a dep on
   ``email-validator`` / ``idna`` and rejects e.g. local-network HTTP
   URLs that might be valid in some deployments. Defer to install-time
   validation where the network is reachable and rejection is
   actionable.

4. **Parse semver ranges at manifest-load time.** Library choice
   (packaging vs semver vs python-semantic-version) is itself a decision;
   manifest load is a hot path during skill discovery; ranges are only
   meaningful relative to a running version which the model layer doesn't
   know. Deferring to install time scopes the parse to one call site.

5. **Symmetric ``trust_level``-vs-``source`` validator** ("first-party
   MUST NOT declare source", "community MUST declare source"). Cleaner
   symmetry, loses real information (in-tree first-party skills
   crediting their repos). Rejected as over-strict.

6. **``calendar_visibility: "if_due_at_set"`` as default.** Friendlier
   default for finance/email-followup skills that always set due_at,
   but wrong for the average skill author who doesn't think about
   calendar at all. Privacy-respecting "never" plus explicit per-skill
   opt-in is the better default.

7. **``default_enabled: False`` as default** (every skill opts in
   explicitly). Stricter security posture but creates friction for the
   90% case where the user wants a freshly-discovered skill to work.
   Opt-out via ``default_enabled: false`` for the rare cases (CAS,
   region-specific, sensitive) is the better balance.

8. **String types + manual validation functions** for trust_level
   etc. Implementable but reinvents what ``Literal`` does for free.

## Consequences

**Positive:**
- Schema parses every existing in-tree manifest unchanged (all new
  fields default to first-party-in-tree expectations).
- Pydantic ``Literal`` errors give callers a clear "accepted values:
  …" message on typos.
- Each implementation choice has a stable refactor path: Literal
  extension is additive; validators can be tightened later; default
  values can flip with a single edit if usage patterns argue.
- 72 existing skills tests + 8 new schema tests passed unchanged.

**Negative:**
- Eight small decisions distributed across a single schema file mean
  the *why* of each isn't visible at the line; this ADR is the
  central place to read them. Mitigation: a brief docstring per field
  in ``SkillManifest`` references the relevant parent ADR or this
  ADR.
- ``iris_compatibility`` is unparsed at schema layer; bad ranges
  (e.g., ``"foo bar baz"``) won't be caught until install time.
  Acceptable since install is the only place the range is meaningful.

**Reversibility:** high. Each of the eight decisions is isolated to
its field or its validator; flipping any of them is a small edit. The
two new nested models are self-contained.

## Related ADRs

- ADR-0008 (R2/R3 calendar split) — parent for ``calendar_visibility``.
- ADR-0009 (no-sandbox community-skill trust model) — parent for
  ``trust_level``, ``source``, ``maintainers``, and the asymmetric
  community-requires-source validator.
- ADR-0010 (entity extractor implementation shape) — first example of
  the implementation-shape ADR pattern this ADR continues.
- ADR-0011 (Keychain credentials module shape) — sibling
  implementation-shape ADR for Phase 0 track 3.
- Canonical doc §4.4 — the ``homepage`` and ``iris_compatibility``
  fields are listed there without an independent ADR; this ADR is the
  decision log for their implementation defaults.
