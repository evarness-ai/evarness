# ADR-0071: Semantic email retrieval + dues-from-notification extraction

Status: Accepted (2026-06-21) — slices 1-3 implemented on

**Status pass (2026-10-18):** the decision holds; the code now lives in `evarness_personal/email/semantic_index.py`, `agent_tools.py`. the email half is here and ADR-0121 turned the flag on by default and amended the dues part; the dues extraction itself is in the private finance tree

`fix/inbox-digest-and-chat-history`; flag-gated, off by default
(`IRIS_EMAIL_SEMANTIC_SEARCH`, `IRIS_FINANCE_DUES_FROM_EMAIL`). Slice 2 collapsed the
lexical/semantic split into one hybrid `search_inbox` (RRF) rather than two tools.
Follows from `docs/issues/0006` (retrieval decision),
`0007` (finance dues branch), `0008` (daily ingest heartbeat). Builds on ADR-0026
(PDF-from-email finance ingestion; bodies not persisted) and ADR-0007 (multi-currency,
no implicit FX). The judge-gated feedback/clarify loop (issue 0006 decision #2) is a
**separate** ADR and out of scope here.

Context: harness testing exposed two linked retrieval gaps.

1. **Email search is lexical.** `search_inbox` hands the query straight to SQLite
   FTS5 `MATCH` over **subject + sender + snippet** (bodies aren't stored, ADR-0026
   §3.1). A conceptual ask — "any pending payments?" — ANDs the literal tokens and
   misses the mail that actually matters ("statement ready", "minimum payment due",
   "autopay"). The issue 0006 quick win (OR-broaden + offer) softened the dead-end
   but does not *understand* the ask.

2. **Card/bank dues are invisible.** "What do I owe" is answered from
   `FinanceStore` bills + statement `amount_due` (ADR-0007 dues branch, issue 0007).
   But statements only enter the store via the **PDF-required** ingest pipeline
   (ADR-0026 / issue 0008): an email must carry an attachable PDF. The user's real
   card/bank mail is **notification-only** ("your statement is ready — log in",
   "AutoPay reminder") with the amount and due date **in the email text, not a PDF**.
   A re-ingest after fixing registry domains detected 92 financial emails and
   produced **0 statements** — confirming the structural gap.

Assets already in the repo to build on, not reinvent:

- **An embedder.** `iris_harness.email.discovery.embed_corpus` — sentence-transformers
  MiniLM-L6-v2, 384-dim, L2-normalized, cached singleton — already embeds
  `sender + subject + snippet` for triage/kNN clustering. Query and document
  vectors will share it (cosine = dot product on normalized vectors).
- **A vector-store pattern.** `iris_harness.memory.semantic_index.SemanticIndex` uses
  `chromadb.PersistentClient` with per-purpose collections.
- **A dues surface.** The issue 0007 dues digest already groups owed amounts by
  currency and flags overdue — it just needs more rows to show.
- **On-demand body fetch.** `iris_harness.email.gmail_fetch.fetch_message_body` already
  fetches one email's full body live (used by `read_email`) — no persistence.

## Decision (proposed)

Two **composable** capabilities that share the embedder. Both are LOCAL-only and
preserve ADR-0026 (no email bodies persisted) — see Privacy below.

### A. Semantic email retrieval (snippet-only index)

1. **Persisted vector index.** A ChromaDB `PersistentClient` at `data/email_semantic/`,
   collection `email_snippets`, one record per message: precomputed `embed_corpus`
   vector over the **same** `sender + subject + snippet` text triage uses, keyed by
   message id, metadata `{account_id, received_at, classified_category}`. **No body
   text stored** — vectors + ids + minimal metadata only.
2. **Backfill + incremental.** A one-time backfill over the existing `EmailStore`
   (~3.5k messages; MiniLM is cheap and local), then an incremental upsert hook in
   the email-sweep / `EmailStore.upsert` path so new mail is indexed as it arrives.
3. **`semantic_search` tool** for the email ReAct loop: embed the query → cosine
   top-k → resolve to `SearchHit`s. Keep lexical `search_inbox` for exact
   sender/keyword/date asks; tool descriptions steer the model (conceptual →
   semantic, exact term/sender → lexical). A later slice may fuse the two
   (reciprocal-rank fusion) behind one tool.
4. **Index scope = snippet-only for v1 (no ADR-0026 amendment).** Gmail snippets
   already carry the lead of the body, where "payment due / statement ready"
   normally sits. For details below the fold, the loop can `read_email` (live body
   fetch) on the **top-k** semantic hits — recall of body content **without
   persisting bodies**. Persisting/embedding full bodies is explicitly deferred.

### B. Dues-from-notification extraction

1. **Candidate detection.** For emails that are financial (registry sender-domain
   match or learned `Finance*` category) and look like a due/statement notification
   (subject/snippet keywords: statement, payment due, minimum due, autopay, amount
   due) but carry **no PDF**, run a dues-extraction pass.
2. **Grounded extraction.** Use a local tier-2/3 LLM with a **strict schema**
   (`amount_due`, `currency`, `due_date`, `issuer`) over snippet + on-demand body
   text; never invent figures; low-confidence → skip. Currency comes from the
   matched institution (ADR-0007), never guessed.
3. **Derived-due record.** Persist a lightweight "derived due" (a `Statement` with
   `amount_due`/`due_date` and **no `file_ref`**, or a dedicated `derived_due`
   row) tagged `source=email_notification` with provenance = message id, so it's
   auditable and **deduped to the latest per account/card** (mirrors the issue 0007
   latest-statement-per-account rule). These rows flow into the existing dues
   digest — that is what finally populates "pending payments" for cards.
4. **Scheduled.** The `finance_ingest_tick` heartbeat (issue 0008) gains this second
   pass alongside PDF-statement ingest, so dues refresh daily across both inboxes.

### Flags + rollout (consistent with the repo's opt-in posture)

`IRIS_EMAIL_SEMANTIC_SEARCH` and `IRIS_FINANCE_DUES_FROM_EMAIL`, **off by default**
until validated on real mail; flip per the usual preflight. The `semantic_search`
tool is read-only over local data; extraction is gated behind its flag.

## Privacy (load-bearing, per the user's "never commit emails" rule)

- The vector index stores **embeddings + ids + minimal metadata**, never raw bodies
  or snippets; it lives under `data/` (gitignored) and never egresses.
- Dues extraction runs on a **local** tier; email text never leaves the device.
  Bodies are fetched on demand and **not persisted** — ADR-0026 §3.1 stands.
- Derived dues store the amount/date/issuer + a message-id provenance pointer, not
  the email body. `data/*.db` and `data/email_semantic/` stay gitignored.

## Alternatives considered

- **A. Lexical synonym expansion only** (the issue 0006 quick win). Shipped; cheap;
  but a fixed synonym list isn't semantic and won't generalize. Kept as the
  no-embeddings fallback, not the answer.
- **B. Persist + embed full bodies.** Best raw recall, rejected for v1: reverses
  ADR-0026, enlarges the personal-data footprint, and the snippet + on-demand-fetch
  path recovers most of the value without storing bodies.
- **C. Portal / API statement fetch** (log in to each issuer, Plaid, scraping).
  Rejected: ADR-0006 already says no Plaid/scraping in core; credential risk and
  fragility. Notification-text extraction reuses mail we already receive.
- **D. Chroma's default embedder.** Rejected: query and document vectors must match,
  and MiniLM is already loaded for triage — reuse it.
- **E. One monolithic "finance email" pipeline.** Rejected: semantic retrieval is
  general (not finance-specific) and dues extraction is finance-specific; keeping
  them as two capabilities sharing the embedder is cleaner and independently useful.

## Consequences

- **Positive:** conceptual email asks work; card/bank "pending payments" finally
  populate from notifications; both reuse existing infra; privacy posture preserved;
  daily heartbeat keeps it fresh across the INR + USD inboxes.
- **Negative / cost:** a new persisted index (disk + an incremental-index hook that
  must stay cheap — batch, lazy model load); a one-time backfill; per-notification
  LLM cost on the dues pass (gated, daily, candidates only); new derived-due
  provenance + dedup logic that must not double-count against PDF statements.
- **Reversibility:** high. Both are flag-gated and additive; the vector dir and
  derived-due rows can be dropped to revert. Lexical `search_inbox` and the PDF
  ingest path are untouched.

## Implementation status

1. **DONE** — Email semantic index (`iris_harness.email.semantic_index.EmailSemanticIndex`,
   dedicated `data/email_semantic/` Chroma collection, `embed_corpus` reuse, id-only
   storage), `iris email semantic-index` backfill CLI, `EMAIL_NEW_ARRIVED` incremental
   subscriber, and the `semantic_search` tool (gated by `IRIS_EMAIL_SEMANTIC_SEARCH`).
   Live-verified: backfilled 3,586 messages; "pending payments / bills due" surfaced
   real bill mail by meaning.
2. **DONE** — Hybrid retrieval: `rrf_fuse` + `hybrid_search` (`iris_harness.email.semantic_index`)
   fuse FTS5 + vector recall via reciprocal-rank fusion. `search_inbox` uses it when the
   flag is on; the standalone `semantic_search` tool was removed (subsumed) so the model
   never chooses lexical vs semantic — one tool, steered by its description.
3. **DONE** — Dues-from-notification (`iris_harness.finance.dues_extract`): candidate gate →
   grounded strict-JSON extraction → `DerivedDue` rows (own table, provenance +
   idempotent dedup) merged into the issue 0007 dues digest. Wired into
   `finance_ingest_tick`/`run_scheduled_ingest` behind `IRIS_FINANCE_DUES_FROM_EMAIL`.
4. **Partial** — pass 3 wired into the daily heartbeat + opt-in flags shipped; a
   fixture-inbox e2e + an enable preflight remain.

## Related ADRs

- ADR-0026 (PDF-from-email finance ingestion; bodies not persisted) — preserved.
- ADR-0007 (multi-currency, no implicit FX) — currency source for derived dues.
- ADR-0060/0061 (finance source-of-truth, document storage policy) — derived dues
  must respect supersession + retention.
- Issue 0006 decision #2 (judge-gated, optional feedback/clarify loop) — a separate
  ADR, complementary: it decides *when to ask*; this ADR decides *what to retrieve*.
