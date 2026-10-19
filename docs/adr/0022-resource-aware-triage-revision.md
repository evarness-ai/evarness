# ADR-0022: Resource-aware triage — pure-kNN per email, on-demand LLM batch review

Status: Accepted (2026-05-25) — supersedes ADR-0021 §4 (auto-fire LLM per email via `email.new_arrived` subscriber).
Context: Personal-assistant upgrade Phase 1 Track 1G revision. Parent: [ADR-0021](./0021-email-triage-skill-shape.md). Companion: [ADR-0002](./0002-privacy-aware-tier3-sensitivity-per-category.md) (sensitivity routing).

## Context

Track 1G as shipped invoked Tier-3-local (Qwen3-30B-A3B Q4_K_M) on every email via the `email.new_arrived` event subscriber. The real-Gmail validation classified 20 emails in ~30 seconds — about 1.5 s/email — at a peak memory footprint of ~25 GB and meaningful M4 Max thermal load.

That cost is fine for batch workloads (bootstrap-categories does ~50 calls on first run for an account, re-cluster does the same on user demand) but wrong for **streaming** per-email triage. Every sweep tick processes new mail and would re-pay the cost; the user explicitly wants Tier 3 only invoked when they choose to spin it up.

ADR-0021 §"Alternatives considered" had already noted "Skip the LLM entirely; classify by nearest centroid" was rejected on quality grounds. The current ADR reopens that with a structural change: classify with kNN, **escalate ambiguous cases to a deferred queue**, and let the user invoke Tier 3 (or Tier 2) to drain the queue on demand.

Five non-obvious implementation calls to lock:

1. Classification strategy at sweep time (pure-kNN vs Tier 2 swap vs hybrid).
2. Where the ambiguous-case queue lives (column on emails vs separate table).
3. Confidence gate threshold (the line between "kNN-confident" and "queue for review").
4. Whether the `email.new_arrived` subscriber still classifies or only enqueues.
5. Track 1H's status now that the per-email LLM call is gone.

## Decision

Five implementation decisions:

1. **Pure-kNN at triage time with a confidence-gated queue.** The classifier embeds the incoming email (same MiniLM-L6-v2 via the cached `embed_corpus`), computes cosine similarity to every active category centroid, and takes the top-1. If the gate passes (defined below) the row is classified directly with `triage_state='classified'`. Otherwise the row is queued with `triage_state='pending_review'` and the user drains the queue on demand via `iris email triage-batch`. Zero LLM calls per email. Rejected: pure-Tier-2 swap (still runs an LLM per email — same architectural pattern, just cheaper; doesn't satisfy the "Tier 3 only on demand" constraint and Tier 2 is not significantly cheaper if it stays loaded continuously). Rejected: pure-kNN with no queue (loses information about which cases the LLM should have been asked about).

2. **Queue lives as a column on `emails`.** Add `triage_state TEXT` to the emails table. Values: `NULL` (never triaged), `'classified'` (locked in), `'pending_review'` (kNN-ambiguous, awaiting batch), `'error'` (soft-fail, retry candidate). Indexed for fast `list_pending_review` queries. Rejected: separate `email_triage_queue` table. Adds a join; the queue is fundamentally a state of the email, not a separate entity. Rejected: `triage_state ENUM` (SQLite doesn't have enums; application-side validation via the `mark_classified` / `mark_pending_review` API is sufficient).

3. **Confidence gate: `cos_top1 ≥ 0.50 AND (cos_top1 - cos_top2) ≥ 0.02`.** Both conditions required. `cos_top1` ≥ MIN means the email is meaningfully close to the chosen centroid (not just the least-bad of a poor set). Margin ≥ MIN means top-1 is clearly the winner over the runner-up (no ties between siblings). When the gate passes: `classified_confidence = cos_top1`. When the gate fails: queue with `triage_state='pending_review'`, `classified_category` stays NULL. Rejected: cohesion-relative threshold (harder to reason about across categories with different cohesions; can revisit if uniform thresholds underperform per category in future measurements).

   **Threshold tuning history.** Originally shipped at `(0.70, 0.05)` as a defensible starting point. Tuned to `(0.50, 0.02)` on 2026-05-26 after Track 1L's measurement (in the private project log) against 50 user-labeled emails: the original gate queued 78% of holdout (22% gated at 90.9% accuracy); the new gate classifies 54% at 85.2% accuracy — 2.5× the kNN coverage for a 5.7pp accuracy trade-off, with queued cases still reachable via `iris email triage-batch`. Re-run `iris email knn-gate` to re-tune after future re-clustering passes.

4. **The `email.new_arrived` subscriber still classifies, but with pure-kNN only.** It calls `classify_pure_knn`, persists confident rows, queues ambiguous ones. Useful work continues to happen as mail arrives — the user wakes up to most mail classified, only the ambiguous tail in a queue. Rejected: subscriber just enqueues everything (delays useful classification by the time it takes to manually run a batch).

5. **Track 1H (tier-router wiring for Tier 3) is deferred.** The remaining Tier 3 call sites are explicit user-invoked CLIs (`bootstrap-categories`, `triage-batch`, future re-cluster). `LlamaServerClient` is a fine transitional shim for that — it has clear retirement criteria, no per-email overhead, and the deferral is cheap to reverse later. 1H lands when Track 1J (self-learning) or 1K (wiki ingestion) needs Tier 3 in a way that requires the canonical tier-router surface.

## Behavior changes

`EmailTriageClassifier`:
- `classify(email)` → now calls `classify_pure_knn` by default; the LLM picker path is reachable only via `classify_with_llm(email)` (used internally by the batch CLI).
- `classify_pure_knn(email)` → embeds, computes kNN top-1 + top-2 similarities, checks the gate, returns a `TriageResult`. The `error` field surfaces gate-fail with `"queued: cos_top1=X, margin=Y"` so the user can debug.
- `classify_and_persist(email)` → routes the result. Gated → `mark_classified` with `triage_state='classified'`. Queued → `mark_pending_review`. Error → `triage_state='error'`.
- Event subscriber → unchanged surface; internally uses the new pure-kNN path.

`iris email triage` (existing CLI):
- Default behavior changes from "kNN + LLM picker" to "kNN only". Renders three counts in the summary table: classified / queued / soft-failed.
- New flag `--use-llm` falls back to the original hybrid behavior (still useful for direct user invocation when llama-server is up).

`iris email triage-batch` (new CLI):
- Process the `pending_review` queue for an account.
- `--tier {3,2}` selects the LLM (defaults to 3, llama-server on :8090). `--tier 2` routes through Ollama qwen2.5:7b-instruct (already loaded as Tier 2 — zero spin-up cost). Tier 2 path uses the same OpenAI-compatible API; details land with the implementation commit.
- Per-email picker logic is unchanged from ADR-0021 §1 (top-3 candidates + LLM picks one + confidence = `cos_chosen × 0.9`).

`emails.triage_state` migration:
- Additive `ALTER TABLE emails ADD COLUMN triage_state TEXT`. Existing rows have NULL.
- The 20 emails classified in Track 1G's validation have `classified_category NOT NULL` AND `triage_state IS NULL`. They will NOT be re-classified by the new pure-kNN path because `list_unclassified` continues to filter on `classified_category IS NULL`. No backfill needed; they coexist cleanly with the new state machine.

## Consequences

- **Memory + thermal load drops to baseline at sweep time.** ~50 ms per email for MiniLM encode + ~50-vector dot product. Tier 3's 25 GB peak only reached during explicit user-invoked workflows.
- **Quality is the open question.** Track 1L's kNN-gate measurement is now load-bearing: it measures pure-kNN-only accuracy on the user's labeled holdout set and tunes (0.7, 0.05) thresholds against it. The gate is correct in shape; the numbers are empirically tunable.
- **The pending_review queue grows over time** if the user never runs `triage-batch`. Acceptable — these emails are still reachable via search (Track 1M) and the queue length is a directly visible diagnostic. The CLI could surface a "you have N pending review" hint on `iris email` invocations; not in 1G's scope.
- **`email.classified` event still fires** on the gated-classified path, so subscribers (wiki ingestion in 1K) don't change. Queued emails do NOT emit the event — they're not classified yet. Wiki ingestion sees only confidently-classified mail, which is the right semantics.
- **Track 1H deferral is documented.** `LlamaServerClient` retires when 1J or 1K needs Tier 3 in a way that benefits from the canonical tier-router surface.
- **ADR-0021 §4 ("both auto-fire AND manual CLI") is superseded** by this ADR. The auto-fire still happens but no longer invokes Tier 3 per email. ADR-0021 §1 (hybrid kNN-then-LLM) is preserved as the batch path; the per-email path is purely kNN.

## Alternatives considered

- **Pre-load Tier 2 (qwen2.5:7b-instruct) as the per-email picker.** Would keep the hybrid pipeline at ~500 ms/email and ~5 GB RAM. Rejected: still continuous LLM workload, user explicitly said "only when I choose to spin it up." Also, Tier 2 quality on the picker task is unverified — the cohesion-weighted hybrid only works well if the picker is strong enough to fix the close cases.
- **Skip kNN, classify by `from_domain` regex match.** Faster than embedding (~100 µs vs ~50 ms). Rejected: ADR-0017's "no hardcoded taxonomy" stance applies — domain → category mappings are taxonomy hardcoding by a different file format. Same Path B rejection from ADR-0020.
- **Use ChromaDB at triage time instead of an in-memory centroid array.** Pulls Track 1I forward. Rejected for now: 50 centroids × 384 floats is 75 KB; a numpy matrix multiplication is fine. Chroma adds value when we kNN against individual labeled emails (1I's actual scope), not against centroids.
- **Defer the new column entirely; queue by `classified_category = '__pending__'`.** Encodes state in the existing column. Rejected: confuses downstream consumers that expect `classified_category` to be a real path; the `triage_state` column is the right separation.
- **Run a heartbeat that drains the queue automatically with Tier 2.** Tempting — "background catch-up." Rejected on user request: explicit invocation only. The user knows when the machine has thermal headroom; the system shouldn't guess.
