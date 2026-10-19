# ADR-0021: `email-triage` skill — implementation shape

Status: Accepted (2026-05-25)

**Status pass (2026-10-18):** superseded by ADR-0022. per-email triage is pure kNN with a confidence gate; the hybrid LLM pick survives only as the batch path (`email_workflows/triage.py`)

Context: Personal-assistant upgrade Phase 1 Track 1G. Parent: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md) (dynamic categories). Predecessor: [ADR-0019](./0019-categories-store-and-accept-cli-shape.md) (categories store). Companion: [ADR-0018](./0018-email-bootstrap-categories-cli-shape.md) §5 (`LlamaServerClient` transitional shim).

## Context

Tracks 1E.2 and 1F shipped the "discover + persist taxonomy" half of email classification: `bootstrap-categories` produces a per-account `proposals.jsonl`, `accept-categories` persists accepted proposals into `data/iris.db.categories`. Track 1G is the first track where the taxonomy does work — new mail arriving via the heartbeat sweep (Track 1D, fires `email.new_arrived`) is classified against the accepted categories.

The originally-scoped order for Phase 1 was 1E → 1F → 1G (Tier-3-only baseline) → 1H (tier-router wiring) → 1I (Chroma kNN store) → 1L (kNN-gate measurement). 1G's design choice for *classification strategy* was reopened: pure Tier-3 baseline, pure kNN, or hybrid kNN-then-LLM. The session picked **hybrid** because going pure-LLM in 1G and adding kNN later in 1I would mean re-architecting; the hybrid shape lets 1I add Chroma underneath without changing the triage call site.

Seven non-obvious implementation calls to lock:

1. Classification strategy (pure-LLM vs pure-kNN vs hybrid).
2. Where category centroids come from (proposal contract change vs lazy compute vs backfill script).
3. Where the classification result lives (existing emails columns vs new table).
4. How triage is triggered (event subscription vs heartbeat poll vs both).
5. Where the LLM client comes from (reuse `LlamaServerClient` vs duplicate vs promote now).
6. How confidence is computed (LLM self-report vs centroid distance vs combined).
7. What happens on per-email classification failure.

## Decision

Seven implementation decisions:

1. **Hybrid: kNN top-3 from centroids → Tier-3-local picks among them.** Embed the incoming email (subject + from + from_domain + snippet) with the same MiniLM-L6-v2 used by Track 1E.2's discovery. Compute cosine similarity to every active category's centroid for the account; take the top 3 candidates. Hand those 3 paths plus the email envelope to `LlamaServerClient` with a constrained prompt: "Pick exactly one of these three paths." The LLM's job is local choice between similar options, not free-form taxonomy — that posture is much smaller than the discovery-time task, so token cost is low (~150 tokens/call) and prompt engineering risk is low. Rejected: pure-LLM with all 50 paths in the prompt (works but doesn't establish the kNN signal Track 1I needs); pure-kNN (no LLM at all — fragile in cases where centroids are close).

2. **Lazy centroid computation from `proposals.jsonl.member_ids`.** Triage at startup reads the per-account `proposals.jsonl`, walks the `member_ids` of every active category, re-embeds those members via `embed_corpus`, computes the centroid as their mean, caches in-memory keyed by category path. Cost: ~50 categories × 8-10 members each = ~500 embedding calls ≈ 1s on M4 Max, paid once per triage process lifetime. Rationale: matches the existing 47 accepted categories on the real iris.db without a backfill; avoids adding a `centroid` column to `categories` or a `centroid` field to `CategoryProposal` (schema stays narrow per ADR-0019 §6). Rejected: contract change to `CategoryProposal` (would force re-bootstrap to backfill existing categories); separate `recompute-centroids` CLI (adds a fifth `iris email` subcommand for a one-shot tool — not load-bearing).

3. **Reuse existing `emails` columns.** `classified_category` (TEXT, the category path), `classified_confidence` (REAL ∈ [0,1]), `classified_at` (TEXT, ISO timestamp) — already present in the Track 1B schema. Add `EmailStore.mark_classified(message_id, category_path, confidence)` if not present. Rejected: new `email_classifications` table (1:1 join, premature normalization for the single-classifier case).

4. **Both auto-fire (event subscriber) AND manual CLI.** The classifier handler subscribes to `email.new_arrived` from the sweep heartbeat — when the 10-min sweep returns new IDs, the handler classifies them in-process. Plus a `iris email triage --account <slug> --limit N` CLI for manual classification (validation, debugging, catching up on a backlog from when the heartbeat was off). Rejected: pure heartbeat-driven (would conflict with the event-bus pattern established by Tracks 1C-1D); event-only (no recovery path if the runtime was off when mail arrived).

5. **Reuse `LlamaServerClient` from `iris_harness.email.discovery`.** Mark the usage `# ADR-0018 §5 transitional` where the classifier imports it. Track 1H retires the shim by wiring Tier-3-local into `tier_router.get_llm_config("classify")`; at that point the classifier rebinds. Rejected: duplicate the client (worse); promote it to `iris_harness.llm.local` now (adds a refactor that 1H is the right home for).

6. **Confidence = cohesion of chosen category × LLM-of-3 reliability prior.** Specifically: `confidence = chosen.cohesion * 0.9` if the LLM picked one of the top-3 cleanly, else `0.5` if the LLM's choice didn't match any of the 3 candidates (fallback to the kNN top-1). The `0.9` reliability prior reflects that an LLM picking-1-of-3 is more reliable than free-form generation; the cohesion factor down-weights low-quality clusters. This is a Track 1G heuristic — Track 1J's self-learning loop will replace it with a learned confidence model. Rejected: pure LLM self-report (already proven inflated per ADR-0018 §3); pure centroid distance (doesn't capture LLM uncertainty).

7. **Per-email soft-fail.** On any failure (LLM unreachable, embedding error, JSONL missing) the row's `classified_category` stays NULL and `classified_at` stays NULL — the row is "unclassified" and will be picked up by a future `iris email triage` retry. Log the error at WARN level with the message id. The event subscriber catches exceptions per-email; one bad email doesn't break the batch. Rejected: hard-fail (a transient llama-server hiccup would silently break the heartbeat).

## Implementation outline

```
src/iris_harness/email/
  triage.py                   # NEW
    EmailTriageClassifier
      __init__(workspace_dir, db_path, naming_client, embed_model)
      load_categories()        # reads proposals.jsonl, lazy-computes centroids
      classify(email)          # embed → kNN top-3 → LLM picks → confidence
      classify_batch(account_id, limit, fetch_unclassified=True)
    handle_new_arrived_event(payload)   # event subscriber thin wrapper
  events.py                    # ADD
    EMAIL_CLASSIFIED = "email.classified"
    EmailClassifiedPayload(id, account_id, path, confidence, classifier)
  store.py                     # ADD
    mark_classified(message_id, category_path, confidence)

config/skills/email/email-triage/
  manifest.yaml
  tools.py

src/iris_harness/main.py               # ADD: iris email triage CLI

src/iris_harness/runtime/bootstrap.py  # ADD: subscribe handler to email.new_arrived
                               # (lazy classifier construction to keep
                               # iris --help out of MiniLM cold-load)
```

## Consequences

- The classifier carries one transitive runtime dependency on `proposals.jsonl` existing on disk. If the user deletes the file, triage cannot compute centroids and falls back to NULL for every email. The error path logs WARN with a "run iris email bootstrap-categories first" hint. Track 1F's accept-categories does NOT delete the JSONL after persisting; this contract continues.
- The centroid cache is per-process. Heartbeat subscribers in one runtime use one cache; the manual `iris email triage` CLI builds its own. Acceptable for Phase 1; pulls toward a shared cache when the runtime grows multiple classification consumers.
- Track 1I's Chroma store is unblocked: when it lands, the kNN step in `triage.py` swaps from the in-memory centroid cache to a Chroma query over individual labeled email embeddings (much richer signal than centroid-only). The triage call site doesn't change.
- The confidence formula is intentionally a heuristic that Track 1J will replace. Documented in `triage.py` with an `# ADR-0021 §6 — Track 1J replaces this` comment so the dependency is greppable.
- Per-email soft-fail means unclassified rows accumulate during outages. A future `iris email triage --account <slug>` invocation with no `--limit` will reprocess them. This is fine for Phase 1; if it becomes a perf problem, add a `--reclassify-unclassified-only` flag.
- Exit codes for `iris email triage`: 0 success, 2 categories file missing, 3 no unclassified emails to process, 4 llama-server unreachable. Matches the pattern from ADR-0019 §5.

## Alternatives considered

- **Skip the LLM entirely; classify by nearest centroid.** Fast (~10ms/email, no llama-server). Rejected: cohesion-weighted kNN alone gets the close-but-different cases wrong (e.g. shopping.apparel.gap vs shopping.apparel.dsw look similar in embedding space if the email is generic enough — LLM disambiguation pulls quality up). Worth revisiting in 1L's kNN-gate measurement if the LLM step proves unnecessary.
- **Use Tier-2 cloud (Claude) for the picker step.** Faster (~300ms vs ~1.5s), no llama-server dependency. Rejected: email content is sensitivity-high per ADR-0002. Tier-2 cloud is for sensitivity-low only.
- **Subscribe to email.new_arrived via the heartbeat scheduler instead of the eventbus.** Would centralize scheduling but breaks the event-bus convention from ADR-0013. Subscribers per topic stays the right pattern.
- **Land Track 1H tier-router integration as part of 1G.** Tempting — would remove the `LlamaServerClient` shim's last call site. Rejected on scope: 1H needs `config/llm_tiers.yaml` schema changes and three downstream consumers (1G + 1J + 1E.2's namer) that all want it landed atomically.
- **Skip the manual `iris email triage` CLI and rely solely on the heartbeat subscription.** Cheaper to ship. Rejected: validation needs a synchronous trigger; otherwise the only way to test classification quality is to wait 10 minutes. The CLI is ~30 LOC.
- **Compute centroids at accept-time and persist into `categories.metadata`.** Cleanest architecturally — categories table self-contained. Rejected for 1G: existing 47 accepted categories on the real iris.db have no centroids; would require either a re-bootstrap (loses the user's Path A airline edit unless we add JSONL retention) or a backfill CLI. Lazy compute from JSONL is the smaller delta and the JSONL is already designated as the durable record of cluster composition (ADR-0019 §6).
