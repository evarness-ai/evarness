# ADR-0123: Models are chosen by capability, not by tier size

Status: Proposed (2026-09-27, revised the same day after re-reading exp-005). Nothing in

**Status pass (2026-10-18):** still Proposed; not implemented. only the email judge runs qwen3.5:4b; tiers, embedder and reranker are unchanged

this ADR is applied until the proof plan in §Proof passes. Relates to: ADR-0068 (escalation judge), ADR-0071 (semantic email
retrieval, MiniLM/Chroma), ADR-0103 (actions on the instruct tier), ADR-0115 (memory is a
graph of claims), ADR-0122 (email judge tier, no cloud failover), cloud-trial plan
decisions 12-13, agentic-evals exp-005 (router shootout) and exp-004 spike 5 (tier-2
weight format). Amends `config/llm_tiers.yaml` and `deploy/server/config/llm_tiers.yaml`.
Supersedes nothing.

## Context

IRIS exists to make a harness work for small models: use what they do well, and add
deterministic helpers where they are weak. The tier map grew by size (router < tier1 <
tier2 < tier3). The work IRIS does now is not sized that way. It is a set of capabilities:

| Capability | Model on 2026-09-27 | Evidence of the gap |
|---|---|---|
| Intent routing | keyword-first; `llama3.2:3b` (2024) as the LLM fallback | exp-005 measured the keyword classifier at 88.3% and every small LLM below it (best granite4 83.8%; `qwen3.5:4b` parsed 0% of its own answers with thinking on). `router_decisions` shows `conf=0.0` silent routes (exp-006 session 2). Routing is a rules problem, not a model problem. |
| Escalation judge | `tier2` = `qwen2.5:7b-instruct` (2024) | ADR-0068 requires judge >= generator. A 7B judging a 3B is a weak margin; the judge is also the model it is asked to replace. |
| Email classification | `qwen3.5:4b-q4_K_M`, pinned | Works. Keep. |
| Semantic search / memory recall | `sentence-transformers/all-MiniLM-L6-v2` + `BAAI/bge-reranker-base` | MiniLM is a 2021 model: 384 dims, 256-token window, English only. It is the weakest part of memory recall and email discovery. |
| Tool calling (finance, search, planner, action, communication) | `qwen2.5:7b-instruct` | Measured 25% tool-call reliability on `configure_brief` (ADR-0103 note). |
| Fact extraction into the memory graph | tier 1 (`granite4`) JSON call | Works, but a separate model from the router and the judge, so three small models compete for the same hot slot. |
| Reminders, tasks, follow-ups to closure | model + state | This is a state machine. The model only extracts `{task, due, owner}`. No model change. |

Eight text models are configured. Every tier change evicts one model and loads another.
That costs seconds per turn and RAM the tool loop needs.

External evidence, not yet reproduced here: in a 13-model local tool-calling test
(jdhodges, 2026), Qwen3.5 4B passed 97.5% at 3.4 GB and Nemotron Nano 4B passed 95%;
Phi-4 Mini passed 57.5%. Our own 2026-05-19 tier-1 log says `qwen3.5:4b` "regressed tool
calls". That run had thinking on and a ReAct prompt. The two results are not comparable.
Only our harness decides.

## Decision

1. **One small model, always hot: `qwen3.5:4b-q4_K_M`, thinking off.** It serves
   `tier1`, `email_judge` and `fallback`. All three tiers use the same `num_ctx`
   (16384). Ollama reloads a model when `num_ctx` changes, so a shared `num_ctx` is
   what makes the slot shared. `keep_alive: 10m` on every one of them. The judge calls
   with `think=false` and a JSON schema (`llm/ollama_json.py`). Fact extraction
   (`memory/fact_extractor.py`) moves to the same model through tier 1 with no code
   change. **The `router` tier is not touched by this ADR** (see 3).

2. **One tool-loop model, chosen by exp-004 spike 5.** The default candidate is
   `qwen/qwen3.6-35b-a3b` on LM Studio (MLX), replacing `qwen2.5:7b-instruct` as
   `tier2`. It already runs for `tier3`, so this adds no new download. The spike
   measures it against `Qwen3.8-27B-4bit` (official dense, ~15 GB), Bonsai 2 27B
   (ternary 1.7 bpw of the same model, ~7 GB, needs Prism's `llama-server`) and,
   as a tie-breaker only, the community `Qwen3.8-35B-A3B-Distill`. Three numbers
   decide: tool-call pass rate (>= 90%, byte-stable across two greedy passes), decode
   tok/s, resident RAM next to the hot 4B. The rest of this point assumes the MoE
   wins; if another row wins, the model id changes and nothing else in this ADR does. It decodes at 75-104 tok/s on the Mac (exp-004 spike 4), faster than the 7B
   dense model. `temperature` drops from 0.7 to 0.3: a tool loop wants stable JSON, not
   variety. Because `tier2` is now the 35B, `escalation.yaml` `judge_tier: tier2` gives
   ADR-0068 a judge that is much stronger than the tier-1 generator, with no change to
   that file. ADR-0103 stands: a stronger model does not fix "the model chose not to call
   the tool". The deterministic intercept stays in front of it.

3. **Routing stays keyword-first; no router model change.** exp-005 already ran
   this test: the deterministic keyword table beat every small local LLM, and the
   LLM-as-router costs 300-800 ms per turn for a worse answer. The first draft of
   this ADR proposed swapping the router model and adding a k-NN pre-router; both
   are withdrawn. The routing work that pays is exp-005 RECOMMENDATIONS §2: rule
   fixes for the five trap families, graded against the 77-case golden set. That is
   project-iris work outside this ADR.

4. **Embedder: `Qwen/Qwen3-Embedding-0.6B`** replaces MiniLM (`EMBED_MODEL_DEFAULT` in
   `llm/embeddings.py`). 1024 dims, 32k window, multilingual. The Chroma snippet index
   (ADR-0071) and the memory semantic index must be rebuilt: the dimension changes
   from 384 to 1024, so old vectors are not comparable. Fallback if it is too slow on the
   Mac: `google/embeddinggemma-300m`.

5. **Reranker: `BAAI/bge-reranker-v2-m3`** replaces `bge-reranker-base`. It is a
   drop-in `CrossEncoder`, so `research/rerank.py` needs only the id changed.
   `Qwen/Qwen3-Reranker-0.6B` scores better but is a causal LM with a yes/no head, not a
   CrossEncoder. It needs an adapter. Do it later, only if recall@5 shows the need.

6. **Vision and cloud do not change.** `qwen2.5vl:7b` stays. Tier 3 stays local by
   default and cloud only through the `escalation.yaml` classification gate.

7. **`code_exec` and `gemma` are out of scope.** They are not on the capability list.

Result: 4 text models (router 3B untouched, 4B, tier-2 winner, coder 7B) + 1 embedder
+ 1 reranker + vision, down from 8 text models.

## Proof

Change one model at a time. Record the result in the eval log comment of
`llm_tiers.yaml`, as before. A change that does not beat the baseline is reverted.

| # | Swap | Eval | Baseline | Pass line |
|---|---|---|---|---|
| 1-2 | *(withdrawn — routing, see decision 3)* | | | |
| 3 | Tier 2 candidates | agentic-evals exp-004 spike 5: 20 tool cases x 2 greedy passes, decode tok/s, resident RAM, per row A/B/C/D | `qwen2.5:7b-instruct` 25% on `configure_brief` | >= 90% and 20/20 stable; then the RAM/tok-s rule in the spike README |
| 4 | Tier 1 -> `qwen3.5:4b` | `memory-identity` battery (identity recall is where granite4 won on 2026-05-19) | 1.0 | 1.0, or keep granite4 and accept two small models |
| 5 | Embedder -> Qwen3-Embedding | 50 memory queries with known answers, recall@5; 50 email discovery queries | MiniLM | recall@5 >= baseline + 10 pt |
| 6 | Reranker -> bge-v2-m3 | same 100 queries, nDCG@5 | bge-base | >= baseline |
| 7 | All together | `iris-evals probe`, then `core-spine`, `memory-identity`, `skills-routines` | 22/26 (session 1) | >= 24/26, no regression on memory |
| 8 | Resource | resident RAM and tok/s with 4B + tier-2 winner + embedder all hot (spike 5 row) | today's set | >= 8 GB free with the 4B hot |
| 8a | if 8 fails on the MoE | `Qwen3.8-27B-4bit` (spike 5 row B) | row A | >= 90% stable and >= 30 tok/s |
| 8b | if 8a is too slow | Bonsai 2 27B `PQ2_0` via `llama-server` (spike 5 row C); adds a `llamacpp` base URL, no code change | row B | >= 90% stable and >= 30 tok/s; else stay on A with `tier2 keep_alive: 60s` |

The email judge is not re-tested. It is pinned so that its accuracy is comparable run to
run (ADR-0122 §7).

## Alternatives rejected

- **Keep the size ladder and add one strong cloud model.** Rejected: private intents
  never leave the Mac (cloud-trial decision 13), and the gaps are in routing, recall and
  tool calls, which the cloud never sees.
- **One 8-9B dense model for everything.** Rejected: slower than the 35B-A3B MoE at
  decode on this hardware, and no better than the 4B at label picking.
- **Ternary tier 2 by default.** Rejected until spike 5 shows the MoE does not
  fit: a dense 27B reads all its weights per token, so even at 1.7 bpw it decodes
  2-3x slower than the 3B-active MoE, and it needs a runtime IRIS does not ship.
- **Qwen3-Reranker now.** Rejected for this step: it needs an adapter; the drop-in
  CrossEncoder gets most of the gain with a one-line change.
- **Swap MiniLM without reindexing.** Not possible: the dimension changes.

## Consequences

- Positive: two models cover routing, judging, extraction and the tool loop; fewer
  reloads; a judge that outranks its generator; recall over a 2025-class embedder.
- Negative: a full reindex of the Chroma and memory indexes; the 35B-A3B hot for tool
  loops costs ~20 GB resident; `deploy/server/config/llm_tiers.yaml` and
  `tests/unit/test_deploy/test_cloud_trial_config.py` must follow the model ids
  (the test pins the Mac/VM pairing).
- Reversible: every swap is one YAML line plus a reindex. The eval log records the
  reason for each.

## Files

- `config/llm_tiers.yaml` — proposed diff in `0123-llm_tiers.patch` next to this ADR
  (tier1, email_judge, fallback, tier2; router untouched).
- `agent-workbench/agentic-evals/docs/experiments/exp-004-serving-stack-bakeoff/spikes/0005-tier2-weight-format/` — the tier-2 spike (`cases.json`, README with the decision rule, `results.jsonl` after the run).
- `src/iris_harness/llm/embeddings.py` — `EMBED_MODEL_DEFAULT`.
- `src/iris_harness/plugins_builtin/research/rerank.py`, `manifest.yaml` — reranker id.
- `deploy/server/config/llm_tiers.yaml` — mirror after the Mac proof passes.
