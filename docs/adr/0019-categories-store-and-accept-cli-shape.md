# ADR-0019: `categories` data model + `iris email accept-categories` CLI — implementation shape

Status: Accepted (2026-05-25)

**Status pass (2026-10-18):** the decision holds; the code now lives in `evarness_personal/email/category_store.py`. Category, CategoryStore and categories_history live in the email record, not a generic data module

Context: Personal-assistant upgrade Phase 1 Track 1F. Parent: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md) (dynamic hierarchical categories). Predecessor: [ADR-0018](./0018-email-bootstrap-categories-cli-shape.md) (1E.2 — `bootstrap-categories` CLI produces the JSONL this track consumes). Companion: [ADR-0020](./0020-drift-correction-followup.md) (drift-correction follow-up).

## Context

ADR-0017 mandates a cross-cutting `categories` table in `data/iris.db` keyed by a `type` column so finance, calendar, tasks, etc. can reuse the schema. Track 1E.2 ships the discovery half (`iris email bootstrap-categories`) which writes a per-account `proposals.jsonl`. Track 1F adds the persistence half: the table, the `CategoryStore` API, and the `iris email accept-categories` CLI that converts an accepted-and-edited JSONL into rows in `categories`.

Six non-obvious implementation calls had to be locked:
1. Where the `Category` Pydantic contract lives (`iris_harness.skills.contracts.email` vs `iris_harness.data.categories`).
2. Primary key shape (path-style slug vs integer + unique tuple).
3. Edit-history surface (single table vs companion history table).
4. `accept-categories` behavior on re-run (idempotent upsert vs diff-then-confirm).
5. Validation strictness against the JSONL (strict vs lenient).
6. Whether to persist cluster `member_ids` (1F or defer to 1G's kNN store).

## Decision

Six implementation decisions:

1. **Contract lives in `src/iris_harness/data/categories.py`, not `iris_harness.skills.contracts.email`.** The categories table is cross-cutting by ADR-0017 design — finance and calendar will reuse the same schema with `type='finance'` / `type='calendar'`. Putting the Pydantic in `iris_harness.skills.contracts.email` would imply email-specific provenance and would be wrong when the finance track lands. The `CategoryProposal` in `iris_harness.skills.contracts.email` stays where it is (it IS email-specific — a discovered-from-Gmail draft); the persisted `Category` lives generic.

2. **Primary key: path-style slug `<type>/<root>/<branch>/<leaf>`.** Example: `email/shopping/apparel/outlet-brand`. Reasons matching ADR-0016's account-slug rationale: stable across delete-and-re-add cycles, self-documenting in `SELECT *` debugging, easy to grep, and trivial to URL-encode for a future API. Rejected: autoincrement `id` + `UNIQUE (type, root, branch, leaf)`. The path encodes the full identity; an int id buys nothing except shorter foreign-key columns in tables that don't yet exist.

3. **Edit history: companion `categories_history` table.** Schema: `(history_id PK, category_path FK, op TEXT, old_path TEXT NULL, new_path TEXT NULL, edited_at TEXT, source TEXT)`. Every mutation to `categories` records a row. `op` ∈ {`insert`, `update`, `archive`, `rename`}. `source` defaults to `'accept-categories'` but lets Track 1J's self-learning loop tag corrections it makes as `'self-learning'`. Rejected: single-table mutate-in-place — loses the audit trail ADR-0017 §self-learning explicitly relies on. Rejected: soft-delete-only — doesn't capture rename / branch promotion edits that the user-driven correction Path A from ADR-0020 needs to track.

4. **`accept-categories` is idempotent upsert-by-path.** Re-running with the same JSONL inserts new paths, leaves existing paths untouched, and writes nothing to history for no-ops. If a proposal's path *already exists*, the existing row's `active` flag is set to True (in case it was previously archived) but root/branch/leaf are not overwritten — the existing row wins. To rename an existing category, the user must edit the path explicitly via a future `iris email recategorize` CLI (deferred — see ADR-0020). Rejected: a diff-then-prompt mode. It adds a UX surface (yes/no per-row in TTY) that complicates testing and bumps scope; the JSONL itself is the diff (user can review/edit before running accept).

5. **Strict validation against the JSONL.** Every proposal must satisfy: `proposed_root ∈ ALLOWED_ROOTS` (the 14-root list from ADR-0018), `proposed_branch` non-empty, `proposed_leaf` non-empty, all three lowercase, dashes-not-spaces. A single invalid row aborts the entire run with exit code 4 and a Rich error listing every offender — no partial commits. Rejected: lenient mode that skips invalid rows with a warning. The user would think they accepted 50 categories and discover later they got 47, with no clear way to find the missing three. The exit-and-explain UX is cheaper than lossy partial success.

6. **Don't persist `member_ids` in 1F.** The `CategoryProposal.member_ids` field stays in the JSONL but does not become a column on the `categories` table. Track 1G's kNN seed step re-derives the labeled-example map either from the JSONL on disk or by re-reading `email.db` plus a small `category_seed_emails` table. Keeping `categories` small (path + active + timestamps + metadata JSON) means joins stay readable. Rejected: stuffing `member_ids` into a JSON column on `categories`. Rejected: a separate `category_seed_emails` table in 1F. The latter is the right home for membership data when 1G lands but adding it speculatively now violates "don't add abstractions beyond what the task requires."

## Schema

```sql
CREATE TABLE IF NOT EXISTS categories (
    path             TEXT PRIMARY KEY,             -- e.g. 'email/shopping/apparel/outlet-brand'
    type             TEXT NOT NULL,                -- 'email', future: 'finance', 'calendar', …
    root             TEXT NOT NULL,                -- 'shopping'
    branch           TEXT NOT NULL,                -- 'apparel'
    leaf             TEXT NOT NULL,                -- 'outlet-brand'
    account_id       TEXT,                         -- soft FK to email_accounts.id (NULL = cross-account)
    cohesion         REAL,                         -- from CategoryProposal at acceptance time
    sensitivity      TEXT NOT NULL DEFAULT 'low',  -- per ADR-0002; routing implication
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    metadata         TEXT NOT NULL DEFAULT '{}'    -- JSON: discovery params, top_domains, etc.
);

CREATE INDEX IF NOT EXISTS idx_categories_type_active ON categories(type, active);
CREATE INDEX IF NOT EXISTS idx_categories_account ON categories(account_id);

CREATE TABLE IF NOT EXISTS categories_history (
    history_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    category_path    TEXT NOT NULL,                -- FK target may be archived row
    op               TEXT NOT NULL,                -- 'insert', 'update', 'archive', 'rename'
    old_path         TEXT,
    new_path         TEXT,
    payload          TEXT,                         -- JSON snapshot of relevant fields
    edited_at        TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'accept-categories'
);

CREATE INDEX IF NOT EXISTS idx_history_category ON categories_history(category_path);
```

The `sensitivity` column on `categories` is `'low'` by default. ADR-0002's per-category sensitivity mechanism plugs in here when Track 1H wires the tier router — high-sensitivity categories like `finance.banking.*` will be set explicitly via a future CLI flag or YAML overlay.

## Consequences

- Cross-domain reuse is baked in: the finance ingestion track in Phase 3 inserts rows with `type='finance'` and no schema change is required.
- The audit trail is non-optional. Every change to the table writes a row to history; the absence of any history rows means no one ever touched the table. Track 1J can read the history backwards to learn from user corrections.
- The strict-validation choice means users who hand-edit the JSONL must be careful — a typo in a single row aborts the whole accept run. This is the intended forcing function: edit-then-validate-then-commit, never accidentally commit a half-broken taxonomy.
- The `accept-categories` CLI never deletes or renames in 1F. To remove a category the user must wait for `iris email recategorize` or edit the SQLite directly (with the history table capturing the change either way). Deferred per Path A of ADR-0020.
- `CategoryProposal.member_ids` lives in the JSONL on disk forever — it's the lossless record of cluster composition at discovery time. If a user re-runs `bootstrap-categories` later (Track 1J re-cluster cadence), a new JSONL overwrites the old one; the historical membership is gone. Track 1J's design will decide whether that's acceptable or whether to snapshot the JSONL into `categories.metadata` JSON at accept-time.

## Alternatives considered

- **Schema-on-write that rejects unknown roots at the DB level via CHECK constraint.** Tempting because it makes "valid root" a SQLite invariant. Rejected: the ALLOWED_ROOTS list is the application's call (per ADR-0018) and could grow over time as users hit the `other` root and want a new bucket. A CHECK constraint forces schema migrations every time the list grows. Application-level validation is the right layer.
- **A single denormalized `category_path` column with no separate root/branch/leaf columns.** Simpler schema. Rejected: filtering by root for a "show me all my finance categories" query becomes a `LIKE 'email/finance/%'` scan, vs an indexed `WHERE type=? AND root=?` lookup with the split.
- **JSON column for the entire category record.** Schema-flexible. Rejected: SQLite's JSON support is real but typed columns are easier to query and let downstream code rely on column types instead of `json_extract`.
- **A `category_aliases` table that maps user-edited paths to original LLM-proposed paths.** Useful for retro analysis ("what did the model think before I corrected it?"). Rejected for 1F: same information lives in `categories_history.payload` JSON. Re-evaluate if Track 1J needs structured access to corrections.
