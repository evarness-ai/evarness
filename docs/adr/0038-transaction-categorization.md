# ADR-0038: Transaction categorization — hybrid rules + embedding kNN + corrections

Status: Accepted (2026-06-13)
Context: Finance build F3 (slice 2). Reuses the email triage pattern (ADR-0021/0022/0023). Companion: ADR-0007 (per-currency), ADR-0037 (CLI/queries).

## Context

Extracted transactions have descriptions ("ZOMATO ORDER", "DMART", "SALARY
CREDIT") but no category, so `spend` could only total per currency, not
tell the user *what* they spent on. We need categorization that is
explicit where possible, learns the long tail, and improves from user
corrections — mirroring the email triage design the roadmap pointed at.

Transaction descriptions are short merchant strings, so a keyword/merchant
table is unusually effective (more so than for email bodies); the embedding
classifier is the fallback, not the front door.

## Decision

**Three layers, highest precedence first** (same shape as email triage):

1. **Corrections** — user-taught `keyword → category` facts in
   `finance.db` (`finance_category_corrections`). A correction is a
   merchant-level routing fact (the finance analog of email's
   sender-domain-tagged correction exemplars), top precedence.
2. **Rules** — seed merchant/keyword substrings per category in
   `config/finance/categories.yaml` (income, groceries, dining, transport,
   utilities, investment, …; optional `direction` restricts a category to
   debit/credit). Deterministic, explicit, open-sourceable. **Longest
   matching keyword wins** (so "upi-zomato" beats "upi").
3. **Learned kNN** — for novel merchants, embed the description (MiniLM via
   `iris_harness.email.discovery.embed_corpus`) and cosine-match against category
   centroids built from the seed keywords; gated by **cosine ≥ 0.35 and
   margin ≥ 0.08** — the same gate as the email kNN (ADR-0023). Off by
   default (`--use-knn`) since it loads the model.

A transaction the kNN is unsure about is left **uncategorized**, never
guessed. CLI: `iris finance categorize [--use-knn]`,
`recategorize --keyword --category` (teach + re-run),
`spend --by-category`.

## Alternatives considered

- **LLM-per-transaction categorization.** Slower, non-deterministic, and
  (per ADR-0035) the model shouldn't be trusted with the numbers anyway;
  rules + embeddings are cheaper and explainable.
- **kNN-only (no rules).** Needs labelled data to bootstrap and is opaque;
  merchant strings are a near-perfect deterministic signal — rules first.
- **Reuse the email categories store verbatim.** Its taxonomy/centroids are
  email-shaped (hierarchical roots); finance needs a flat spend taxonomy.
  We reuse the *embedding function + gate*, not the email schema.

## Consequences

**Positive:**
- Explainable, fast categorization with a learning loop; corrections
  persist and take precedence on every future statement.
- `spend --by-category` per currency (ADR-0007).
- Reuses the proven email embedding + kNN-gate; no new ML infra.

**Negative / still open:**
- Longest-keyword-wins can mis-rank when two categories' keywords overlap
  in one description ("DMART SHOPPING" → shopping over groceries); a
  correction fixes it.
- kNN centroids are seeded from keywords (not real labelled transactions)
  — good enough as a fallback; could be enriched from corrections later.
- No per-account category budgets / trends yet (F4 monitor territory).

## Related ADRs

- ADR-0021/0022/0023 (email triage kNN-gate + corrections — the pattern),
  ADR-0007 (per-currency), ADR-0037 (CLI/queries), ADR-0035 (numbers stay
  deterministic; categorization never touches amounts).
