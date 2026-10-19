# ADR-0004: Reuse the existing wiki/knowledge subsystem as the cross-domain correlation engine

Status: Superseded in part by ADR-0114 (2026-09-17) — automatic ingest is off by default
(`IRIS_WIKI_INGEST`) and the wiki is no longer the agent's knowledge layer. Measured before
superseding: 2,243 ingests, 0 queries, 0 `wiki_search` calls in four months.
Originally: Accepted (2026-05-24)
Context: Personal-assistant upgrade. Companion: `personal-assistant-upgrade.md` §3.5.

## Context

The user's stated value of the upgrade is "facts and correlations to
link events, info, inference and synthesize." Concretely: a Chase bank
email should tie to a Chase statement PDF should tie to Chase
transactions should tie to bills paid to Chase — surfacing as one
coherent entity rather than four disconnected records across domain
stores.

IRIS already has a knowledge subsystem (`src/iris_harness/knowledge/`) with
`WikiPage`, `Entity`, `WikiIngestEvent`, an entity extractor, page
manager, and engine. On-disk wiki at `data/wiki/{entities,concepts,
sources,synthesis}/` with `[[wikilink]]` syntax. It was built for an
adjacent purpose and is currently producing lossy output (sample entity
pages like `accessibility--the.md`, `achieved-successfully.md` show
common nouns being extracted as entities).

The question is whether to wire email/finance/calendar into the
existing wiki layer or build a parallel correlation model in domain
databases.

## Decision

**Adopt the existing wiki/knowledge subsystem as the cross-domain
correlation engine.** Domain skills (email-triage, finance-statements,
gcal-events, etc.) emit `WikiIngestEvent` for each meaningful
observation, with `entities_hint` populated. The wiki engine creates
and updates entity pages (`data/wiki/entities/<slug>.md`), appends
source backlinks, and accumulates cross-domain references on the same
entity.

A weekly **synthesis routine** reads recently-touched entities + recent
events from domain stores, calls Tier 2 LLM, and writes
`data/wiki/synthesis/weekly-<date>.md`. Morning brief consumes
synthesis pages.

**Prerequisite (Phase 0 gate):** tighten the entity extractor's precision
before email/finance volume reaches it. Prefer LLM-driven extraction with
typed output over the current heuristic. Spike scope: 1 day–1 week.

## Alternatives considered

1. **Build a `correlations` table in `email.db` or `finance.db`.**
   Rejected: cross-domain edges belong outside any single domain's
   schema. Putting them in `email.db` makes finance-only queries
   impossible without cross-DB joins.
2. **Add a graph database (Neo4j, NetworkX-on-disk).** Rejected: the
   wiki + wikilinks is a perfectly adequate graph for personal-scale
   data. Sub-second traversal via regex on markdown bodies. Adding a
   graph DB would dwarf every other dependency.
3. **Fold the wiki into the 4-layer Chroma memory.** Rejected: memory
   is for distilled personal facts ("Bob is my landlord"); wiki is for
   knowledge-graph nodes ("Bob the entity, mentioned in email/abc, in
   transaction/xyz"). Different shapes, different retrieval semantics.
4. **Ignore the wiki, ship without correlation in v1.** Rejected: the
   user explicitly named this as the upgrade's main value.

## Consequences

**Positive:**
- Reuses substantial existing infrastructure. No new abstraction.
- Cross-domain correlation is free once skills emit
  `WikiIngestEvent` — no per-pair integration work.
- Open-source pluggability: a new domain skill (fitness, journaling)
  gets correlations for free by emitting the same event.
- Synthesis pages become input to morning brief, query responses, and
  long-term recall — one mechanism, many consumers.

**Negative:**
- The entity extractor must be tightened **before** rollout. Current
  output noise will drown signal if email volume hits it as-is.
  Recorded as Phase 0 gate.
- The wiki sits on disk as markdown. Sensitivity inheritance must be
  respected (entity page about `chase-bank` is `high`-sensitivity).
  Synthesis on high-sensitivity entities routes to `tier3.local` per
  ADR-0002.
- Wiki growth over time will require a compaction story (Q12+ matters,
  not MVP).

**Reversibility:** moderate. Domain skills emitting `WikiIngestEvent`
are decoupled from the wiki engine. Switching to a different
correlation layer later means replacing the subscriber, not the
producers.
