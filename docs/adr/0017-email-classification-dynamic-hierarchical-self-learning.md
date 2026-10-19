# ADR-0017: Email classification — dynamic, hierarchical, self-learning categories

Status: Accepted (2026-05-25)
Context: Personal-assistant upgrade Phase 1 entry. Supersedes the static-categories.yaml posture of canonical doc §3.1. Companion: Phase 1 spike writeup at `docs/architecture/spikes/phase1_email_classification.md`.

## Context

The canonical doc §3.1 originally treated email classification as a
pipeline over a **static category set** declared in
`config/skills/email/categories.yaml`. The Phase 1 spike (50 emails,
6 user-defined categories) surfaced two problems that invalidate the
static posture for the user's real corpus:

1. **Scale.** The user has ~6,000 emails in a single account and
   multiple accounts (IN + US). Six categories is a starting point;
   the actual shape distribution at 6K+ scale spans 15-30+ distinct
   clusters with strong subcategorical structure (`finance.banking`
   vs `finance.insurance` vs `finance.investment`; `learning.school`
   vs `learning.courses` vs `learning.github`).
2. **paia experience.** The user's prior assistant (paia) used
   subcategory classification with a static schema and still required
   ongoing manual tweaks — new senders introduced shapes that didn't
   fit, and category meanings drifted over time.

The spike re-run with user-labeled ground truth showed:
- Content-driven categories (finance/marketing/social/news-digest)
  classified well at 71-89% recall from Tier-3-local alone.
- The `personal` category collapsed to 7% because the user defines
  `personal` relationally (anything about family life, including kids'
  school notifications and conversational bank threads) — a
  user-specific semantic mapping no content-only classifier can
  recover without seeing the user's labeling history.

These findings tell us the right architecture is not "good static
categories" but **dynamic categories that grow from the user's
corpus with a self-learning loop**. This ADR locks that design.

## Decision

Five interlocking design choices:

### 1. Categories are a typed, hierarchical data model in `data/iris.db`

Replaces `config/skills/email/categories.yaml` as the source of truth.
`categories.yaml` is demoted to a **seed file** that bootstraps the
table on first run; subsequent mutations happen at runtime via CLI
commands and the self-learning loop.

```sql
CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,           -- path-style slug, e.g. 'email:finance.banking.transaction-statement'
    type        TEXT NOT NULL,              -- 'email', 'finance', future: 'calendar', 'task'
    name        TEXT NOT NULL,              -- leaf label, e.g. 'transaction-statement'
    parent_id   TEXT REFERENCES categories(id),
    level       INTEGER NOT NULL,           -- 1=root, 2=branch, 3=leaf (max 3 enforced)
    description TEXT,
    source      TEXT NOT NULL,              -- 'seed' | 'discovered' | 'user-added' | 'user-merged-from'
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    retired_at  TEXT,                       -- soft-delete; NULL = active
    UNIQUE (type, parent_id, name),
    CHECK (level BETWEEN 1 AND 3)
);

CREATE TABLE IF NOT EXISTS category_history (
    id              TEXT PRIMARY KEY,
    category_id     TEXT NOT NULL REFERENCES categories(id),
    event           TEXT NOT NULL,           -- 'created'|'renamed'|'merged-into'|'split-from'|'retired'
    payload         TEXT,                    -- JSON
    occurred_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_discovery_runs (
    id                 TEXT PRIMARY KEY,
    email_account_id   TEXT NOT NULL REFERENCES email_accounts(id),
    type               TEXT NOT NULL,         -- 'email' for now
    started_at         TEXT NOT NULL,
    completed_at       TEXT,
    embeddings_count   INTEGER,
    clusters_found     INTEGER,
    proposals_made     INTEGER,
    proposals_accepted INTEGER,
    notes              TEXT
);
```

Key shape decisions:
- **Three-level hierarchy** (root → branch → leaf), enforced by the
  `level` CHECK. Two levels were too coarse for the user's corpus;
  more than three over-engineers the common case. Examples:
  `finance.banking.transaction-statement`,
  `learning.school.community-school`, `marketing.shopping.apparel`.
- **Path-style slug as primary key** (`email:finance.banking.transaction-statement`)
  rather than a UUID. Human-readable in dumps, debugger-friendly,
  easy to grep across kNN store and logs. Cost: renames update
  the `id` and need to migrate kNN-store references — handled by
  a one-shot migration helper that takes a few seconds for typical
  store sizes.
- **`type` column** opens the table to multiple taxonomies. `email`
  is the only consumer in Phase 1; `finance` will use the same table
  for transaction categorization in Phase 3; `calendar` may use it
  in Phase 6 for event types. Avoids a `finance_categories` /
  `calendar_categories` proliferation later.
- **`category_history`** preserves the evolution audit. When a
  category is renamed or merged, kNN-store labels are remapped via
  a migration helper; `category_history` is the trail.

### 2. Single global taxonomy, per-account discovery

The `categories` table is one global set per `type` (i.e., all
email-typed categories form a single taxonomy regardless of which
email account contributed the seed examples or proposals). Bootstrap
discovery, however, **runs per `email_account_id`**.

Rationale:
- A "bank-statement" category is the same conceptual shape regardless
  of whether the email arrived at the IN or US account. Splitting
  taxonomies per account would force cross-account queries to do
  taxonomy reconciliation that has no real value.
- A second account's bootstrap run can either (a) match a cluster to
  an existing category — Tier 3's name proposal lands on a slug that
  already exists, and the kNN store gets seeded with the new
  account's examples under that existing category — or (b) propose a
  new category that didn't exist before. Both flows are natural.
- The kNN store is also global: labeled examples from account A
  inform classifications on account B if the sender's content is
  similar. Per-account override mechanisms can be added later if a
  real conflict surfaces; not in MVP scope.

### 3. Bootstrap discovery process — opt-in CLI, per account

`iris email bootstrap-categories --account <email_account_id>` runs a
four-step pipeline:

1. **Embed.** Load all emails for the account from `email.db` (or a
   user-specified sample size). Embed via
   `sentence-transformers/all-MiniLM-L6-v2` (~80MB, ~384-dim, fast).
   6,000 emails → ~9MB of vectors, completes in ~30-60 seconds on
   M4 Max.
2. **Cluster.** HDBSCAN with `min_cluster_size=20` (configurable).
   Density-based clustering naturally handles variable cluster sizes
   and emits a "noise" set for outliers that don't fit anywhere.
3. **Name.** For each cluster, send the centroid embedding's 5
   nearest representative emails (sender + subject + snippet) to
   Tier 3 LOCAL with a structured prompt: *"Propose a hierarchical
   category path (level-1.level-2.level-3) for this cluster. Match
   an existing category if appropriate; otherwise propose new."*
   Returns the proposal + a candidate description.
4. **Review.** Interactive CLI (similar to the spike labeler) walks
   the user through each proposal — `accept` / `rename` / `merge into
   <existing>` / `reject`. Accepted proposals persist to
   `categories.db` with `source='discovered'`; the cluster's emails
   are added to the kNN store as seed examples under the accepted
   category.

**Re-running** the bootstrap on a later date re-clusters only the
emails received since the last `category_discovery_runs.completed_at`
for that account. New clusters that emerge that don't match existing
categories surface as new proposals. This is the "learn and update
along the way" path the user explicitly called for.

### 4. Three self-learning signals after bootstrap

Once the initial taxonomy is in place and email-triage is running,
three signals drive ongoing category-set evolution:

| Signal | Trigger | Action |
|---|---|---|
| **User correction** | User reclassifies a triaged email | Add the corrected example to the kNN store under the new category. If the user names a category that doesn't exist, prompt: *"Create new category `<path>`?"* |
| **Persistent low confidence** | A sender's emails routinely fall below the kNN confidence threshold for ≥N occurrences (default N=5) | Flag for user review; likely indicates a missing subcategory or a "split candidate" within an existing category |
| **Off-cluster drift** | Weekly background re-cluster of emails received since last run; if a new cluster emerges that doesn't fit any existing category | Surface as a new-category proposal in the next morning brief |

All three are passive surfaces — the user reviews proposals; the
system never auto-creates categories without consent.

### 5. kNN-gate criterion refined for the dynamic taxonomy

The acceptance criterion for completing Phase 1 from the spike:

> Across all held-out user-labeled emails from warm familiar senders,
> kNN-only prediction agreement with user labels ≥ 85%.

With dynamic categories, the gate is refined:
- **Warm familiar sender** = ≥10 emails in last 90 days AND ≥3 user-labeled examples in kNN store under the same (leaf) category.
- **Agreement** = predicted leaf category == labeled leaf category.
- **Stratification** = report agreement per (root, branch, leaf) level; if root-level agreement is ≥85% but leaf is <85%, that's a "right neighborhood, wrong leaf" failure — Phase 1 is conditionally complete with leaf-refinement as a follow-on routine.

The gate is measured by a re-spike script (sibling to
`phase1_spike_classify.py`) that walks `email.db` for warm familiar
senders and the kNN store for seeded examples.

## Alternatives considered

1. **Keep `categories.yaml` static; let users edit it manually.**
   The original canonical-doc posture. Rejected for the reasons in
   Context: the user has 6,000 emails the YAML author has never seen;
   guessing categories upfront is the wrong shape of work.

2. **paia's exact subcategory schema, ported.** paia used a fixed
   subcategory list per category. Rejected because the user
   explicitly said paia still needed tweaks; the design lesson is
   "don't lock the schema."

3. **k-means clustering with predeclared k.** Rejected for HDBSCAN's
   advantages: variable cluster sizes, no need to guess k, native
   "noise" support for emails that don't fit anywhere. k-means would
   force the user to guess k upfront — the same problem we're trying
   to avoid.

4. **One Chroma collection per user-defined category.** Cleanly maps
   "labeled example for category X → collection X." Rejected
   because category renames/merges/splits become collection-level
   migrations; one collection with metadata is simpler.

5. **Per-account category taxonomies.** Cleanly separates account
   semantics but forces cross-account queries through reconciliation.
   Rejected as over-engineering for the typical case
   (same-conceptual-categories across accounts).

6. **Auto-discovery on first sweep.** Triggers bootstrap automatically
   when an account is connected for the first time. Rejected as
   surprising — bootstrap involves clustering + LLM calls + user
   review; user should opt-in explicitly.

7. **Deeper hierarchy (4+ levels).** Cleaner taxonomy for complex
   inboxes. Rejected for now: three levels is the sweet spot between
   expressiveness and review burden. Can extend later by relaxing
   the CHECK and migrating the `level` column.

8. **UUID primary keys for categories.** Rename-safe but loses the
   path-style readability that helps debugging. Slug-style is the
   right MVP choice; UUIDs can be added as a stable internal id
   alongside the slug if rename collisions become painful.

## Consequences

**Positive:**
- Categories grow from real corpus data, not author guesswork. The
  user's 6,000-email inbox shapes the taxonomy; my 5-category guess
  from a 50-email sample does not.
- The same table supports email + finance + future domain
  taxonomies via the `type` column — one schema, multiple consumers.
- Three-level hierarchy lets the brief render at any roll-up level
  ("$1,200 in `finance.banking` last week" or
  "$800 in `finance.banking.transaction-statement`").
- The self-learning loop captures user intent over time; corrections
  drive both the kNN store AND category-set evolution.
- Migration paths for category renames/merges/splits are first-class
  via `category_history`; no broken references when the user
  refactors their taxonomy.

**Negative:**
- More moving parts. The bootstrap process is itself a non-trivial
  pipeline (embed + cluster + Tier 3 + interactive review). Bugs
  here are user-facing.
- The dynamic schema makes integration tests harder: any test that
  classifies an email must first construct an in-memory taxonomy.
  Mitigation: a `seed_test_taxonomy(store)` test helper.
- Renames cause kNN-store migrations. If the migration is buggy,
  labeled examples could lose their connection to categories. The
  migration helper must be transactional + idempotent + tested.
- The bootstrap CLI requires a Tier-3-local model. Users without
  one must connect cloud Tier 3 or accept the bootstrap will only
  cluster, not name. Documented behavior.

**Reversibility:** moderate. Rolling back to static `categories.yaml`
is feasible but loses any discovered categories + user corrections —
not actually a useful rollback. Forward changes (deeper hierarchy,
per-account overrides, alternative clustering algorithms) are all
additive.

## Implementation notes

These are decisions that flow from the design and don't need their
own ADR:

- **Embedding model**: `sentence-transformers/all-MiniLM-L6-v2` for
  MVP. Faster than bge-base; quality good enough for clustering at
  this scale. Swap in a later ADR if real-world quality demands it.
- **Bootstrap trigger**: opt-in CLI only. No auto-fire.
- **Clustering library**: `hdbscan` (PyPI package). Add to
  `pyproject.toml` when Phase 1 Track 1E lands.
- **Chroma kNN collection**: `email_labeled_examples`. Metadata per
  point includes `category_id`, `email_account_id`, `sender_domain`,
  `labeled_at`. Soft-delete by metadata flag rather than physical
  delete (cheaper migration on category rename).
- **kNN confidence**: top-5 cosine vote; confidence = mean cosine
  similarity of agreeing neighbors. Threshold for "confident" =
  0.65 (tunable in a future ADR after Phase 1 measurement).
- **Discovery batch size**: `--sample <N>` flag lets the user
  bootstrap from a sample (e.g., the first 1,000 emails) for a
  quick first pass; subsequent runs cover the rest.

## Related ADRs

- Canonical doc §3.1 — supersedes the static-categories posture.
  A small doc fix to §3.1 cross-references this ADR in the same
  commit.
- ADR-0007 (multi-currency rules) — sibling design constraint;
  the `type` column convention here will extend to finance taxonomy
  in Phase 3.
- ADR-0016 (email accounts registry) — bootstrap discovery is
  per-account, keyed by `email_accounts.id`.
- Phase 1 spike writeup — the empirical justification for this
  ADR's existence.
- A future ADR will document the embedding-model upgrade if
  MiniLM proves insufficient.
- A future ADR will document the kNN-store rename/merge migration
  helper once it's been exercised against real corpus mutations.
