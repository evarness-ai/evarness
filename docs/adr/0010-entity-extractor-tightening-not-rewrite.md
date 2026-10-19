# ADR-0010: Entity extractor — tighten regex + hint validation, not rewrite

Status: Accepted (2026-05-24) — after Phase 0 spike investigation

**Status pass (2026-10-18):** the decision holds; the code now lives in `memory/knowledge/entity_extractor.py`. stopword denylist, allowlist and hint validation are there; the extractor also accepts an optional LLM call

Context: Personal-assistant upgrade Phase 0. Companion: `personal-assistant-upgrade.md` §3.5, §5; ADR-0004.

## Context

ADR-0004 (canonical doc §3.5) named the existing wiki entity extractor
at `src/iris_harness/knowledge/entity_extractor.py` as a **Phase 0 gate**: the
extractor produces noisy entity pages today (e.g. `breaking-news.md`,
`hello--it.md`, `web-search.md`, `none--my.md`), and Phase 1+ skills
(email-triage, finance-statements, calendar) will only multiply that
noise once they begin emitting `WikiIngestEvent` at volume. The spike
question was scope: is the cleanup a one-day tightening, or a one-week
rewrite as LLM-driven typed extraction?

The spike inspected the 129-line extractor, sampled ~25 of the ~390
existing entity pages in `data/wiki/entities/`, traced both callers of
`WikiIngestEvent` (`src/iris_harness/runtime/bootstrap.py:7344`,
`src/iris_harness/learning/lesson_capture.py:232`), and ranked token recurrence
across the noise pages.

### Findings

Two localised, fixable leak sources account for the bulk of the noise:

1. **`_PERSON_RE` is too permissive.** The regex
   `[A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,25}` matches any two consecutive
   title-cased words. User-prose ingestion in `bootstrap.py` passes
   raw `/chat` text with empty hints, so every random title-case
   bigram (`Breaking News`, `Latest Headlines`, `Active Items`,
   `Hello World`, `Today Programme`) becomes a `person` entity.
2. **`entities_hint` has no input validation.** `lesson_capture.py`
   passes `lesson.tools` (snake_case tool names like `web_search`,
   `data_storage`) as hints; the extractor trusts them at confidence
   0.95 and writes them as entity pages.

Other regexes (`_INSTITUTION_RE`, `_EMAIL_DOMAIN_RE`, `_CONCEPT_RE`)
have suffix or list anchors and are not significant noise sources.

The `--` (double-hyphen) pattern in slugs (`hello--it`,
`accessibility--the`, `none--my`) confirms the hint-pollution path,
not the regex path — `_PERSON_RE` requires whitespace-only between
the captured words and cannot produce `--` slugs directly.

## Decision

**Tighten the existing extractor; defer the LLM-primary rewrite.**
Three small, independent edits:

1. **Add a three-tier bigram-stopword denylist** to
   `entity_extractor.py` — ~50 words across articles/pronouns/
   prepositions, interjections/greetings, and recency adverbs.
   `_PERSON_RE` matches are rejected if either word appears in the
   combined denylist, unless the exact `(first, second)` pair is in
   a small allowlist (seeded with `("new", "york")`, `("new", "delhi")`,
   `("new", "jersey")`, `("hong", "kong")`, `("san", "francisco")`).
2. **Validate `entities_hint` inputs** at the boundary of
   `EntityExtractor.extract()`: length 3–60, not pure-lowercase
   snake_case (rejects tool names), not bigram-stopword-shaped.
   Failing hints are dropped with a debug log, never raised.
3. **Stop passing tool names as entity hints** in
   `learning/lesson_capture.py:236`. Tool references belong in
   `WikiIngestEvent.metadata`, not in `entities_hint`.

Compaction of the ~390 existing noise pages is a **separate, later
one-shot migration** — not part of this commit. The fix above stops
the bleeding; cleanup is a backfill.

## Alternatives considered

1. **Rewrite extractor as LLM-primary typed extraction** with
   Pydantic-shaped output. Genuinely better long-term per the
   `feedback_agentic_over_heuristic` memory note. Rejected for Phase 0:
   introduces a Tier-1 LLM call into every wiki ingest; latency and
   cost compound at the email/finance volume of Phase 1+. Worth doing
   later as a future ADR superseding this one.
2. **Introduce a typed `EntityHint(type, name, source)` API** so
   callers declare entity type explicitly and the extractor trusts +
   normalises. Rejected for Phase 0: requires changing every caller
   (`bootstrap.py`, `lesson_capture.py`, plus the upcoming
   email-triage / finance-statements / calendar producers in Phase 1+).
   Better surfaced as part of those Phase 1+ work items, not
   retrofitted in isolation.
3. **Compact the existing 390 noise pages as part of this commit.**
   Rejected: reverse-engineering "noise vs signal" at scale needs its
   own care and should not delay unblocking Phase 0's other six
   tracks. Cleanup happens in a separate commit when convenient.
4. **Per-position denylists** (different sets for the first vs second
   word of the bigram). Rejected as premature complexity; one combined
   denylist with a small allowlist for known-good pairs covers the
   observed noise patterns with manageable false-rejection cost.

## Consequences

**Positive:**
- Noise stops growing immediately when Phase 1+ skills begin
  emitting `WikiIngestEvent`. The Phase 0 gate is cleared.
- Spike unblocks the other six Phase 0 tracks for parallel work.
- Edits are independent and individually revertable.
- Cost is small: ~150 LOC modified across two files, ~120 LOC of new
  unit tests, this ADR.

**Negative:**
- The ~390 existing noise pages persist until a separate compaction
  pass. They will show up in search results and entity-listing UIs
  in the meantime; the compaction pass is a follow-on Phase 0 item.
- The denylist is a small static heuristic. It will miss novel noise
  patterns that the regex captures (e.g. title-cased verb phrases
  not in the recency set). Acceptable trade for Phase 0; the LLM-
  primary rewrite path remains open for later.
- False rejection of legitimate proper nouns containing a stopword
  token (e.g. `Latest Releases` as a band name). Mitigated by the
  allowlist — extend as real ingestion surfaces real misses.

**Reversibility:** high. Each edit is independent. The denylist is a
single module constant; the hint validation is one function; the
lesson_capture change is one argument removal. Reverting any of them
restores prior behavior without schema or API changes.

## Related ADRs

- ADR-0004 (correlation engine) named this spike as the Phase 0 gate.
- A future ADR replacing the regex path with LLM-primary typed
  extraction would supersede the relevant section of this ADR; this
  ADR's denylist would remain only as a fast-path fallback.
- A future ADR introducing a typed `EntityHint(type, name, source)`
  API would supersede the string-based hint contract this ADR
  preserves.
