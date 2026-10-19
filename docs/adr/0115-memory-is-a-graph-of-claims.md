# ADR-0115: Memory is a graph of claims, and its engine (memris) knows no vocabulary

Status: Accepted (2026-09-18) — design only; implementation not started
Relates to: ADR-0114 (memory is what the owner confirmed — decision 7 amended here), ADR-0105 (fact-store coherence), ADR-0077 (context budgets).
Amends: ADR-0114 decision 7 ("the graph is a view") — it stays a view, but over statements rather than over `key=value` facts.

## Context

After ADR-0114 shipped, the owner opened the Memory → Map tab and asked how its
relationship verbs (`knows`, `learned`, `works_at`) are produced, with a view to building
it out as a proper ontology-backed graph that other projects could adopt and port their
own memory graphs into. Reading the code gave the answer:

- **There is no graph and no ontology.** `memory/graph.py` rebuilds a star around "You"
  on every request from four sources: facts, session summaries, behaviors and patterns.
  Every edge starts at "You" or at a session. Entities have no type (an employer and a
  spouse are both `entity`), there are no entity-to-entity relations, and edges carry no
  time, source or confidence.
- **Vocabulary is split between YAML and code.** `entity_aliases.yaml` maps sixteen fact
  keys to verbs (`employer: works_at`). Everything else is hardcoded in `graph.py`:
  `knows`, `talked about`, `mentioned`, `learned`, `tends to`, the five node kinds, and a
  regex for the summary's `Referenced:` line. That breaks the owner rule that vocabulary
  lives in config, not code (2026-09-13).
- **Identity is a name.** `_entity_id()` makes the slug of a name into the node's ID, so
  "HDFC" and "HDFC Bank" are two nodes unless someone adds an alias by hand.
- **A fact can only be about the owner.** `user_facts` is `(key, value)` with an implied
  subject. "My wife works at Infosys" cannot be stored.
- **Finance vocabulary sits in core config.** `bank: banks_with`, `insurer: insured_by`
  and friends break the boundary rule (core = mechanisms, domains = plugins).
- **In a deployment the config is read-only.** `docker-compose.yml` mounts
  `./config:/app/config:ro`; only `data/` is writable. Anything the runtime learns cannot
  live in YAML.
- `user_facts` has **87 references across 15 source files and 24 test files**, so replacing
  it is the riskiest step of any redesign.

The owner brought a written proposal: keep a YAML language for developers, but make it a
thin layer over RDF/RDFS, SHACL and PROV-O, with JSON-LD for interchange; separate
classes, relations and attributes; keep constraints (shapes) and mappings out of the
ontology; model every learned relation as a statement with time and provenance;
generalise conversations to episodes; give entities stable IDs; and let the vocabulary
grow from experience without letting an LLM edit the ontology. The decisions below adopt
most of it and change it where the existing code or ADR-0114's lessons required.

## Decision

**1. The graph serves the agent, and it is derived first.** It is built for the agent to
query, not only for the owner to look at. It is still computed from stored records; a
new store is added only for what no existing record can produce (entity identity,
decision 4; learned vocabulary, decision 7).

**2. No vocabulary in code.** Every class, relation, attribute, verb and extraction rule
is declared in YAML. The engine is an interpreter of that YAML. A CI test enforces it
(decision 13).

**3. Statements replace `user_facts`.** One store holds
`(subject, predicate, object | literal)` plus time, provenance, status, confidence and
`supersedes`. Today's facts are statements whose subject is the owner. ADR-0114's
confirmation tiers become a statement's `status` (proposed / confirmed / retracted);
`user_fact_history` and `user_fact_contradictions` become supersede chains. The graph
stays a view over statements and episodes, so there is still one copy of every belief.

**4. Entities get stable IDs; when unsure, keep them separate.** An `entities` table holds
`(id, class, label, aliases, merged_into)`; `owner` is a reserved Person. Resolution:
- exact match after normalisation, or a YAML alias → resolved automatically;
- a likely match → **asked inline**, sharing ADR-0114's budget of one question per
  conversation with fact confirmation;
- unanswered → the two stay separate while evidence accumulates, and merge automatically
  only past a configured threshold (for example, used interchangeably in 3 episodes), with
  the evidence recorded and a one-step undo;
- "no" → recorded as `distinct_from`, never asked again.
Nothing becomes `owl:sameAs` on its own. A wrong merge corrupts every statement attached
to both entities; a missed merge is only a duplicate node.

**5. Statements are bitemporal from day one.** Valid time (`valid_from`, `valid_to`; NULL
means unknown) and record time (`recorded_at`, `retracted_at`). Three operations replace
today's single overwrite: **supersede** ("I moved to HSBC" closes the Barclays statement),
**end** ("I left Barclays"), **retract** ("forget that" — never true, kept in the audit
trail, restorable). Whether a new value supersedes or coexists is decided by
`shapes.yaml` (`max_count: 1`), not by code. Only current, non-retracted statements reach
a prompt.

**6. Extraction is closed and scoped one hop from the owner.** The extractor prompt is
generated from the ontology YAML; the model picks a known predicate or returns nothing.
ADR-0114's plausibility, durability and grounding gates stay, with grounding applied to
the entity name as well as the literal. Subjects are the owner and entities one hop away
through a relation asserted in the same message or already confirmed, so "my wife Priya
works at Infosys" is captured and the same sentence about a public figure is not. The hop
limit is configuration. `fact_keys.yaml` is retired into `ontology.yaml` and
`mappings.yaml`.

**7. Vocabulary can grow, automatically, in a learned layer held as data.** A predicate
the ontology lacks is:
- a near-synonym of an existing predicate (similarity past a threshold and compatible
  domain and range) → recorded as an alias of it;
- recurring (N observations across M episodes) → activated as `learned:<name>` with
  inferred domain and range, its statements following the normal confirmation tiers;
- anything else → counted, with a few examples, and expired after 90 days.
Learned terms live in an `ontology_terms` table in the data volume, never in YAML, so the
runtime never writes config and nothing restarts. The vocabulary in use is
`core YAML (mem:) ∪ plugin YAML (fin: …) ∪ learned rows (learned:)`. YAML holds rules and
the database holds what was learned, so no YAML file goes stale when nobody promotes a
term; `iris ontology promote` is optional curation that writes a diff for a human to
commit. Every graph export carries the learned terms its statements use, and the startup
compile check links a learned term to a later core term with the same label, domain and
range.

**8. Ontology changes never rewrite stored statements.** A retired term stays in YAML with
`deprecated: true` and `replaced_by` (a list, for splits, where the new classes are
subclasses of the old). Reads resolve through `replaced_by`. Every statement records the
`ontology_version` it was written under. A compile check at startup and in CI loads core,
plugin and learned vocabulary and fails if a term that stored data uses has disappeared
without a `replaced_by`.

**9. The agent gets a graph tool and automatic one-hop linking.**
`memory_graph(entity, relation?, direction, hops ≤ 2, as_of?)` returns statements with
their provenance; its parameter descriptions are generated from the ontology. No query
language is exposed: the local models cannot write SPARQL or Cypher reliably, and doing
so would tie memory to one backend. Each turn, entities named in the message are linked
and their current one-hop statements enter the prompt within a token budget shown on the
Context tab. `memory_search` stays text similarity.

**10. The engine is `memris`, a sibling root with no IRIS imports.** `src/memris` holds the
YAML compiler and compile check, the statement and entity model, the bitemporal
operations, the resolution policy, a `GraphStore` protocol with a SQLite implementation,
and the importers and exporters. It contains no LLM: similarity and extraction are
callables passed in. Dependencies are pydantic and pyyaml, with rdflib and pyshacl behind
an `[rdf]` extra. An import-linter `forbidden` contract keeps it from importing
`iris_harness`. It ships in the one wheel (folder-restructure decision 1) and can become
its own package later without a rewrite. `iris_harness.memory` holds IRIS's use of it:
extraction prompt, confirmation tiers, turn capture, the tool, the UI and the migration.
Connectors to external systems (Graphiti, Neo4j, Mem0) and domain vocabulary (finance)
are plugins.

**11. JSON-LD is the interchange format.** The `@context` is generated from the ontology
YAML and provenance uses PROV-O terms. An importer is a source reader plus a
`mappings.yaml`, run by the same mapping engine IRIS uses for its own facts; vocabulary
the ontology does not know goes to the learned layer under the source's prefix
(`graphiti:COLLEAGUE_OF`). memris → JSON-LD → memris must reproduce statements, IDs, time,
provenance and learned terms exactly; a foreign import either maps every source field or
lists what it left out. Graphiti is the first connector because its model (episodes,
entities, temporal fact edges) is the closest to this one.

**12. Build order.** Eleven PRs, listed in `docs/architecture/memris-plan.md`. The store
swap (PR 2) comes before any new capability and sits behind a facade so the existing 87
references keep working, and it needs a dry-run report against the owner's real
`data/memory.db` before merge. Nothing else gates the start: the M6 memory-layer move
this record first waited on had already landed (#459, 2026-09-14), so no restructure
work touches `memory/store.py` alongside it.

**13. Acceptance.** A deterministic suite (`tests/integration/test_memory_graph.py`)
covering supersede and `as_of`, one-hop capture, closed vocabulary to learned term,
resolution ask / separate / evidence merge / undo, retraction, `replaced_by` and the
compile check, the JSON-LD round trip, and a test that fails if any vocabulary from the
YAML appears in `src/memris` or `memory/graph.py`. A live playground suite passing 5/5 on
granite4 and on qwen2.5:7b-instruct. For PR 2, a migration dry run accounting for every
`user_facts` row with no change in the existing continuity suite.

## Alternatives considered

- **A Statement store beside `user_facts`.** Less work now; two sources of truth
  permanently, and the migration still has to happen later. Rejected.
- **memris as an IRIS plugin.** Core memory would depend on a plugin, the SDK would have
  to expose memory internals (gate 2), and only IRIS users could install it. Rejected;
  connectors and domain vocabulary are plugins instead.
- **Open extraction, mapped afterwards.** Free-form triples are how the wiki filled with
  2,175 "person" pages. Rejected.
- **Migrating statements in place on every ontology change.** Cleaner data, destroyed
  history, and every edit becomes a migration. Rejected for deprecation and `replaced_by`.
- **Writing learned vocabulary to YAML.** The deployed config is read-only, and a mirror
  that someone must remember to export goes stale. Rejected for a table.
- **Auto-merging likely duplicate entities.** Wrong merges are expensive and hard to see.
  Rejected for ask, then evidence.
- **An RDF-native store or Neo4j as the backend now.** Ties the design to one engine
  before the model is proven. Deferred: both are export or connector targets.
- **Exposing SPARQL or Cypher to the agent.** Unreliable on the local models, and
  backend-specific. Rejected for a typed tool.
- **Names `memgraph` and `memorise`.** Memgraph is an existing graph-database company and
  the PyPI name is taken; `memorize` is taken, which makes `memorise` a spelling trap.

## Consequences

- **The store swap is the risk.** It touches the most-used memory API. The facade and the
  real-data dry run are how that risk is paid down; the retention work showed why (an
  age-only rule would have deleted 864 turns).
- **Entity questions cost interruptions.** They share ADR-0114's one-per-conversation cap
  rather than adding a second one.
- **The learned layer can grow junk terms.** Thresholds and 90-day expiry bound it;
  inferred ranges fall back to the superclass when the observed objects are mixed.
- **Each install learns its own vocabulary.** Promotion or export is how it travels; this
  is the same property facts already have.
- **YAML becomes the ontology's source of truth**, so ontology edits get code review like
  any other config change.
- **Portability is a tested promise, not a hope**: the round trip and the import report
  are CI-checked.
- Reversible up to PR 2. After it, going back means restoring `user_facts` from the facade.

## Verification

Decision 13 is the bar. Until the implementation lands, this ADR and
`docs/architecture/memris-plan.md` are the record of what was decided and why.
