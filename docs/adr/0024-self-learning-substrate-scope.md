# ADR-0024: Self-learning substrate — Phase 1 scope

Status: Accepted (2026-05-26)
Context: Personal-assistant upgrade Phase 1 Track 1J. Parent: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md) §self-learning. Companions: [ADR-0019](./0019-categories-store-and-accept-cli-shape.md) (`categories_history` audit substrate), [ADR-0020](./0020-drift-correction-followup.md) (drift correction follow-up — Path C "canonical self-learning"), [ADR-0023](./0023-knn-gate-and-holdout-labeling.md) (kNN-gate measurement).

## Context

ADR-0020 Path C named "self-learning correction loop" as the canonical mechanism for systematic LLM drift correction — but framed it through one signal only: **naming corrections** (user edits `proposals.jsonl` before `accept-categories`). Tracks 1G + 1L surfaced a richer set of correction signals that need to be captured for any future learning loop to work:

| Signal source | Captured today? |
|---|---|
| User edits `proposals.jsonl` before accept | No — the diff is lost on accept |
| User labels in `holdout-labels.jsonl` (via `label-holdout`) | Yes (in the JSONL itself) |
| User reclassifies a wrongly-triaged email | No — there is no CLI |
| Triage-batch LLM overrides a pure-kNN result | Indirectly (`classifier` tag changes) |

Three signals are uncaptured. Without them, "self-learning" has nothing to learn from.

Track 1J shipped the **substrate** for capturing the missing signals. It does NOT implement the learning loop itself. Five non-obvious implementation calls had to be locked:

1. Whether to ship the full learning loop or only the data capture half.
2. How the user expresses a correction (CLI shape).
3. Where corrections persist.
4. Whether LLM-vs-kNN classifier disagreements count as corrections.
5. Whether re-running `iris email knn-gate` is automated.

## Decision

Five implementation decisions:

1. **Substrate-only for Phase 1.** Track 1J ships exactly three things: a recategorize CLI, augmented `knn-gate` that can read corrections, and a read-only audit CLI. Online centroid updates, scheduled re-clustering, and namer-fewshot updates are explicitly deferred to a future Phase 2 track. Rationale: a partial learning loop creates more risk than no learning at all — a single wrong correction permanently drifting a centroid is hard to reverse. Capturing correction signal is value-neutral; learning from it is value-direction-bearing and needs explicit design when the signal volume justifies the effort. Rejected: full loop in 1J (scope creep; the signal volume after Phase 1 is ~10 corrections, statistically insignificant for learning).

2. **CLI surface: `iris email recategorize --message-id <id> --to <path>`.** Explicit required path. Single message at a time — bulk corrections via shell scripting if needed. The category path must already exist in `categories` (no auto-create); if the user wants a new category, they run `bootstrap-categories` to discover it OR edit the proposals JSONL. Rejected: a `--reject` flag that just un-classifies (lets triage retry). It's tempting for "I don't know, ask the LLM" but creates a third state in the existing two-state matrix (`triage_state IS NULL` / `'classified'` / `'pending_review'` / `'error'`) which is enough already. Users wanting that effect can manually edit `triage_state` to `'pending_review'` until a future `--queue` flag if the gap proves real.

3. **Corrections persist in `categories_history`.** Per ADR-0019 §3 the table already exists with the right shape:
   - `op = 'update'` (the only existing op that fits — corrections update a row's effective category)
   - `source = 'user-classification-correction'` (NEW source tag; the existing `'accept-categories'` and `'self-learning'` continue to exist)
   - `category_path = <new_path>` (which category received the correction)
   - `payload = {"message_id": "<msg>", "account_id": "<slug>", "old_path": "<previous>", "new_path": "<new>", "previous_classifier": "<tag>"}`
   - `edited_at = <now>`

   Rejected: a new `email_corrections` table. Adds normalization burden when an existing audit table already captures the right operation semantically. Track 1J reads `categories_history WHERE source='user-classification-correction'` rather than introducing a join.

4. **LLM-vs-kNN classifier disagreements are NOT corrections.** When `triage-batch` runs and the LLM picks a different category than pure-kNN would have, that's not user intent; it's a different classifier's opinion. The classifier tag changes (`pure-knn` → `tier3-local-knn`) but no correction row is logged. Only explicit user invocations of `iris email recategorize` create correction rows. Rejected: logging classifier overrides as corrections. The signal would drown the user's actual intent in mechanical reclassification noise.

5. **Re-running `iris email knn-gate` is manual.** No heartbeat-driven re-measurement. Threshold tuning has long blast radius (every email classification afterward changes); user-in-the-loop is the right cadence. A future track can add a scheduled re-tune if it proves valuable. Rejected: monthly heartbeat re-tune (silent threshold drift).

## CLI surface

```
iris email recategorize --account <slug> --message-id <id> --to <path>
  Required:
    --message-id    the email being recategorized
    --to            full path (e.g. email/finance/banking/hdfc-savings)
  Optional:
    --reason        free-form note saved into history payload
  Effects:
    - emails.classified_category = <path>
    - emails.classified_confidence = 1.0 (user assertion is ground truth)
    - emails.classified_at = now
    - emails.triage_state = 'classified'
    - categories_history INSERT with source='user-classification-correction'
  Exit codes:
    0  recategorized
    2  message_id not in email.db
    3  --to path not in categories (and active=1)

iris email corrections list --account <slug> [--since DATE] [--limit N]
  Read-only audit. Renders a Rich table:
    edited_at | message_id | old_path | new_path | reason
  Joins categories_history WHERE source='user-classification-correction'
  with emails to surface a from-line for context.

iris email knn-gate --account <slug> [--include-corrections]
  Existing CLI with new flag. When set, augments the labeled holdout
  with rows derived from `categories_history WHERE source=
  'user-classification-correction'`. Each correction becomes a
  synthetic HoldoutLabel where true_root = root-of(new_path). The
  measurement then has more data without the user labeling more.
```

## Consequences

- Track 1J's value is **future optionality**: the substrate exists, so a Phase 2 track can read corrections + re-cluster + update centroids without first going back to add the capture layer.
- The user has a clear "I disagree with this classification" surface that wasn't there before. Even without learning, that's directly useful — a wrong row gets fixed once instead of staying wrong forever.
- `categories_history` now carries three sources: `accept-categories` (default), `self-learning` (reserved for Phase 2), `user-classification-correction` (new). Schema stays the same; only the value space expands.
- `confidence = 1.0` for user corrections is intentional — it reflects "user asserts this is correct," not a model's certainty. Downstream consumers (wiki ingestion, future scoring) treat user-classifier rows as ground truth.
- `iris email knn-gate --include-corrections` is the bridge to Track 1L: corrections immediately grow the measurement set, so the user sees their corrections influence threshold tuning on the next `knn-gate` run.
- The deferred items are documented forever: ADR-0024 §Decision lists what 1J does NOT do, so future readers know what's still open.

## Alternatives considered

- **Build the full learning loop in 1J.** Tempting because it's the headline feature. Rejected: signal volume after Phase 1 is too low (<10 corrections expected from one user) for any meaningful re-cluster or centroid update. Worse, a single wrong correction would silently drift the system. Phase 2 reads the substrate when the volume justifies the design.
- **Skip the `iris email corrections` audit CLI.** YAGNI. Rejected: corrections that are silently logged but never visible to the user create the worst possible UX — they apply mysterious effects to future classifications. A read-only view is cheap and the right minimum.
- **Use the email.classified event to log corrections.** Reuse existing wiring. Rejected: that event fires on every classification, including auto-fired pure-kNN ones. Adding a "this one was a correction" flag muddles the event contract; better to keep events for "what happened" and use a dedicated row in `categories_history` for "what changed and who changed it."
- **Allow `--to` to specify a non-existent path and auto-create.** Speeds up the user's flow. Rejected: violates ADR-0017's "no out-of-band taxonomy" stance. A user who wants a new category should go through `bootstrap-categories` so the centroid exists for future pure-kNN classifications.
- **Persist a `corrections_applied` count column on `categories`.** Useful for surfacing "this category gets corrected often" diagnostics. Rejected for v1: derivable from `categories_history` via a `COUNT(*)` query; speculation that the count column will be needed often is unverified.
