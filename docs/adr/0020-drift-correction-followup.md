# ADR-0020: Drift correction — user-driven corrections for systematic LLM naming errors

Status: Accepted (2026-05-25) — captures a deferred decision; the canonical mechanism (Path C) lands in Track 1J.
Context: Personal-assistant upgrade Phase 1 follow-up. Companion: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md) (self-learning), [ADR-0018](./0018-email-bootstrap-categories-cli-shape.md) (bootstrap-categories CLI), [ADR-0019](./0019-categories-store-and-accept-cli-shape.md) (categories store).

## Context

Track 1E.2's end-to-end run against a 2000-message real Gmail corpus surfaced a class of LLM naming errors that the prompt-fix from ADR-0018 §4 could not eliminate:

- **an airline → `shopping.travel.airline`** (correct: `travel.airlines.airline`). The prompt contained an explicit few-shot example for this exact case; the model still rooted the cluster as shopping. The cluster's tone — loyalty program offers, deal-style subject lines — matched the model's "shopping" prior more strongly than the few-shot's correction signal.
- **funpark → `shopping.entertainment.funpark`** (debatable: `travel.attractions.theme-park`). Less clear-cut.
- **Time-pegged campaigns** (Mother's Day, Memorial Day) — fixed by ADR-0018's prompt update, but the *class* of error (subject-line capture vs sender-identity preference) is recurring.

Three observations:

1. **Some drift is systematic, not per-cluster random.** The same kind of cluster will get the same wrong name on the next bootstrap run. Prompt-only fixes hit diminishing returns.
2. **The user already sees the proposals before they get persisted.** The JSONL between `bootstrap-categories` and `accept-categories` is editable — a sufficiently motivated user can just fix the JSON. The question is what tooling we ship around that.
3. **Auto-correction via hardcoded rules is the wrong solution.** ADR-0017 commits to "no hardcoded taxonomy" and that stance should also rule out hardcoded sender → root mappings. A YAML of `airline* → travel` mappings is a slippery slope to a static taxonomy by another name.

This ADR locks the path: how does a user correct a drift case today, and how does the system learn from those corrections tomorrow?

## Decision

Three paths considered. **Path A is the stop-gap shipping with Track 1F. Path C is the canonical mechanism landing in Track 1J. Path B is rejected for now and re-evaluated only if cross-user drift patterns prove universal.**

### Path A — the JSONL is editable on disk (ships with 1F)

After `iris email bootstrap-categories` writes the JSONL but before `iris email accept-categories` reads it, the JSONL is just a file the user can open in any editor. No new tooling needed. Track 1F's strict validation (ADR-0019 §5) guarantees that mistypes during manual editing surface immediately rather than silently corrupting the categories table.

**Workflow:**
```
iris email bootstrap-categories --account gmail:user@gmail.com
vim ~/.iris/workspace/email/gmail-user-at-gmail.com/proposals.jsonl   # fix airline
iris email accept-categories --account gmail:user@gmail.com
```

This is the minimum-viable correction loop. Lands as part of 1F; no new code beyond what ADR-0019 specifies.

**Limitations:**
- Re-running `bootstrap-categories` later overwrites the JSONL, losing edits. Track 1J's re-cluster cadence will have to either (a) detect existing accepted categories and seed the prompt with them, or (b) write to a separate `proposals_pending.jsonl` so user edits aren't blown away.
- No mechanism for the system to *learn* from these corrections — they're file-local and the LLM will keep making the same mistake on the next bootstrap.

### Path B — `categories_overrides.yaml` (REJECTED for now)

A YAML file at `~/.iris/workspace/email/<slug>/category_overrides.yaml` mapping sender-domain patterns to forced (root, branch, leaf):

```yaml
overrides:
  - match: { domain_pattern: "*.exampleair.example" }
    force: { root: "travel", branch: "airlines", leaf: "airline" }
```

`accept-categories` would apply overrides over the LLM-proposed names before strict validation.

**Rejected because:**
- Introduces a parallel source of truth that lives outside the JSONL workflow. Users who edit overrides.yaml and forget it exists will be confused when bootstrap output looks "wrong" but accept output "right."
- The ADR-0017 "no hardcoded taxonomy" stance: domain → category mappings are de facto taxonomy hardcoding by a different file format.
- Doesn't actually fix the underlying model behavior; just papers over it forever.

**Re-evaluate if:** cross-account drift patterns prove identical (e.g., every IRIS user who imports airline mail gets the same misrooting). At that point a shipped-with-IRIS overrides file might be defensible as a "known LLM weaknesses" registry — but that's a community-shared artifact, not per-user state.

### Path C — self-learning correction loop (CANONICAL, lands in Track 1J)

When the user corrects a category (via Path A's JSONL edit, or via a future `iris email recategorize` CLI), the correction is recorded in `categories_history` with `source='user-edit'` and the original LLM-proposed payload preserved in the `payload` JSON column (per ADR-0019 §3).

Track 1J's self-learning loop:
1. **Periodically samples `categories_history` rows where `source='user-edit'`.** These are ground-truth corrections.
2. **Derives systematic patterns.** Example: every email-classification correction where the original root was `shopping` and the user changed it to `travel`, with sender-domain patterns logged. Cluster the corrections by (original-root → corrected-root, dominant-sender-domain) to find the recurring drift cases.
3. **Updates the namer prompt's few-shot bank.** New `NAMING_SYSTEM_PROMPT` for the next `bootstrap-categories` run includes the user's corrections as additional few-shot examples. Bootstrap → name → user-edits-fewer-things → loop tightens.

Track 1J's design ADR (forthcoming, will be ADR-002X) makes the canonical choices for the loop frequency, the threshold for promoting a correction into the prompt, and the storage shape for the prompt's adaptive few-shot bank.

**Requires:** the audit-history schema from ADR-0019 (✓ shipping in 1F), plus the recategorize CLI surface (Track 1J or earlier).

### What ships in 1F

- Path A (the JSONL-edit workflow) works the moment Track 1F lands. No new tooling.
- ADR-0019's `categories_history` table is the substrate Path C will read. No 1F schema changes needed when 1J lands.
- This ADR is the captured deferral. It documents that the airline-style drift is a known limitation that 1F does not fix, with a forward plan that future readers can find without re-discovering the problem.

## Consequences

- The user is the source of correction in 1F. They must notice the drift, open the JSONL, and edit. Friction is real but tractable — the JSONL is at most ~50 rows and one bad row stands out on a `grep` for unfamiliar roots.
- `categories_history` becomes load-bearing for self-learning. Anything that mutates `categories` MUST record a history row (ADR-0019's invariant). Test coverage for the store should enforce this.
- The `payload` JSON column in `categories_history` is the lossless record of "what did the LLM propose, what did the user pick." Track 1J's design will likely standardize a payload schema (proposed_root/branch/leaf vs accepted_root/branch/leaf vs user-edit context) — that schema is forward-compatible because JSON.
- Reversibility: Path A → Path C transition is purely additive — Track 1J reads history that Path A's edits naturally populate. Path B can still be added later as a layer between A and the validation step if the cross-user-drift case materializes.

## Alternatives considered

- **Build Path C now in 1F.** Tempting because the loop is the actual fix. Rejected: 1J is the right home for self-learning (per ADR-0017 §self-learning); pulling the design forward into 1F adds scope and the user-edit-JSONL stop-gap is genuinely sufficient for the immediate need.
- **Ship Path B as a stop-gap and retire it when Path C lands.** Rejected because retiring a config file is hard once users start relying on it; transitional config tends to ossify into permanent config.
- **A `iris email correct-category --path <p> --new-root <r>` one-shot CLI.** Equivalent to editing the JSONL but typed. Useful but not load-bearing — adds command surface without changing what's possible. Deferred to Track 1J's design alongside the recategorize CLI.
- **Accept the airline drift as a known acceptable error and don't ship any correction mechanism.** Rejected: the audit-trail is needed for Track 1J regardless, and Path A is zero new tooling cost.

---

## Amendment 2026-05-25 — second class of error: duplicate-leaf collisions

The Track 1F end-to-end validation surfaced a second class of LLM naming error not anticipated when this ADR was first written:

**Observed:** out of 50 proposals on the real Gmail corpus, **3 pairs of distinct clusters got identical `(root, branch, leaf)` names** and collided on the categories table's path PK:

| First cluster (theme) | Second cluster (theme) | Collided path |
|---|---|---|
| Instructure homework grades (cluster 5) | Instructure attendance grades (cluster 4) | `email/learning/assignments/instructure` |
| DSW new-arrivals (cluster 41) | DSW sale (cluster 42) | `email/shopping/apparel/dsw` |
| Groupon Local (cluster 32) | Groupon Tours (cluster 22) | `email/shopping/experiences/groupon` |

`CategoryStore.upsert_if_new` (idempotent-by-path per ADR-0019 §4) caught the collision and dropped the second cluster silently. The first run reported "47 new / 3 unchanged" instead of "50 new / 0 unchanged" — the audit trail correctly shows only 47 inserts, no duplicate inserts.

**Class definition:** the `(root, branch, leaf)` is each individually plausible. The error is that two *distinct* clusters should have been *differentiated* in the leaf and weren't. Caused by ADR-0018 §4's brand-leaf preference rule overcorrecting on multi-cluster same-sender cases — when one sender produces two distinct topical sub-clusters (homework vs attendance, new-arrivals vs sale), the rule pushes both to the brand-leaf and they lose their differentiation.

**Why ADR-0020's existing Paths don't cover this:**
- Path A (edit the JSONL) works once the user knows. But the JSONL itself shows two rows with identical proposed names — the duplication is right there in front of the user but easy to miss unless they sort by path.
- Path B (overrides.yaml) would require domain → leaf-suffix rules; doesn't scale.
- Path C (self-learning) eventually learns the differentiation but requires the user to make corrections first, which they can't make if collisions are silently dropped.

### Decision — Path D ships with Track 1F: collision detection on accept

`iris email accept-categories` checks for duplicate paths *across the JSONL* during the validation phase, before any DB writes. Behavior:

- If any path appears more than once: **exit code 5**, print every collision with the cluster ids that produced each name, do NOT write to the DB.
- Error message tells the user to either:
  - Edit one of the conflicting rows to give it a more specific leaf (e.g. `instructure-homework` vs `instructure-attendance`), OR
  - Re-run `bootstrap-categories` (re-prompts the namer, which may produce different leaves on the second pass).

This is distinct from exit code 4 (invalid row content) because the fix is structural (rename to disambiguate), not value (fix a typo or wrong root).

**Why ship in 1F instead of deferring to 1J:** unlike the airline drift, this class of error is *silently destructive* — the second cluster's entire membership is lost on accept. Path C self-learning cannot recover lost categories. Detection on accept is the only way to surface the problem before data loss; the fix itself is mechanical (~10 LOC + one test) and uses the strict-validation forcing function already established by ADR-0019 §5.

### Updated workflow

```
iris email bootstrap-categories --account <slug>      # writes proposals.jsonl
vim ~/.iris/workspace/email/<slug>/proposals.jsonl   # fix wrong roots (Path A)
iris email accept-categories --account <slug>       # exits 5 if collisions
vim …                                                 # disambiguate leaves
iris email accept-categories --account <slug>       # success
```

### Implication for prompt-only fixes

The brand-leaf rule from ADR-0018 §4 is correct for the time-pegged-leaf class (a pharmacy's Mother's Day, a furniture brand's Memorial Day — those WERE fixed). It overcorrects on multi-cluster same-sender cases. Future namer prompt iterations could add a "if this is the Nth cluster you're naming for a given sender, prefer a topic-derived leaf" rule, but the verification is hard and Path D catches the failure mode regardless. Defer prompt iteration to Track 1J alongside the few-shot learning loop.
