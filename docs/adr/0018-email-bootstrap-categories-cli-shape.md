# ADR-0018: `iris email bootstrap-categories` CLI — implementation shape

Status: Accepted (2026-05-25)

**Status pass (2026-10-18):** the decision holds; the code now lives in `evarness_personal/plugins/email_workflows/cli.py`, `discovery.py`. command and pipeline live in the email plugin, not under the harness; sentence-transformers is the optional `ml` extra

Context: Personal-assistant upgrade Phase 1 Track 1E.2. Parent: [ADR-0017](./0017-email-classification-dynamic-hierarchical-self-learning.md). Predecessor spike: phase1_corpus_discovery.md (in the private project log).

## Context

The Track 1E.1 spike validated that `(embed → HDBSCAN sweep → Tier-3-local naming)` produces a reviewable starter taxonomy on a real 2000-message inbox in under 3 minutes. The spike scripts under `scripts/spike_corpus_*.py` are reusable harnesses but not shippable: they hardcode account slugs in CLI args, talk to llama-server directly with `requests`, write to `data/spike/`, and trust the LLM's self-reported confidence.

Track 1E.2 productionizes the pipeline as a real, opt-in IRIS CLI command. ADR-0017 fixes the *design* (3-level hierarchy, dynamic discovery, opt-in bootstrap, MiniLM embedder); this ADR fixes the *implementation shape* for the production module + CLI.

Eight non-obvious implementation calls need to be locked before code lands:

1. Where on disk the proposals file goes.
2. Whether the CLI auto-fetches mail if the local corpus is too small.
3. How "confidence" is computed (since the LLM self-confidence was inflated).
4. How the namer prompt avoids the time-pegged-leaf bug seen in the spike.
5. How the LLM endpoint is reached before Track 1H lands the tier router for Tier-3-local.
6. Where the production code lives (module layout under `src/iris_harness/email/`).
7. What changes for `sentence-transformers` (dev → main deps).
8. What the CLI does NOT do yet (persistence is Track 1F).

## Decision

Eight implementation decisions:

1. **Workspace path: `~/.iris/workspace/email/<account_slug>/proposals.jsonl`.** Matches the existing `~/.iris/workspace/` convention for agent-local state (SOUL.md, USER.md, AGENTS.md, credentials/). Account slug is the same `provider:address` form used in `data/iris.db.email_accounts.id` per ADR-0016 (e.g. `gmail:user@gmail.com` → directory name `gmail-user-at-gmail.com`; `@` → `-at-`, `:` → `-` for filesystem hygiene). Rejected: `data/spike/...` keeps prototype paths in production. Rejected: `data/email/<slug>/...` muddles agent-state with cross-cutting tables.

2. **Default `--fetch-if-low True`.** If the local `email.db` has fewer than `--min-corpus` (default 500) messages for the account, the CLI runs a one-shot bulk fetch using the same `fetch_new_emails` path the heartbeat sweep uses, with `max_messages=2000, cold_start_days=180`. Side effect: writes to `email.db`. Rationale: the user-friendly first-run experience. A flag `--no-fetch-if-low` opts out. Rejected: error-with-instructions ("run `iris email fetch` first") — we don't have a stable `iris email fetch` CLI yet and shouldn't invent one just to gate this.

3. **Cohesion replaces LLM self-confidence.** The proposal's `cohesion` field is the mean intra-cluster centroid cosine similarity. Vectors are already L2-normalized → cosine sim = dot product. Cluster cohesion ≥ 0.6 is a useful "this cluster is tight" signal. The LLM's self-reported `confidence` field is dropped from the schema entirely (not stored, not surfaced). Rationale: the spike showed the model returned ≥0.95 on every cluster — including misrouted airline — making it useless as a triage signal.

4. **Improved naming prompt.** The system prompt for the namer adds three constraints over the spike version:
   - **Leaf preference rule.** "Prefer the sender's brand or domain for the leaf when the cluster is dominated by one sender (top domain ≥ 60% of cluster). Use subject-derived leaves only when the cluster spans multiple senders posting the same topic."
   - **No time-pegged leaves.** "Leaves must not name specific seasonal campaigns (e.g. mother-s-day-sale, memorial-day, summer-sale). Use evergreen brand or topic names instead."
   - **Few-shot fix examples** matching the spike's known errors: airline (loyalty tone ≠ shopping), a pharmacy's Mother's Day (time → brand), a furniture brand's Memorial Day (time → brand).

5. **LlamaServerClient as transitional shim.** `src/iris_harness/email/discovery.py` ships a small `LlamaServerClient` class that talks to llama-server over OpenAI-compatible HTTP, configured by `--llama-base http://localhost:8090/v1`. **Marked transitional in a docstring + an `# ADR-0018 transitional` comment.** Track 1H will move classification calls to `tier_router.get_llm_config("classify")`; at that point `LlamaServerClient` collapses into the tier router and this CLI rebinds. Rejected: building the tier-router integration as part of 1E.2 — scope creep, and Tier-3-local config in `llm_tiers.yaml` has multiple downstream consumers (1G triage skill, 1J self-learning) that all want it landed atomically.

6. **Module layout.**
   - `src/iris_harness/email/discovery.py` — pure functions + orchestrator. ~250 LOC.
   - `src/iris_harness/skills/contracts/email.py` — add `CategoryProposal` Pydantic next to the existing `EmailMessage`. Frozen dataclass shape matches every other contract.
   - `src/iris_harness/main.py` — new `email_app` Typer group under the top-level. First subcommand: `bootstrap-categories`. Future siblings: `accept-categories` (1F), `fetch` (1F or 1G), `search` (1M).
   The CLI command function is thin (≈ 40 LOC); all logic lives in `discovery.py` so tests can drive it without invoking Typer.

7. **`sentence-transformers` graduates to main deps.** Move the entry from `[tool.poetry.group.dev.dependencies]` to `[tool.poetry.dependencies]`. The discovery code path is opt-in (CLI only fires when the user runs the bootstrap command) but the import has to succeed at runtime when the command is invoked. Lazy-import the model inside `embed_corpus` so test suites that don't touch discovery don't pay the import cost. **Cold-import cost of torch/sentence-transformers is significant (~3-5s); the lazy import keeps `iris --help` snappy.**

8. **NOT in scope for 1E.2.** No write to `data/iris.db.categories` (Track 1F lands the schema). No `iris email accept-categories` command. No re-cluster loop (Track 1J). No deletion or merge of existing proposals on re-run (Track 1E.2 overwrites `proposals.jsonl` and that's intentional — the file is a working draft, not a persistent store). No interactive TUI for cluster review (writeup §5 marked batch-JSONL as the right first-pass UX; revisit if 1F's accept-categories shows the editing friction).

## Consequences

- The bootstrap CLI is the user's only opt-in entry to category discovery before 1F lands; running it without a follow-up accept step produces a JSONL the user can read but the agent can't act on. This is intentional — 1E.2 is the "discover" half of "discover-then-persist."
- `LlamaServerClient` is technical debt with a clear retirement plan (Track 1H). The transitional marker should not be removed until tier-router integration lands.
- Moving `sentence-transformers` to main deps adds ~1.5GB to the cold-install footprint (torch is the bulk). Acceptable per ADR-0017's commitment to local-first ML.
- The `data/spike/...` paths used by the spike scripts are now redundant. A cleanup commit can remove `scripts/spike_corpus_*.py` after 1E.2 ships and is validated against the same account, but keep `scripts/bulk_fetch_for_spike.py` (still useful for re-seeding test corpora).
- Cohesion as the load-bearing confidence signal pushes the bar for which clusters get auto-accepted by 1F's accept-categories CLI. The threshold (e.g., auto-accept if cohesion ≥ 0.75, else require user edit) is 1F's decision.

## Alternatives considered

- **Use Tier-2 cloud (Claude) for naming instead of Tier-3-local.** Faster, more reliable parses, no llama-server dependency. Rejected: email content classification is sensitivity-high per ADR-0002. Tier-2 cloud is for sensitivity-low only.
- **Skip the cohesion computation and trust HDBSCAN's `probabilities_`.** sklearn HDBSCAN exposes `probabilities_` (point-to-cluster membership strength). Rejected: probabilities are per-point not per-cluster; we'd still need to aggregate. Centroid cosine is also more interpretable to the user reviewing the JSONL.
- **Land the tier-router integration here as part of 1E.2 (instead of deferring to 1H).** Tempting because then `discovery.py` would ship clean. Rejected on scope: 1H needs schema changes in `config/llm_tiers.yaml` that are easier to validate against three call-sites (1G + 1J + 1E.2's namer) than one.
- **Auto-accept high-cohesion clusters into a persisted store as part of 1E.2.** Rejected: persistence is 1F. Splitting it lets 1F design the schema (and choose between `categories` table flat-list-with-parent-pointer vs ltree-style path-string layouts, etc.) without 1E.2 forcing a shape.
- **Use a YAML "starter taxonomy" of roots/branches that the bootstrap can extend.** Rejected per ADR-0017's explicit "no hardcoded taxonomy" stance. The 14 allowed roots are a coercion fallback, not a starter taxonomy; branches and leaves are fully discovered.
