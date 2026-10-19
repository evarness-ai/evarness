# ADR-0026: email-search FTS shape

Status: Accepted (2026-05-26)
Context: Personal-assistant upgrade Phase 1 Track 1M. Parents: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md) (dynamic categories — search filter), [ADR-0019](./0019-categories-store-and-accept-cli-shape.md) (categories store — `--category` filter joins here).

## Context

Phase 1 has shipped 2000+ emails in `data/email.db` with classification + wiki ingestion, but **there's no surface for the user to actually find an email by content**. Today the only path is opening Gmail in a browser, which defeats the local-first design. Track 1M adds `iris email search "HDFC statement"` returning matching rows in milliseconds with snippet highlights.

SQLite has FTS5 built-in — a virtual table that maintains an inverted index over text columns, supports phrase/AND/OR/prefix queries via the MATCH operator, and provides BM25 ranking + snippet highlighting. No new deps, no embedding service, no separate database.

Seven non-obvious implementation calls to lock:

1. Which columns to index.
2. FTS5 vs LIKE.
3. How existing rows get indexed (migration).
4. Sync strategy when `emails` changes.
5. Ranking + result shape.
6. Filter set on the CLI.
7. Query syntax exposed to the user.

## Decision

Seven implementation decisions:

1. **Indexed columns: `subject`, `from_address`, `snippet`.** The user's mental model when searching: "I remember the subject said something about X" or "it was from gap.com." Body is NOT indexed because it's not persisted (canonical §3.1 — only snippet survives). `classified_category` is exposed as a structured FILTER, not an indexed column — typing `category:shopping` into the query box mixes the two namespaces. Rejected: indexing `classified_category` for free-text search. Rejected: re-fetching bodies from Gmail at index time (quota cost; bodies were explicitly NOT cached for a reason).

2. **FTS5 contentless table** (`CREATE VIRTUAL TABLE emails_fts USING fts5(subject, from_address, snippet, content='emails', content_rowid='rowid')`). Contentless means FTS stores its index but reads source columns from `emails` on query — no duplicate storage, automatic consistency on the joined columns. BM25 ranking built-in. Rejected: plain `LIKE %query%` — degrades past ~5k rows; we already have 2000. Rejected: a standalone FTS table that duplicates content — wastes ~30% disk on a 10k-email DB and creates an extra sync surface.

3. **Migration: `_apply_migrations` creates the FTS table + rebuilds on first encounter.** Detection: `PRAGMA table_info(emails_fts)` returns empty if the virtual table is missing. On miss, `CREATE VIRTUAL TABLE ...` then `INSERT INTO emails_fts(emails_fts) VALUES('rebuild')` to populate from the existing `emails` rows. Idempotent — second `ensure_schema` call sees the table and skips. Rejected: a separate `iris email reindex-fts` CLI as the only path (force users to remember; brittle).

4. **Sync via SQLite triggers, not Python-side.** Three triggers — `AFTER INSERT`, `AFTER UPDATE OF subject,from_address,snippet`, `AFTER DELETE` — keep `emails_fts` in lockstep with `emails`. Declarative; no `EmailStore.upsert` coordination needed; new call sites (future `EmailStore.upsert_bulk` etc.) get sync for free. Rejected: per-row Python coordination — easy to miss a new call site and silently drop search hits.

5. **BM25 rank + FTS5 `snippet()` highlight** in results. Result shape: `SearchHit(id, subject, from_address, from_domain, received_at, classified_category, snippet_highlighted, rank)` where `snippet_highlighted` carries the FTS5 snippet with `<mark>...</mark>` tags around the matched terms (mark tag, not ANSI, so the Rich renderer can style it). `rank` is BM25 negative (smaller = better) and exposed for debug; users see results sorted by rank descending implicitly. Rejected: cosine similarity ranking — FTS5 doesn't carry embeddings; we already pay for kNN over centroids elsewhere (Track 1G).

6. **Filters: `--account`, `--category`, `--since`, `--limit`.** All optional; compose with the FTS MATCH at SQL level (the search SQL is `WHERE emails_fts MATCH ? AND account_id = ? AND classified_category = ? AND received_at >= ? ORDER BY rank LIMIT ?`). The `--category` filter accepts a path prefix — e.g. `--category email/shopping/` matches the whole `shopping` root. Rejected: filter-only mode without a query (degenerate; just SELECT). Rejected: prefix-match via FTS5 syntax in `--category` — keeping it a structured filter is more debuggable than encoding into the MATCH clause.

7. **User query → raw FTS5 MATCH.** Phrase queries (`"acme apparel"`), boolean operators (`hdfc AND statement`, `shopmart OR target`), prefix (`stat*`), and column qualifiers (`subject:statement`) all work because we hand the query unmodified to MATCH. Reserved characters that confuse FTS5 (mostly `:` and `.`) — defer to FTS5's "raw" tokenizer behavior; if users hit issues, document the escape rules in the CLI help. Rejected: a bag-of-words "safe" mode — limits power for a technical user; documenting the syntax in `--help` is the right cost.

## Schema

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject,
    from_address,
    snippet,
    content='emails',
    content_rowid='rowid'
);

-- Backfill on first creation (run only when migration detects the virtual
-- table didn't exist; idempotent if accidentally re-run — FTS5 'rebuild'
-- truncates + repopulates).
INSERT INTO emails_fts(emails_fts) VALUES('rebuild');

-- Triggers keep emails_fts in sync.
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
  INSERT INTO emails_fts(rowid, subject, from_address, snippet)
    VALUES (new.rowid, new.subject, new.from_address, new.snippet);
END;

CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE OF subject, from_address, snippet ON emails BEGIN
  INSERT INTO emails_fts(emails_fts, rowid, subject, from_address, snippet)
    VALUES('delete', old.rowid, old.subject, old.from_address, old.snippet);
  INSERT INTO emails_fts(rowid, subject, from_address, snippet)
    VALUES (new.rowid, new.subject, new.from_address, new.snippet);
END;

CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
  INSERT INTO emails_fts(emails_fts, rowid, subject, from_address, snippet)
    VALUES('delete', old.rowid, old.subject, old.from_address, old.snippet);
END;
```

## CLI surface

```
iris email search <query> [--account <slug>] [--category <path-prefix>]
                          [--since <iso>] [--limit N]
```

Query syntax (passthrough to FTS5 MATCH):
- Bare word: `hdfc`
- Phrase: `"acme apparel"`
- Boolean: `hdfc AND statement`, `shopmart OR target`, `gap NOT factory`
- Prefix: `stat*`
- Column qualifier: `from_address:hdfcbank.com`

Output: Rich table with rank, received_at (truncated to date), from, classified_category (if any), and the FTS5-highlighted snippet (Rich style on the `<mark>` regions).

Exit codes: 0 ok (even when 0 hits — empty result is valid); 2 query parse error from FTS5.

## Consequences

- The `emails_fts` virtual table grows ~30% of `emails` table size in indexing overhead. For 10k emails ~= a few MB. Acceptable.
- Triggers fire on every `emails` mutation. Backfill cost is dominated by index size: 2000 emails takes <2s on M4 Max.
- The contentless mode means FTS reads from `emails` at query time — vacuum-safe but slightly slower than a duplicated content table. Worth the consistency win.
- `--category` accepts a prefix (`email/shopping/` matches all shopping leaves) which is the natural mental model since paths are hierarchical. The CLI passes the prefix through `LIKE 'email/shopping/%'` — guard against SQL injection by escaping `%` and `_` in user input.
- Track 1I (Chroma kNN over labeled examples) and 1M are independent — Chroma is for classification, FTS is for retrieval. No overlap; both can land in any order.
- Future: a `iris email search --semantic` flag could route the query through the embedding store for fuzzier matches ("emails about my taxes" without "tax" in the subject). Out of scope for v1 but easy to add — same CLI, different backend.

## Alternatives considered

- **ChromaDB semantic search over email envelopes.** Heavier (embedding per email at index time), good for "emails about banking" without keyword overlap. Rejected for v1: most user searches are keyword-shaped ("HDFC", "Acme Apparel"), FTS5 gives 95% of the value for 5% of the complexity. Add later as `--semantic`.
- **Ripgrep over per-email markdown dumps.** Tempting because it's filesystem-native. Rejected: requires materializing one file per email, drift between SQLite + filesystem is the worst kind of drift bug.
- **Index `classified_category` as an FTS column instead of a filter.** Lets users type `shopping` to find all shopping emails by free-text. Rejected: the hierarchy is structured for a reason (ADR-0017); searching it as text reverses that. `--category email/shopping/` is the right surface.
- **Skip BM25; sort by `received_at DESC`.** Newest first is the email default. Rejected: BM25 surfaces _relevance_ which is the actual user need when searching; the `--since` filter handles recency for users who want chronological.
- **Pre-tokenize stopwords / Porter stemmer.** FTS5's default tokenizer ("simple") already handles common English; stemming would catch `statements`/`statement` but adds index complexity and surfaces noise on short queries. Reverse-engineerable from `tokenize='porter'` in the virtual table DDL — defer until users report misses.
