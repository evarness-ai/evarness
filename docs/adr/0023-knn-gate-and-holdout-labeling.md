# ADR-0023: kNN-gate measurement + holdout-labeling tools

Status: Accepted (2026-05-25)
Context: Personal-assistant upgrade Phase 1 Track 1L. Parent: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md) §kNN-gate. Companion: [ADR-0022](./0022-resource-aware-triage-revision.md) §3 (the thresholds this track measures).

## Context

ADR-0022 §3 introduced two threshold constants — `KNN_GATE_MIN_COSINE = 0.7` and `KNN_GATE_MIN_MARGIN = 0.05` — as "starting points subject to Track 1L's kNN-gate measurement." Track 1G's e2e validation against a real Gmail showed those defaults produce a gate-pass rate of ~37% (11/30 on the sample). That number is a black-box observation; we don't know whether 0.7/0.05 is the *right* trade-off for the user's corpus, only that some emails make it through.

Phase 1 spike #2 (`scripts/phase1_spike_*.py`, PR #43 merged) produced 50 user-labeled emails in `data/spike/phase1_emails_labeled.jsonl` with one of 6 flat root-level labels: `{personal, marketing, social, news-digest, finance, learning}`. Those labels are the closest thing to ground truth available without additional labeling work.

ADR-0017 specifies "≥85% leaf-level agreement" as the kNN-gate criterion. The spike data is flat-root; leaf-level measurement requires labeling work we explicitly defer. **Track 1L measures root-level agreement** and treats leaf-level as a follow-up that needs richer labeled data.

Beyond Phase 1, the categorization taxonomy evolves over time (re-cluster every quarter, new senders, etc.). Threshold tuning needs to be a repeatable operation, not a one-shot. That requires:
- A standing labeling tool the user can invoke any time to grow the held-out set.
- A measurement tool that consumes the held-out set, sweeps thresholds, and emits a reproducible report.

Eight non-obvious implementation calls to lock:

1. Where the labeled holdout lives on disk.
2. Whether to build a new labeling CLI vs reusing the spike script.
3. The supported label vocabulary.
4. The CLI surface for measurement.
5. How thresholds are recommended from the sweep.
6. Whether ADR-0022 §3 thresholds auto-update from measurement output.
7. Leaf-level measurement coverage.
8. How the spike's existing 50 labels become accessible to the new tool.

## Decision

Eight implementation decisions:

1. **Holdout labels live at `~/.iris/workspace/email/<slug>/holdout-labels.jsonl`.** Per-account, JSONL format (append-only, one `HoldoutLabel` per line). Workspace path matches the existing `proposals.jsonl` placement from ADR-0018. Real email content is in the user's workspace, not the repo — gitignored by convention via `~/.iris/`. Rejected: in-repo path (would force gitignore tooling and leak content); per-runtime SQLite table (overkill for an append-only flat file with ≤500 expected rows).

2. **Build a new `iris email label-holdout` CLI; retire the spike's `phase1_spike_label.py`.** The spike script is dev-only naming (`phase1_*` prefix, hardcoded paths). Open-source requires a stable command name + arg surface. The new CLI re-uses the spike's interactive-labeling logic (sample → prompt → write) but ships as `iris email label-holdout` with proper Typer integration. The spike script will be deleted in a later cleanup commit (same pattern as `scripts/spike_corpus_*.py` removal after 1E.2). Rejected: leave the spike script in place as the de-facto tool. It works for one user but isn't a shippable surface.

3. **`ALLOWED_HOLDOUT_LABELS` = the 14 ADR-0022 roots.** Mapping from the spike's 6 flat labels:
   - `personal` → `personal`
   - `marketing` → `shopping`
   - `social` → `social`
   - `news-digest` → `news`
   - `finance` → `finance`
   - `learning` → `learning`
   The other 8 roots (`work`, `travel`, `jobs`, `community`, `automotive`, `tools`, `transactional`, `other`) are valid labels in the new tool even though the spike data has no examples of them. Rejected: hierarchical (root, branch, leaf) labels — leaf-level requires too much labeling work for v1 and the spike data only supports root.

4. **`iris email knn-gate --account <slug> [--labels <path>] [--writeup-to <path>]`.** Default `--labels` is the per-account workspace path from §1. Output: JSON sweep grid to stdout, Rich table summary, optional markdown writeup. Standalone command — separate from `iris email triage` to keep the per-email-classification path and the measurement path independent. Rejected: `iris email triage --measure` overload (mixes two concerns).

5. **Threshold pick: highest gated-accuracy subject to gated-count ≥ 50% of holdout.** Both constraints required. Pure accuracy maximization with 100% queue rate is degenerate. The 50% floor mirrors ADR-0022's spirit (kNN should be the common path, LLM the exception). If no combination meets the 50% floor, the tool reports "no gate combination meets the constraint — corpus needs more discriminating categories or richer embedder" and recommends widening the gate (`cos_min=0.5, margin_min=0.02`) as the loosest sensible default.

6. **Threshold updates require human review.** The measurement tool writes a recommendation but does NOT auto-edit `ADR-0022 §3` or `KNN_GATE_MIN_*` constants. A human reads the writeup, decides whether to adopt the recommendation, and lands it as a separate commit (see Commit 7 of Track 1L's plan). Rejected: auto-tune (ADRs shouldn't change without explicit human review; sudden gate changes ripple through every classification).

7. **Root-level measurement only for v1.** The CLI reports root-level precision/recall/accuracy. Leaf-level measurement is parked behind a future "richer labeled set" track — explicitly noted in the writeup. When leaf-level becomes feasible (more labels OR a self-learning-driven labeled set from Track 1J), this CLI extends to add a `--level {root,leaf}` flag.

8. **Spike data is imported, not parsed at measurement time.** A `iris email label-holdout --import-spike <path>` flag converts `data/spike/phase1_emails_labeled.jsonl` (flat schema) to `holdout-labels.jsonl` (HoldoutLabel schema) one-time. Rejected: the knn-gate CLI accepting the spike's schema directly — couples two file formats and makes the spike's path-shape load-bearing forever.

## Schema

```python
class HoldoutLabel(BaseModel, frozen=True):
    # Identity
    message_id: str  # the EmailMessage.id this label applies to
    account_id: str  # email_accounts.id slug

    # Label
    true_root: str  # one of ALLOWED_HOLDOUT_LABELS
    label_source: str = "user"  # 'user' | 'imported-from-spike'

    # Provenance — denormalized envelope so the holdout file is
    # self-contained (the email.db row may have been pruned/aged out
    # later; the holdout label must survive)
    from_address: str
    from_domain: str | None
    subject: str
    snippet: str
    received_at: datetime

    # Lifecycle
    labeled_at: datetime
```

## CLI surfaces

```
iris email label-holdout
  --account <slug>                  required
  --sample N                        default 10; how many unlabeled to present per session
  --import-spike <path>             one-shot: convert spike JSONL to HoldoutLabel JSONL,
                                    no interactive prompt
  --skip-already-labeled            default True; never re-prompt for a labeled message_id

iris email knn-gate
  --account <slug>                  required
  --labels <path>                   default ~/.iris/workspace/email/<slug>/holdout-labels.jsonl
  --writeup-to <path>               optional; write markdown report
```

## Sweep grid + cost function

Per ADR-0023 §4:
```
cos_min   ∈ {0.50, 0.60, 0.65, 0.70, 0.75, 0.80}
margin_min ∈ {0.02, 0.05, 0.08, 0.12, 0.20}
= 30 combinations
```

For each combination, compute:
- `gated_count` — labels where (cos_top1 ≥ cos_min AND margin ≥ margin_min)
- `gated_accuracy` — agreement between classifier's predicted root vs `true_root`, on the gated subset
- `queue_rate` — fraction of labels that fell below the gate (= 1 - gated_count/total)

Pick:
```
candidates = [c for c in combinations if c.gated_count / total >= 0.5]
if not candidates: candidates = combinations  # widen to all and flag
best = max(candidates, key=lambda c: (c.gated_accuracy, c.gated_count))
```

## Consequences

- Threshold tuning becomes a repeatable, automatable operation — not a one-shot. The user runs `iris email label-holdout` periodically to grow the labeled set, then `iris email knn-gate` to re-measure.
- ADR-0022 §3 thresholds are treated as configuration, not source-of-truth. The KNN_GATE_MIN_* constants in triage.py are the *current* values; the writeup at `docs/architecture/spikes/phase1_knn_gate.md` records what was last measured and when.
- The flat-root limitation is documented forever. Future tracks (1J self-learning OR a leaf-level relabeling pass) can extend the holdout schema to add `true_branch` + `true_leaf` columns — additive, no break.
- The spike script `phase1_spike_label.py` becomes redundant after import. A cleanup commit removes it once we've validated the new tool reproduces the spike's labels (verified by `--import-spike` round-trip).
- Open-source readiness: every CLI sub-command now reaches the user via `iris email <verb>`. The dev-prefixed scripts under `scripts/phase1_*` retire in favor of the proper namespace.

## Alternatives considered

- **Skip the labeling tool; require users to hand-author the JSONL.** Fastest path. Rejected: contradicts the open-source goal; hand-editing 50+ rows is a paper cut at scale.
- **Ship a TUI (curses-style) labeler.** Better UX for power users. Rejected for v1: typer-prompt-style numbered-choice input is good enough and zero new deps. TUI is a follow-up if the labeler sees enough use.
- **Use the spike data path (`data/spike/...`) directly with a CLI shim.** Saves the import step. Rejected: cements `data/spike/` as a load-bearing path forever; the spike directory is explicitly experimental + gitignored + ephemeral.
- **Combine `label-holdout` and `knn-gate` into one command.** Reduces command count. Rejected: they have different cadences (label periodically, measure on demand) and the combined help text would confuse first-time users.
- **Maximize F1 instead of accuracy.** F1 weighs precision and recall, useful when class imbalance matters. Rejected for v1: the gate is binary (classify-or-queue), not per-class. Per-class precision/recall is in the writeup for diagnostics; the threshold pick uses overall gated accuracy.
- **Add a calibration-curve plot to the writeup.** Useful long-term. Rejected: markdown writeup is plain-text-friendly; plots would force a PNG asset in the repo. Add only if Track 1J needs visual calibration.
