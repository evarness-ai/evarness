# Evarnesslab developer onboarding

New-developer walkthrough of the system, from naming to a hands-on test of the
first feature (the assurance spine). Written 2026-07-18 against the
first-capability-release tree; all commands below were run and verified on that
date (107/107 tests passing).

---

## 0. Naming (this trips everyone up)

- **Evarnesslab** is this repo; the Python package inside is **`evarness`**.
- It is the public face of a longer private effort: features are proven in a
  private workbench first, then arrive here through the core/domain seams —
  only finished, verified work lands in this repo.
- It is deliberately **separate** from DLIF. A decision record in the DLIF
  project (ADR-025) fixes the boundary: Evarness governs and evaluates AI
  execution; DLIF supplies private distributed inference. They will share an
  adapter contract, never a codebase.

## 1. What the product is

**Prove an AI agent harness before it touches real data.** An agent = model +
harness (the deterministic scaffolding that routes, gates, grounds, and audits
every turn). Evarness makes the harness's safety claims *checkable* rather than
prose. Four pillars — the "first feature," which the README calls the
**assurance spine**:

1. **Canonical traces** — every run is an event stream; same graph + fixture +
   seed ⇒ same digest (`c1:sha256:…`) on any machine, OS, or Python version.
2. **Invariant contracts** — claims like "approval precedes send" are YAML
   assertions evaluated against the event stream on every run.
3. **Proof bundles** — `evarness prove` runs each scenario twice, demonstrates
   digest reproduction, evaluates contracts, and emits a portable JSON bundle
   with a mandatory `not_proven` section.
4. **Offline verification** — `evarness verify` re-checks a bundle anywhere,
   no network, optionally pinned to an Ed25519 signature
   (`--sign` / `verify --require-signature`).

Everything is **simulation-only** in this release: model and tool responses
come from scripted fixtures. Real providers are *refused with an actionable
error*, never silently simulated — the "refuse, don't degrade" rule.

## 2. Architecture in one diagram

~5,700 lines of source + ~3,300 of tests. The kernel pipeline:

```
graph + environment + seed
   → event stream → canonical digest → contract verdicts → proof bundle → offline verify
```

| Layer | Where | Knows about |
|---|---|---|
| `evarness/core/` | kernel | graphs, events, digests, contracts, proofs — **no** agent concepts |
| `evarness/domains/agents/` | first domain | LLMs, tools, approval gates, RAG — plugs into core via registries |
| `evarness/io/` | exporters | JSONL/OTLP trace export; JUnit/SARIF/HTML render verdicts |
| `evarness/cli/` | thin argparse | everything the CLI does is one library import away |

Layering rule 1 is sacred: **core never imports from domains.** Where the
kernel needs domain judgment ("is this graph deterministic?") it exposes a hook
registry (`DETERMINISM_INSPECTORS`) and the domain registers an inspector at
import time. Extension enters through four typed seams: registries
(`register_node`, `register_exporter`, …), pip entry points
(`evarness.plugins`), config overlays (packaged YAML ← `~/.evarness/*.yaml` ←
`$EVARNESS_*`), and `Protocol` interfaces (`py.typed` ships).

## 3. Code-by-code: how one run flows

Read these five files in this order — that is the whole kernel:

### `core/graph.py` (182 lines) — the IR
`GraphModel` = nodes + edges + `GraphParams` (seed, provider, context budget,
and crucially `invariants: [ids]` — how a graph opts into contracts).
`lint()` validates node configs against the registry, rejects free cycles
(iteration lives in a `loop_controller` node), and warns on LLMs that reach
output without a validator interceptor. `topological_order()` breaks ties **by
node id** — that determinism is the contract.

### `core/executor.py` (217 lines) — event-sourced execution
`execute()` lints, builds a `RunContext` (seeded `random.Random(params.seed)`,
sim provider, approvals dict), then walks nodes in topological order, emitting
events through `Emitter`. Three exceptions define run outcomes:

- `RunPaused` → status `paused` (a gate awaits a human; `RunResult.pending`
  says which node and prompt)
- `NodeBlocked` → status `blocked` (emits `policy_violation` + `run_failed`)
- anything else → status `failed` (emits `engine_error` — engine faults are
  traced, never swallowed)

After the run, `check_invariants()` computes verdicts that attach to
`RunResult.invariants` — **outside** the event stream, so checking a run never
changes its digest.

### `core/trace.py` (91 lines) — the determinism contract, published
Canonical form keeps `{seq, type, node_id, payload}` per event and drops only
`ts` (wall clock). Serialization is sorted-keys, compact separators, ASCII —
byte-stable everywhere. Two digests: `trace_digest` (names the stream,
`c1:sha256:…`) and `chain_digest` (rolling hash committing to every prefix, so
bundles verify event by event). Canonicalization is deliberately **not** an
extension point; the `c1` version bumps on any change to canonical output.

### `core/invariants.py` (266 lines) — contracts
Four temporal primitives, exactly one per contract: `never` (optional `after`
scoping), `eventually`, `every`, `precedes`. Matchers constrain `type`,
`node_id`, and payload fields (dot-paths; operators `in`/`gt`/`gte`/`lt`/
`lte`/`contains`); `satisfies: <name>` calls a predicate registered with
`@register_invariant_check`. Resolution order: pattern-local `invariants.yaml`
> `~/.evarness/invariants.yaml` > packaged library. Honesty rule: unknown ids
or malformed contracts are **failed verdicts**, never silent skips.

### `core/prove.py` (635 lines) — proof bundles
Runs every fixture **twice** (reproduction demonstrated, not asserted), pins
the subject (graph hash, tool-manifest hashes, contract-definitions hash,
engine version, environment), and writes the bundle including `not_proven`.
`verify` recomputes digests and chains, checks verdict consistency, and
validates the signature if present. A bundle with no declared invariants
verifies as "nothing asserted" — loud, not a quiet pass.

The agents domain contributes ~20 node types (`domains/agents/nodes/` —
routing, governance, memory, RAG, tools, loop, observability), a scripted
environment (`sim.py`), tool manifests (`toolspec.py` + `tools.yaml`), and
packaged contract libraries — all through registries.

## 4. The first feature, tested hands-on

Subject: the `approval_gated_send` pattern —
input → intent_router → **approval_gate** → `email.send` tool → output_parser
→ audit_log_sink → output, with three declared invariants
(`approval-precedes-send`, `no-model-call-after-block`,
`no-send-after-rejection`; the last is pattern-local).

The heart is `ApprovalGateNode.run()`
(`domains/agents/nodes/governance.py:167-202`): if no decision exists in
`ctx.approvals`, emit `approval_requested` and raise `RunPaused`. On resume
with `approve`, emit `approval_granted` and pass through; with `reject`, emit
`approval_rejected` and raise `NodeBlocked`. There is **no serialized mid-run
state** — resume replays the whole run deterministically with the decision
injected. Determinism *is* the resume mechanism.

Two stacked safety layers on one action: static (the `email.send` manifest
declares `side_effects: write`, so the graph author had to set
`approve_side_effects: true` to allow that tool class at all) and dynamic (the
gate demands a human decision per run). Independent layers; the pattern's
lesson shows how to test each alone.

Verified results (2026-07-18):

| Test | Command | Result |
|---|---|---|
| Pause | `run` with no decision | 10 events, stops after `approval_requested`/`run_paused`, no `tool_called`, exit 1 |
| Approve | `--approve n3=approve` | 22 events, `approval_granted` → `tool_called email.send` → `run_finished`, 3/3 invariants pass; events 0–7 byte-identical to the paused run (the replay) |
| Reject | `--approve n3=reject` | `approval_rejected` → `policy_violation` → `run_failed`, 11 events, no send, invariants pass |
| Determinism | two identical approve runs | identical digest both times |
| Prove pending | `prove` with no decision | PROOF: PENDING, exit 1 — scenario paused, `ok: null`, invariants/reproduction not evaluated (§5) |
| Prove+verify | `prove --approve n3=approve` then `verify` | PROOF HOLDS, reproduced=True; offline verify all green (digest, chain, verdict consistency) |
| Suite | `pytest -q` | **112 passed** |

## 5. The pending verdict (tri-state `ok`, E8)

`evarness prove approval_gated_send` with no `--approve` **pauses** at the
gate, so invariants are *not evaluated* and reproduction is *not attempted*.
The verdict is honest about that in three states:

- `ok: true` — every declared claim was actually checked and held.
- `ok: null` — **PENDING**: nothing failed, but a scenario paused, so nothing
  was checked. `invariants_pass`/`reproduced` are `null` too (vacuous truth is
  never reported as truth). The CLI exits 1 and JUnit/SARIF carry a failing/
  warning entry — a proof that proved nothing yet must not pass a merge gate.
- `ok: false` — a contract failed, a digest did not reproduce, or no
  invariants were declared (NOTHING ASSERTED).

Pausing is still *designed behavior* — PENDING is not a failure, it is an
instruction. To actually demonstrate the three invariants:

```bash
evarness prove approval_gated_send --approve n3=approve -o proof.json
# → invariants 3✓/0✗, reproduced, PROOF: HOLDS
```

## 6. Rerun crib sheet

```bash
cd evarnesslab
source .venv/bin/activate
P=evarness/domains/agents/patterns/approval_gated_send

evarness patterns                                                  # what's runnable
evarness run $P/graph.json --fixture $P/fixtures/send.yaml         # → paused
evarness run $P/graph.json --fixture $P/fixtures/send.yaml --approve n3=approve
evarness run $P/graph.json --fixture $P/fixtures/send.yaml --approve n3=reject
evarness prove approval_gated_send --approve n3=approve -o proof.json
evarness verify proof.json
pytest -q                                                          # 107 tests
```

## 7. Path to expert

1. `tests/test_core.py` (1,009 lines) — the executable spec of the determinism
   contract.
2. The other two patterns: `single_shot_qa` (minimal graph) and
   `governed_email_assistant` (routing, RAG, budget, plus a failure fixture
   the policy gate must block).
3. Write your own invariant in `~/.evarness/invariants.yaml`; watch a typo'd
   one fail loudly instead of skipping.
4. The sim environment (`domains/agents/sim.py` + fixture YAML format) and the
   prove/verify internals (`core/prove.py`, `core/attest.py`).
