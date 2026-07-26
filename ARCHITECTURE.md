# Evarness architecture

Evarness is layered so that the thing it proves is bigger than the thing it
ships first. The kernel doesn't know what a "tool" or an "LLM" is — it knows
graphs, events, digests, contracts, and proofs. Agents are the first *domain*
built on that kernel; they are not the kernel.

```
evarness/
├── core/                 # the kernel — domain-agnostic, minimal deps
│   ├── graph.py          #   graph IR (nodes/edges/params), lint, migrate
│   ├── executor.py       #   event-sourced execution, RunContext, RunResult
│   ├── trace.py          #   canonical form + versioned digest + event chain
│   ├── invariants.py     #   contracts: temporal assertions over event streams
│   ├── prove.py          #   proof bundles: reproduction, subject pinning, not-proven
│   ├── attest.py         #   Ed25519 attestation ([sign] extra)
│   ├── registry.py       #   generic named registries + entry-point discovery
│   ├── config.py         #   the one overlay loader: packaged YAML ← ~/.evarness ← $EVARNESS_*
│   ├── store.py          #   activity log / run persistence (stdlib sqlite3)
│   └── errors.py         #   exception hierarchy rooted at EvarnessError
├── domains/
│   └── agents/           # the first domain: AI agent harnesses
│       ├── nodes/        #   node types by concern: routing, governance, memory,
│       │                 #   rag, tools, loop, judgment
│       ├── sim.py        #   scripted environment: SimLLM, fixtures, mock tools
│       ├── providers.py  #   provider registry (sim-only in this release)
│       ├── toolspec.py   #   tool manifests; catalog.py resolves them
│       ├── grounding.py  #   answer-faithfulness rules
│       ├── classification.py, judges.py, tiers.py, prompts.py
│       └── patterns/     #   packaged runnable examples (graph+fixtures+lesson)
├── io/                   # evidence in formats the world reads
│   ├── exporters.py      #   trace exports: jsonl, otlp (registry-extensible)
│   ├── render.py         #   render artifacts: graph+run+verdicts as one
│   │                     #   self-contained HTML file, and the proof browser
│   │                     #   (one viewer per scenario, bundle embedded for
│   │                     #   offline verify; registry-extensible, no external
│   │                     #   requests, byte-stable, r-versioned)
│   └── renderers live with prove (junit/sarif/html render *verdicts*, not traces)
└── cli/                  # thin: argparse over the library, nothing else
```

## The layering rules

1. **`core` imports nothing from `domains` or `io`.** Where the kernel needs
   domain judgment (e.g. "is this graph deterministic?"), it exposes a hook
   registry and the domain registers an inspector at import. A violation of
   this rule is a bug, not a style issue.
2. **Domains contribute through registries, never through edits to core.** A
   domain provides: node types, an event vocabulary, an environment (what
   scripted execution means there), default contract libraries, lint rules.
3. **`io` consumes public core surfaces only.** An exporter sees events and
   metadata; a renderer sees a proof bundle.
4. **The CLI is a caller.** Anything the CLI can do must be one import away for
   a library consumer.

## The extension model

Four ways in, all typed, all discoverable:

- **Registries** — `register_node`, `register_exporter`, `register_grounding_rule`,
  `register_invariant_check`, `register_search_provider`, … Every registry is a
  name → object table with a loud unknown-name error. Misregistration is traced,
  never silently ignored.
- **Entry points** — a pip-installed package can register into any registry via
  the `evarness.plugins` entry-point group; `~/.evarness/plugins/` remains for
  local, uninstalled extensions. This is how a third-party domain ships.
- **Config overlays** — every tunable lives in packaged YAML, overridable per
  user at `~/.evarness/<name>.yaml` and per process via `$EVARNESS_<NAME>`.
  One loader (`core.config`) implements the merge; modules declare their
  schema, they don't reimplement the mechanism.
- **Protocols** — the seams are typed interfaces (`Provider`, `Environment`,
  `EventEmitter`, `NodeSpec`), so an extension knows exactly what to implement
  and mypy tells it when it hasn't. The package ships `py.typed`.

## Why this shape: the framework thesis

The kernel's pipeline —

```
graph + environment + seed
      → event stream → canonical digest → contract verdicts → proof bundle → offline verify
```

— contains no agent concepts. "A blocked run never reaches the model" and
"evaluation precedes deployment" are the *same kind of statement*: a temporal
contract over a governed workflow's event stream. That means the machinery that
proves an agent harness can prove any domain whose work can be expressed as a
graph of typed steps with declared guarantees:

- **ML pipelines** — `dataset → transform → train → eval → deploy_gate`, with
  contracts like *no deploy without a passing eval*, *no training on data that
  failed validation*, and every training run named by a reproducible digest.
- **Data/ETL flows** — lineage as first-class events, redaction and
  classification gates reused verbatim from the agents domain.
- **Any regulated process** — where "we always do X before Y" currently lives
  in a wiki, it can live in an `invariants.yaml` and be *verified per run*.

A domain author writes node types and contracts; they inherit determinism,
tracing, proof, and verification. That is the adoption bet: the community
extends domains, the kernel stays small and stable.

## Honesty ledger — seams done vs planned

- Done: layer boundaries as packages; one config loader; generic registries +
  entry-point discovery; typed public API + `py.typed`; exception hierarchy;
  nodes split by concern; determinism inspection lifted out of core; per-run
  domain state as namespaced context extensions (`RunContext.ext`, E10) — the
  kernel constructs nothing domain-shaped, and the agents governance fields
  live in the domain's own `AgentsRunState`.
- Planned (tracked, not pretended): a second in-tree example domain proving the
  thesis end to end; `Environment` fully abstracted so `core.executor` takes
  any environment (today the sim fixture is the only implementation and some
  of its shape still shows); the token/cost vocabulary the kernel emits in
  terminal events (`total_tokens`, `cost_usd`, `ctx.totals`) is agent-flavored
  — renaming it is a `c1`→`c2` canonicalization bump, honestly triggered by
  the second domain's arrival, not before; versioned public-API stability
  promises begin at the first tagged release, not before; concurrent execution
  of independent branches is unimplemented — but the contract that makes it
  safe to add is pinned now (trace.py, rule 4): canonical event order is the
  serial deterministic topological walk, each node's events contiguous between
  its `node_started`/`node_finished` brackets, so a future scheduler must
  buffer per-node events and flush them in that same order — concurrency lands
  as a wall-clock optimization the canonical form cannot observe, digest-neutral
  by construction, no `c1` bump.

## The invariants that outrank everything above

Layering never bends these: same graph + environment + seed ⇒ same canonical
digest (`c1` versioned); contract verdicts live outside the event stream;
blocks and failures are first-class events; ungraduated capabilities refuse
with remediation. Any restructuring that changes a digest is wrong by
definition — the test suite pins this.
