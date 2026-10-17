# ADR-XXXX: Model sets sized to the host, chosen by `iris doctor`

- Status: **Draft** (number and final location to be assigned in the private tree)
- Date: 2026-10-16; decision 7 measured and the `lite` judge changed 2026-10-17
- Relates to: ADR-0120 (tier edits in the settings store), ADR-0023 (kNN gate before
  the LLM), R5 (the starter model set), `config/doctor.yaml`, `config/llm_tiers.yaml`

## Context

The public product is the email assistant. Its minimum machine today is **16 GB of RAM**
(`doctor.yaml` `ram.floor_gb`) and a **4.7 GB starter pull** (`qwen2.5:7b-instruct`).
A tester with a current Windows laptop (Core Ultra 7, 32 GB) reported a 10-minute
`pip install "iris-harness[email]"`; a user with 8 or 16 GB cannot run their own mail at
all. The goal of this ADR is that a user with a decent laptop can try the harness
quickly, without taking anything away from a user with a big laptop or a GPU.

What the cost actually is, measured against the code (not the docs):

| Cost | What it is | Where |
|---|---|---|
| `qwen2.5:7b-instruct`, 4.7 GB pull, ~6 GB resident | The starter model. It is **tier 2**, the agentic/chat tier (`use_for: action, finance, profile_query, ...`). It is not the email classifier. | `doctor.yaml:32`, `llm_tiers.yaml:125` |
| `qwen3.5:4b-q4_K_M`, ~2.5 GB resident | The email judge: one JSON call per email, `pinned: true`. | `llm_tiers.yaml:206`, `email_workflows/judge.py` |
| `scikit-learn` (+ `scipy`) | One import: `HDBSCAN` in category discovery. | `email_workflows/discovery.py:286` |
| `chromadb`, `pyarrow`, `duckdb` | Core dependencies; the heavy part of the install. `pyarrow`/`duckdb` serve only the cold audit archive. (Separate decision; not taken here.) | `pyproject.toml` |

The 16 GB floor is tier 2 (7B) and the judge (4B) resident together. The embedder is
already small and torch-free (ChromaDB's ONNX MiniLM, `llm/embeddings.py`); it is not
the problem.

Three mechanisms already exist and are reused rather than duplicated:

1. **`iris doctor`** reads the host (`platform.system/machine`, `ram_total_bytes`;
   `services/system/doctor.py:243-257`) and checks it against `doctor.yaml`. Today it
   answers yes/no; it does not answer *which size*.
2. **Tier edits (ADR-0120, `llm/tier_edits.py`)** lay owner overrides for
   `provider, model, max_tokens, temperature, timeout_seconds, think` per tier over
   `llm_tiers.yaml` from the settings store (section `llm_tiers`, keys `tier:<name>`).
   A deploy does not clobber them; only the fields that differ are stored.
3. **`ResourceGovernor` (`llm/arbiter.py:266`)** downshifts to `tier1` at run time under
   RAM or thermal pressure (opt-in, `IRIS_ADAPTIVE_TIERS=1`). That is moment-to-moment
   pressure, not install-time sizing; the two must stay distinct.

Profiles (`config/profiles/*.yaml`) are **plugin sets** (`default`, `email`,
`minimal`). Machine size is a different axis and must not be modelled as a profile.

## Decisions

1. **A model set is data, not a profile and not a second `llm_tiers.yaml`.**
   `config/doctor.yaml` gains a `model_sets:` block. Each set names the host it fits and
   a *sparse* override of tiers — only the fields that differ from `llm_tiers.yaml`,
   restricted to `EDITABLE_FIELDS`:

   ```yaml
   model_sets:
     lite:        # CPU laptops, 8-16 GB RAM; measured, so automatic (decision 7)
       fits_when: { ram_gb: 8 }
       tiers:
         tier2:       { model: qwen3.5:4b-q4_K_M, think: false }   # the judge's model
         # router and email_judge are NOT overridden: see decision 7
       starter_models:
         - { name: llama3.2:3b,       size_gb: 2.0 }   # the router, same as standard
         - { name: qwen3.5:4b-q4_K_M, size_gb: 2.5 }   # tier 2 here; the judge everywhere
     standard:    # today's llm_tiers.yaml, unchanged
       fits_when: { ram_gb: 16 }
       tiers: {}
       starter_models:
         - { name: qwen2.5:7b-instruct, size_gb: 4.7 }
     full:        # 32 GB+, or a GPU with >= 16 GB
       fits_when: { ram_gb: 32, vram_gb: 16 }
       tiers:
         tier2: { provider: ollama, model: qwen3.6:35b-a3b }
       starter_models: [...]
   ```

   The top-level `starter_models` list moves under `standard`. `tier1` (`granite4.2:3b`)
   is already small and is the governor's downshift target; `lite` leaves it alone.
   `auto: false` keeps a set out of the automatic choice until it is measured; it is
   then applied only when forced. Everything `lite` changes is now measured (decision
   7), so it is automatic: an 8-16 GB host lands on it without being asked.

2. **`iris doctor` picks the largest set the host satisfies and says why.**
   `fits_when` is a conjunction of minimums; sets are ordered smallest to largest and
   the last one that fits wins. The report gains a line:

   > Model set: lite (15.6 GB RAM, no GPU detected). Override: `iris doctor --model-set standard`

   `iris doctor --fix` pulls *that set's* `starter_models` and writes its `tiers` as tier
   edits. Nobody is pushed down: a 32 GB laptop or a GPU host lands on `standard` or
   `full` without being asked.

3. **GPU detection is best-effort and never blocks.** Apple Silicon is unified memory,
   so RAM is the number. Elsewhere, `nvidia-smi --query-gpu=memory.total` when present;
   after a first model load Ollama's `/api/ps` `size_vram` (already read by the arbiter,
   `arbiter.py:207`) confirms whether the daemon is on a GPU. Unknown GPU → size by RAM.

4. **The choice is applied through tier edits (ADR-0120), not by rewriting the YAML.**
   Rationale: the mechanism exists, it survives upgrades, the owner can still change one
   tier in the app afterwards, and the app's existing guard on provider changes keeps
   working. `iris doctor --model-set <name>` forces a set and is recorded in the same
   section under a new key `model_set` so `iris system status` can print which set is
   active and whether it was chosen or forced.

5. **The runtime governor is unchanged and stacks on top.** A `lite` host under thermal
   pressure still downshifts to `tier1`. The two signals stay separate: `doctor` sizes
   the install once, the governor reacts to the moment.

6. **The email judge may move with the set; the rows already say which model judged.**
   `email_judge` is `pinned: true` so the governor's downshift and learned priors cannot
   move it, because its accuracy is only comparable on one model. A set change is a
   deliberate move and is allowed. `email_judgments` already records `model` and `tier`
   per row (`judgments.py:54-55`), so a later re-judge on another model is comparable.
   The digest and the judge surfaces should show the model when it differs from the
   set's (open question 3).

7. **The `lite` set is gated on the holdout, not on model cards.** Before `lite` ships as
   a chosen (rather than forced) set, each model it changes is measured against
   `holdout-labels.jsonl`, and the delta against the shipped model is recorded here.

   *The judge, measured 2026-10-17.* The 50 holdout emails were judged from their stored
   sender, subject and snippet, through `judge_one` (the real `judge.yaml` prompt and the
   governed schema-constrained Ollama call), in a throwaway `IRIS_HOME`. The holdout's
   labels are category roots, not buckets, so the score is agreement with the shipped
   judge, read against the bucket definitions where the root makes the answer plain.

   | Judge model | Agrees with shipped | bill / event / needs_reply / fyi / unsure | Figures invented | p50 |
   |---|---|---|---|---|
   | `qwen3.5:4b-q4_K_M` (shipped) | 50/50 | 0 / 7 / 1 / 42 / 0 | 0 rows | 2.2 s |
   | `qwen3:1.7b` (proposed) | 25/50 | 10 / 22 / 0 / 18 / 0 | 8 rows (`min_due`, balances) | 1.1 s |
   | `qwen3:4b` | 20/50 | 5 / 32 / 1 / 12 / 0 | 0 rows | 2.9 s |
   | `granite4.2:3b` (tier 1) | 30/50 | 5 / 20 / 2 / 22 / 1 | not checked | 1.8 s |
   | `llama3.2:3b` (router) | 15/50 | 2 / 17 / 1 / 7 / 23 | not checked | 1.2 s |

   Every one of `qwen3:1.7b`'s 25 disagreements is a false positive against the
   definitions: bank alerts and social notifications as `bill`, retailer promotions and
   news digests as `event`, the one `needs_reply` demoted to `event`, all at confidence
   0.90 or more, so `unsure_below` catches nothing. A false `bill` reaches the dues
   digest. The shipped judge on the same input gave 42 `fyi`, 7 `event` from one school's
   mail, 1 `needs_reply` and copied no figures it was not given. Caveat: production reads
   up to 4000 characters of body; here every model saw the snippet, so absolute numbers
   shift with bodies, but every model saw the same text and the gap is not close.

   *Decision on the judge:* the judge is not where the memory goes (about 2.5 GB
   resident), so `lite` keeps `qwen3.5:4b-q4_K_M`. `qwen3:1.7b` is out. A smaller judge
   is revisited only with a smaller model that passes this table.

   *Tier 2, measured 2026-10-17.* A 12-scenario suite over the demo mailbox (200
   synthetic emails, judged): inbox search, bills due, appointments, who is waiting on a
   reply, a digest, a count, the planner, the profile, code generation, a numbered plan.
   Each turn ran through the real runtime with the governed tools, with the model set
   applied through the tier-edit store, two passes per variant in its own copy of the
   demo home. A run passes when the turn reached the agent loop without error, the
   answer names the right sender or fact, and nothing was web-searched.

   | Tier 2 model | Router | Passed | Web-search detours | Thinking leaked | p50 / mean turn |
   |---|---|---|---|---|---|
   | `qwen2.5:7b-instruct` (standard) | 3B | 18/24 | 2 | 0 | 7.0 s / 7.8 s |
   | `qwen3:4b` (first lite proposal) | 3B | 16/24 | 0 | 10 | 28.8 s / 58 s, one timeout |
   | `qwen3:4b` with the `llama3.2:1b` router | 1B | 14/24 | 0 | 11 | 21.2 s / 34 s |
   | `qwen3.5:4b-q4_K_M` (the judge's model) | 3B | 21/24 | 0 | 0 | 7.2 s / 7.7 s |
   | `granite4.2:3b` (tier 1) | 3B | 18/24 | 0 | 0 | 5.0 s / 5.9 s |

   `qwen3:4b` fails for a reason outside the model's judgment: the harness sends Ollama
   `think: false` (verified at the request), and this model tag ignores it, returning
   its thinking inline with no opening tag, which the harness's stripper did not match
   (fixed with this measurement). Every no-tool answer carried a chain of thought and
   turns took four to eight times longer. `qwen3.5:4b-q4_K_M` and `granite4.2:3b`
   honour the flag. The 7B's two web-search detours were on "which payments are due":
   its search query dropped the possessive, so the research guard (ADR-0102) did not
   fire; the guard now also reads the turn's own question (fixed with this measurement).

   *Decision on tier 2:* `lite` runs tier 2 on `qwen3.5:4b-q4_K_M`, the judge's model,
   and keeps the shipped 3B router. One 4B model resident serves both, about 5 GB with
   the router and the embedder. It beat the 7B on this suite at the same latency. The
   tier 1 log of 2026-05-19 recorded this model regressing tool calls as tier 1; on
   this suite it called the right tool more often than the 7B (14 tool-using runs to
   12). Nothing `lite` changes is unmeasured, so `lite` is automatic.

8. **Not decided here, recorded so they are not lost.** Two follow-ups that the research
   behind this ADR supports, each its own decision:

   - *Feed the ADR-0023 kNN gate from a static embedder* (`minishlab/potion-base-32M`,
     model2vec: 30 MB, numpy-only, MTEB classification 71.7 vs MiniLM-L6's 69.3), train
     centroids from holdout labels and judge corrections, so most mail never reaches the
     LLM; drop `scikit-learn` (HDBSCAN is its only user).
   - *Give the judge a typed-decision interface* — `decide(state, questions)` with
     `criteria: {bucket: definition}`, the shape `judge.yaml` already has — with the
     Ollama LLM as the first backend. The "System One" decision-model family
     (`autotrust/JEV-*`, `hotchpotch/bekko-system-one-v0`, `vega`, `Julia-1`, `laya`)
     is the right shape for the judge but, as of 2026-10-16, every small one needs
     PyTorch in Python, the only ONNX path (bekko) is a JS runtime, and the ones that
     fit a laptop score ~57 on unseen choice tasks (bekko-68M, "General Choice") while
     the trending one (`laya`) scores a Task Avg of 13 on S1MB. Revisit when one ships
     ONNX + Python; gate any swap on the holdout.

## Consequences

1. A user on an 8-16 GB machine gets a working email assistant with a 4.5 GB pull and
   about 5 GB resident with the router and the one 4B model warm, instead of a refusal,
   and `iris doctor` picks it without being asked. `standard`'s starter list does not
   include the judge (its tier-models check warns about it); `lite`'s does, since the
   same model is its tier 2. A user on a big machine sees no change.
2. `doctor.yaml` gains a schema (`model_sets`, `fits_when`, per-set `starter_models`);
   `services/system/doctor.py` gains host sizing and a `--model-set` flag; `tier_edits`
   gains the `model_set` key; `iris system status` prints the set. Tests:
   `tests/unit/.../test_doctor*` for the selection order and the override,
   `test_tier_edits.py` for the new key.
3. `lite` judges exactly as `standard` does (decision 7); what it may lose is tier 2
   quality on chat and tool calls, which is unmeasured and is why `lite` stays forced
   only. When a set does move the judge, the row carries the model (decision 6).
4. `ram.floor_gb: 16` becomes `standard.fits_when.ram_gb`; the hard floor becomes the
   smallest set's `ram_gb` (8). `demo_gb` stays: below it only the scripted demo runs.
5. The README's "Ollama and 16 GB of RAM" and `docs/getting-started/install.md` change
   to "8 GB for the lite set, 16 GB for the standard one"; `setup-gotchas.md` follows.

## Open questions

1. **Native Windows.** `check_platform` returns RED for `platform.system() == "Windows"`
   with "Install WSL2" (`doctor.py:404-411`). A Windows user who gets through the install
   is then refused by `doctor`, so `lite` never matters there unless this changes.

   *Why it is refused is not recorded.* The check arrived with the public tree's first
   commit (no message); `doctor.yaml:14`, `install.md:11` and `setup-gotchas.md:33`
   state the rule without a reason. A sweep of `src/` for POSIX-only calls
   (2026-10-16) found no hard blocker — every site is guarded or degrades:

   | Site | On Windows | Effect |
   |---|---|---|
   | `fcntl.flock` — `audit/spool.py:42`, `audit/archive/compact.py:47` | `fcntl = None` fallback exists | Locking becomes a silent **no-op**; concurrent spool or compaction writers are unprotected, no crash |
   | `import fcntl` — `docs_search/corpus.py:232` | `ImportError` caught | Returns `None`; fine |
   | `os.chmod(…, 0o600 / 0o700)` — ~15 sites: vault DB, audit ledger, approvals, devices, side-effects, cost, web-push stores, key files | No error; Windows honours only the read-only bit | The owner-only-file guarantee is **silently weaker** |
   | `pmset` — `host_pressure.py:45` | Guarded, returns 100 | Governor never sees thermal throttling |
   | `AF_UNIX` — `testing/__init__.py`, email demo | `getattr(socket, "AF_UNIX", None)` | Fine |
   | Keyring | Windows Credential Locker backend exists | The vault works natively — unlike WSL2, which has no keyring |
   | Ollama | Native Windows build exists | Fine |

   No `os.fork`, `setsid`, Unix signals or hard `/tmp`. CI runs `ubuntu-latest` only
   (`.github/workflows/*.yml`); there has never been a Windows run.

   The likely reasons, inferred: (a) *untested, not broken* — the owner's machines and
   CI are Mac/Linux, and RED is the honest answer for release 1; (b) *the security
   posture leans on POSIX* — the vault, ledger and approval stores protect themselves
   with `0o600` and `flock`, which on Windows become no-ops without any error, so the
   tamper-evidence story quietly weakens; (c) a smaller support surface.

   Because of (b), flipping the check to YELLOW alone is not enough: a Windows owner
   would get a vault whose files are not private, with no warning. The change that
   makes native Windows honest, if Windows laptops are an audience for `lite`:

   - add a `windows-latest` job to CI for the unit suite and fix what fails (expected:
     the `chmod`/`flock` *assertions* in tests, not the runtime);
   - on Windows, lock the spool and compaction with `msvcrt.locking` instead of
     skipping, and either protect the stores with Windows ACLs or have `doctor` say
     plainly that file permissions are not enforced;
   - then make `check_platform` YELLOW ("untested; may work").

   Estimated a day or two, not a port. Decision needed: do this with the lite set, or
   keep RED and leave Windows users on WSL2 (where `lite` still helps, since WSL2 shares
   the host's RAM).
2. **Thresholds.** 8 / 16 / 32 GB and 16 GB VRAM are proposals. They should be set from
   what the sets actually need resident (judge + tier 2 + context), measured, not from
   round numbers.
3. **Surfacing the model.** Should the digest and `iris email` surfaces print the judging
   model when the owner is on a set other than `standard`, so a re-judge after changing
   sets is understood?
4. **Naming.** "lite / standard / full" are working names. "lite" reads as a lesser
   product; "laptop / desktop / workstation" says what it is sized for instead.
5. **Set changes after install.** When the owner moves to a bigger set later (new
   machine, added RAM), should `doctor --fix` offer to re-judge recent mail on the new
   judge, or leave the rows as they are?

## Research record (for decision 8; model authors' benchmarks, not ours)

| Candidate | Params / disk | Python inference | Evidence | Verdict |
|---|---|---|---|---|
| Smaller Ollama judge (`qwen3:1.7b`, `qwen2.5:1.5b`) | 1.5-1.7B, ~1.1 GB | none added | Qwen2.5-1.5B 95.7 % JSON parse; Ollama `format=schema` is grammar-constrained | **Rejected by the holdout** (decision 7): valid JSON, wrong buckets |
| `minishlab/potion-base-32M` (model2vec) | 32M, 30 MB | numpy, tokenizers, safetensors | MTEB classification 71.7 vs MiniLM 69.3; ~500x faster | **Gate backend** (decision 8) |
| `cross-encoder/nli-deberta-v3-xsmall` / `ModernBERT-base-zeroshot-v2.0` | 71M / 150M, ONNX on Hub | onnxruntime (already installed via chromadb) | BTZSC 2026: best NLI cross-encoder ~0.60 macro-F1 vs Qwen3-4B 0.65 | Viable zero-shot fallback; not chosen |
| GLiClass `edge-v3.0` / `instruct-edge-v1.0` | 32.7M, 131 MB | `gliclass` → torch + transformers + sklearn | edge 0.49 F1 vs base (746 MB) 0.66; no ONNX for v3 | Wrong footprint |
| `hotchpotch/bekko-system-one-v0` 17M/68M/400M | 29 MB / 196 MB / 1.4 GB ONNX | torch officially; ONNX driven by a JS runtime | S1MB 27.6 / 40.5 / 50.6; unseen "General Choice" 39 / 57.5 / 80.3 (Jev 98.7) | Watch v1 |
| `nandakishorm/vega-08b` | Qwen3.5-0.8B + 57 MB engine | `vegaml` → torch + backbone | email example; abstain + conformal; enron-spam 1.00; JevBench 0.545 vs Jev 0.866 | Closest functionally; too heavy |
| `SupersonicLabs/Julia-1` | 144M, 550 MB | torch (custom pkg) | typed-decisions 73.2 % ≈ Jev 72.7 %; Banking77 64 % | No CPU numbers; watch |
| `convaiinnovations/laya` | 421M, 808 MB | torch + transformers | S1MB Task Avg 13.4 (below majority baseline); 512-token ctx | Trending ≠ good |
| `tardellirs/crivo-150m` | 149M | torch + transformers | Decision Index 36.4 | CC BY-NC: cannot ship |
| `autotrust/JEV-35B` / `JEV-27B` | 27-36B | vLLM, 32-80 GB GPU | Decision Index 59.7 | Out of scope for a laptop |

Sources: BTZSC benchmark (arXiv 2603.11991; HF blog `aarabil/btzsc-benchmark`); GLiClass
paper (arXiv 2508.07662) and model table (docs.knowledgator.com); model2vec results
(minish.ai); S1MB leaderboard and bekko v0 release article (HF `hotchpotch`); the model
cards named above, read 2026-10-16. The paper the JEV card cites (arXiv 2609.14973) is
a robotics VLM report; the typed-decision papers appear to be 2610.18773 ("Jeva: System
One Models from First Principles") and 2610.00346.
