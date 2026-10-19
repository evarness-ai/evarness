# ADR-0070: Sandbox replay-eval harness for learning experiments

Status: Accepted (2026-06-21) — fully implemented on `feat/sandbox-replay-eval`

**Status pass (2026-10-18):** the decision holds; the code now lives in `runtime/self_learning_loop.py`, `tools/sandbox/`. gated by EVARNESS_SANDBOX_PREFLIGHT; the sandbox moved from `iris_harness/sandbox/` to `tools/sandbox/`

(harness core + recommendation→variant + experiment pre-flight wiring). Live
LLM eval is opt-in (`IRIS_SANDBOX_PREFLIGHT`, off by default). Builds on ADR-0069
#4 (learning intelligence, slices 1-3). **Extended 2026-06-24** to gate the skill
crystallizer's proposals + add a gVisor/Docker container sandbox (PRs #287–#297) —
see "Extension: crystallized-proposal pre-flight gate" below.

Context: ADR-0069 #4 slice 3 closes the learning loop by *promoting* an analyst
recommendation to a tracked experiment — it captures a baseline and re-measures
the target metric over **live traffic**, leaving *applying* the recommended
change a manual human step. That has two real weaknesses:

1. **Slow.** The experiment can't conclude until enough real traffic accrues for
   the metric to move with signal.
2. **Confounded.** Live traffic changes many variables at once (the inbox shifts,
   the user asks different things, other config drifts), so a single-arm
   before/after measurement can't cleanly attribute a change to the variant.

The proposed remedy (from the design review): test the variant in the isolated
Docker sandbox already built for the `iris-code` agent
(`src/iris_harness/sandbox/docker_sandbox.py`). The instinct is right about the
*weakness* but wrong about the *tool* — and naming why is the crux of this ADR.

**The coding sandbox runs code in isolation; a learning experiment's success
signal is a real-world outcome over a workload.** `completion_rate`,
`correction_rate`, `escalation_precision` are emitted by real chat turns against
the user's real inbox/files — by *interactions*, not by code execution.
Isolating execution does not generate that signal: a variant config running in a
sandbox with nothing flowing through it has nothing to measure. So the sandbox,
alone, makes an experiment *isolated*, not *testable*.

What actually makes it testable is an **offline replay-eval harness** — a frozen
workload + an offline scorer + a recommendation→config mapping. The repo has none
today (no golden/eval/replay harness exists; the closest replay source is the
real `(query, response)` traces in `observability/session_log.py`).

## Decision (proposed)

1. **Build an offline replay-eval harness as a PRE-FLIGHT for slice 3, not a
   replacement.** The loop becomes: promote → sandbox replay-eval (fast,
   counterfactual) → if it wins, human applies the change → slice-3 live
   re-measure (real-world ground truth). The sandbox eval gives an early,
   clean signal *before* anything touches production; slice 3 remains the truth.

2. **Counterfactual replay.** Run a **frozen** query set through an isolated
   runtime under **baseline vs variant** config — same queries, only the config
   differs (the "only the routed model changes" rigor from the instruct-models
   blog). Aggregate over repeats because local models vary run-to-run even at
   fixed inputs.

3. **Scope v1 to the offline-reproducible metric subset.** Score **tool-call
   correctness** and **completion / has_errors** — deterministic-ish given the
   query, and tool-correctness was the proven decisive metric. Explicitly
   **exclude `user_correction` and `downstream_reuse`**: those depend on real
   *future user behavior* and cannot be replayed. The harness measures only what
   it can reproduce honestly, and says so.

4. **Workload = frozen real traces.** Replay a versioned, frozen set of real
   `session_log` turns per intent (or a small curated golden set). Freezing makes
   baseline-vs-variant comparisons stable across runs.

5. **Recommendations must carry an APPLICABLE change.** Only recommendations that
   map to a concrete config knob (tier routing, a flag, a threshold) are
   sandbox-evaluable. This extends the analyst output (ADR-0069 #4 slice 2) to
   emit a structured, applicable mutation, not just prose `action`. Recommendations
   without an applicable mapping stay slice-3-only (live re-measure).

6. **Isolation is not a governance bypass.** Replaying real queries touches
   personal data, so the same egress/classification constraints apply inside the
   eval; a variant that routes to cloud still passes the kernel egress gate. Use
   the sandbox's existing **egress proxy** (`src/iris_harness/sandbox/egress.py`) for
   isolation *with* governance intact.

7. **Bounded + opt-in + shadow.** N queries × repeats × LLM calls is expensive on
   local models — cap N, time-box, and gate the whole harness behind an opt-in
   flag, shadow by default. The eval **never mutates production config**; the
   variant config lives only in the isolated run.

8. **Result wiring.** The variant's aggregated score is recorded on the
   experiment (e.g. a `sandbox_metric` alongside the live `current_metric`) and
   surfaced in the existing ledger; the keep/discard decision reuses the existing
   improvement rule. A losing pre-flight can spare the user from ever applying a
   bad change.

**First buildable slice (when approved):** tier-routing recommendations, scored
by tool-call correctness, replaying frozen `session_log` traces under baseline vs
variant routing, aggregated over repeats, written as a pre-flight result on the
experiment. Defer everything else.

## Alternatives considered

- **A. Slice 3 only (do nothing more).** Rejected: slow + confounded; the explicit
  ask is faster, cleaner testing.
- **B. Sandbox auto-apply + measure + rollback of all metrics.** Rejected as a
  first step: pulls in non-reproducible metrics, the full recommendation→config
  applier set, and autonomous apply at once — high risk/effort, and some metrics
  simply can't be reproduced offline.
- **C. Host a full IRIS runtime inside the code-exec Docker container.** Rejected:
  that container is built for ephemeral shell scripts, not hosting IRIS. An
  isolated in-process runtime with overridden config + the sandbox egress proxy is
  lighter and sufficient. Revisit if stronger isolation (gVisor, ADR-0069/Phase 6c)
  is required.
- **D. Live shadow A/B in production** (variant on a fraction of real traffic).
  Cleaner than slice 3's single-arm, but still needs real traffic + a routing fork
  — heavier than offline replay. A possible later complement, not the first step.
- **E. Adopt `sandcastle`** ([mattpocock/sandcastle](https://github.com/mattpocock/sandcastle),
  MIT, ~6.2k stars). Rejected — off-target, not overkill. Sandcastle is a
  **TypeScript orchestrator for AI *coding agents***: it runs Claude Code / Codex
  in an isolated box, manages git branch strategies + worktrees, and merges the
  agents' commits back. That is a different problem from ours (drive *our* runtime
  against a frozen workload and score outcomes), and it solves **none** of the hard
  part this ADR is about — the eval harness (workload + offline scorer +
  recommendation→config mapping); it only abstracts the container isolation we
  already have. It is also a poor stack fit: Node/TypeScript-only against our
  Python 3.12 runtime; our `src/iris_harness/sandbox/docker_sandbox.py` already provides the
  same Docker primitive **plus** a governance-integrated egress proxy
  (`egress.py`/`egress_proxy.py`) and a gVisor path on the roadmap, none of which
  sandcastle hooks into. Its isolated backend is **Vercel cloud microVMs** — for
  *our* workload that means shipping real personal-data queries to a third-party
  cloud, the exact egress local-first IRIS exists to prevent. Worth borrowing as
  *ideas* (not a dependency): its provider abstraction `bind-mount | isolated-microVM
  | none` validates our own `sandbox/config.py` seam (`docker | gvisor | firecracker`),
  and its `copyIn`/`copyOut` file-sync is the right pattern for a non-bind-mount
  isolation backend if we add one. The substrate was never the bottleneck.

Operator guide: how to turn the loop on, watch it, and run experiments by hand —
`docs/usage-guides/self-learning-and-experiments.md` (in the private project log).

## Implementation status

**Landed (harness core, `feat/sandbox-replay-eval`):**

- `iris_harness.learning.eval_harness` — the pure core: `EvalQuery`, `VariantConfig`
  (carries the applicable knob: `intent_tier_priors`), `RunOutcome`, `ArmScore`,
  `EvalComparison`; `score_arm` / `compare` / `evaluate`. Metrics: `completion_rate`
  (no labels) + `tool_correctness` (labelled only). `inconclusive` is returned
  rather than a false win when a metric is unmeasurable or runs are too few.
  Execution is injected (`RunQuery`) so the core is fully unit-tested without a
  model.
- `iris_harness.learning.eval_workload` — frozen workload: `build_workload_from_traces`
  (snapshot real `session_log` turns, intent-filtered + deduped) + `save_workload`
  / `load_workload` (JSON, hand-labellable for `expected_tool`).
- `iris_harness.learning.eval_adapter` — the live `RunQuery` over an **isolated** eval
  runtime + `run_preflight` one-call entry. `build_eval_runtime` uses a throwaway
  temp `data_dir` so eval turns' learning signals never reach the real
  `learning.db` (**signal-pollution was the key design constraint discovered in
  build** — eval emits the same `task_completed`/`turn_tokens` signals it measures);
  the variant is applied via `tier_router.set_intent_tier_priors`, isolated from
  production routing. The runtime build + real chat turns need a live model backend,
  so those are host-validated; the pure mapping + adapter wiring are unit-tested
  with fakes. No new direct-LLM call site (drives the governed `runtime.chat`).

**Landed (recommendation→variant + experiment wiring):**

- **Structured applicable change.** `analyst.AppliedChange` (v1 kind:
  `route_intent_tier{intent, to_tier}`) added to `Recommendation.change`, distinct
  from `target` (the number to watch vs the edit to make); the analyst prompt asks
  for it. `promote` stamps it onto the experiment (`config_changes[__applied_change__]`).
- **Recommendation→variant.** `eval_preflight.variant_from_change` /
  `variant_from_experiment` map a stamped change to a sandbox `VariantConfig`
  (route → `intent_tier_priors`).
- **Experiment pre-flight.** `eval_preflight.run_experiment_preflight` replay-
  evaluates a promoted experiment (injected eval + workload loader, so it's unit-
  tested) and stamps the verdict under `config_changes[__sandbox__]`; the
  experiments ledger (`GET /learning/experiments`) and the webui experiment row
  surface it. The live driver is `bootstrap._run_sandbox_preflights` on the opt-in
  `sandbox_preflight_tick` heartbeat (builds one isolated eval runtime, pre-flights
  all pending experiments, tears it down; bounded to `_SANDBOX_MAX_ITEMS` queries ×
  `_SANDBOX_REPEATS`). Never applies the change. No new direct-LLM call site.

## Extension: crystallized-proposal pre-flight gate + container sandbox (2026-06-24)

The replay-eval harness was extended from *routing experiments* to gate the **skill
crystallizer's** proposals, and to offer a hard-isolation container mode:

- **Proposal pre-flight.** `iris_harness.learning.preflight.build_proposal_preflight(repo_root,
  sandbox=…)` replays a proposal's intent through an isolated runtime and returns a
  `PreflightVerdict` (`passed`, `completion_rate`, `runs`, `queries`, `reason`).
  Injected into `SkillCrystallizer(preflight=…)` (opt-in `IRIS_CRYSTALLIZE_PREFLIGHT`,
  wired in `bootstrap._build_crystallize_preflight`): on the `crystallize_tick`, a
  **pass** lands the proposal as `proposed` (verdict stamped in the new
  `SkillProposal.preflight_reason`); a **genuine quality failure** (`runs ≥ 1`,
  below bar) quarantines it as `preflight_failed`; **inconclusive** (`runs == 0`, no
  workload) or a **pre-flight exception** (model down) is non-blocking → `proposed`.
  `CrystallizeReport.rejected` + the heartbeat output `{proposals, skipped, rejected}`.
- **Sandbox modes — `IRIS_EVAL_SANDBOX`** (`resolve_eval_sandbox_mode`):
  - `effect` (default) — in-process isolated runtime; external side-effects
    neutralized for the eval's lifetime via a scoped `IRIS_DISABLE_EXTERNAL_WRITES=1`
    (+`IRIS_CALENDAR_APPLE_WRITE=0`) that is restored afterwards (parent untouched).
  - `docker` — full gVisor/Docker container (`eval_docker.run_eval_in_docker` +
    `sandbox/Dockerfile.eval` building `iris-eval:latest`). The in-container
    entrypoint `eval_replay_cli` replays a **host-frozen** workload mounted read-only
    (the container can't see host traces), prints `PREFLIGHT_VERDICT {json}`, points
    the model client at the host Ollama (`host.docker.internal:11434`), mounts no host
    credentials, and runs cap-drop-all / no-new-privileges / resource-limited.
    `runsc_available()` auto-detects gVisor and falls back to plain `runc` on Docker
    Desktop. Validated end-to-end on macOS (PASS, 100%/4 queries).
  - `off` — no containment (debug).
- **Trace sourcing robustness.** `build_workload_from_traces(intent=…)` pushes the
  filter into the scan and de-dupes by query
  (`iter_recent_turns(intent=, require_query=True, unique_queries=True)`) so a
  low-volume or heavily-repeated intent isn't crowded out of the newest-N window.
- **Test isolation (issue 0026).** Session logs and the governance stores now resolve
  their default paths via `IRIS_HOME` (`session_log.LOG_DIR`,
  `governance.paths.governance_data_dir/config_dir`), so the eval/crystallizer tests
  never write into the developer's real `~/.iris` profile.

## Consequences

- A new, phased subsystem that covers only offline-reproducible metrics and
  recommendations that map to an applicable config knob — by design, not by
  omission. The harness must label what it cannot evaluate.
- Extends the analyst (slice 2) to emit structured, applicable config targets.
- Delivers a fast, safe, counterfactual pre-flight that complements — does not
  replace — slice 3's real-world ground truth.
- Off by default, opt-in + shadow, zero production mutation. Egress governance is
  preserved inside the sandbox, not bypassed by it.
- Per `docs/CLAUDE.md`, mirror into `project-iris-prd/13-decisions-and-rationale.md`
  only once this is Accepted and changes shipped behavior. It is Proposed now.
