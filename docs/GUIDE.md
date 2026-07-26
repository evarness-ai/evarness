# Evarness guide

From install to a signed, offline-verifiable proof bundle. Everything here runs
in simulation — no API keys, no network, no side effects.

## 1. Install and orient

```bash
pip install git+https://github.com/evarness-ai/evarnesslab
evarness patterns
```

A **pattern** is a packaged harness: `graph.json` (the topology), `fixtures/`
(scripted scenarios), and a lesson. Three ship built in:

| Pattern | What it teaches |
| --- | --- |
| `single_shot_qa` | The minimal graph: input → llm → output, one scripted answer |
| `governed_email_assistant` | Routing, interception, retrieval, memory, budget-aware assembly — and a failure fixture the policy gate must block |
| `approval_gated_send` | A send tool behind a human approval gate, with declared invariants |

User patterns live in `~/.evarness/patterns/`.

## 2. Run a graph and read the trace

```bash
evarness run path/to/graph.json --fixture path/to/fixture.yaml --json
```

A run is an **event stream**: `run_started`, `intent_routed`, `llm_request`,
`policy_violation`, `run_finished`, … Failures, blocks, and budget overflows are
first-class events — never silent. The run's identity is its **canonical
digest**:

- `canonical form` strips the wall-clock envelope and serializes byte-stably
  (sorted keys, compact, ascii).
- `trace_digest(events)` names it: `c1:sha256:…`. The `c1` names the
  canonicalization version — anything that changes canonical output bumps it.
- Same graph + fixture + seed ⇒ same digest. On your laptop, in CI, on any OS.

Export the canonical stream for other tooling with
`run --trace-out trace.jsonl` (or `--trace-format otlp` for an
OpenTelemetry/GenAI-semconv document).

### See the run: the render artifact

```bash
evarness run path/to/graph.json --fixture path/to/fixture.yaml --html run.html
evarness render path/to/graph.json          # static canvas, no execution
```

`--html` writes the run as one **self-contained HTML file**: the graph as a
canvas, a playhead that replays the canonical events (a blocked run visibly
stops before the model), and the invariant verdicts in a separate judgment
pane. The rules you already know apply to the artifact itself:

- **No external requests, ever.** Inline SVG/CSS/JS, a CSP that pins
  `default-src 'none'`, and hostile fixture content renders inert. Open it
  anywhere, attach it to a bug report, email it to an auditor.
- **The digest travels inside.** The embedded data island contains the
  canonical events verbatim — recompute `trace_digest` from the artifact
  alone and it must match the digest in the provenance bar.
- **Evidence and judgment stay separate.** Verdicts live in their own pane
  and their own data key; a failing verdict's `seq` links scrub the playhead
  to the cited event.
- **A not-established footer.** Every artifact states what it does not prove,
  including honest lines for paused runs and zero-contract runs.

Artifacts are byte-stable for deterministic runs and stamped with a renderer
version (`r1`) — derived evidence, deliberately outside the `c1` digest
contract. Bring your own format with `register_renderer` (io/render.py).

## 3. Declare invariants — contracts, not prose

The guarantees a harness claims live in an `invariants.yaml`, resolved from the
pattern directory, then `~/.evarness/invariants.yaml`, then the packaged
library. A contract is a temporal assertion over the event stream:

```yaml
invariants:
  no-model-call-after-block:
    title: No model call after a block
    assert:
      never:
        type: llm_request
        after: {type: policy_violation}
  approval-precedes-send:
    title: Approval precedes send
    assert:
      precedes:
        first: {type: approval_granted}
        second: {type: tool_called, where: {tool: email.send}}
```

A graph opts in via `params.invariants: [id, …]`. Verdicts are computed from
the events but stored **outside** them — checking a run never changes its
digest. Unknown or malformed contracts are failed verdicts, never silently
skipped.

## 4. Prove, then verify anywhere

```bash
evarness prove approval_gated_send --approve n3=approve -o proof.json --html report.html
evarness verify proof.json
```

`prove` runs every scenario **twice** (digest reproduction demonstrated, not
asserted), evaluates the declared contracts, and pins the subject: graph hash,
tool-manifest hashes, resolved contract-definitions hash, engine version, and
environment. The bundle carries the canonical event streams and a rolling
per-event chain digest.

The verdict's `ok` is tri-state: `true` (every declared claim checked and
held), `null` (**PENDING** — a scenario paused at a human gate, so nothing was
checked and nothing is claimed; the CLI exits 1 and the CI projections carry
it), or `false` (a contract failed, a digest didn't reproduce, or nothing was
declared). Without `--approve`, `approval_gated_send` pauses by design — you
get PENDING, not a proof.

`verify` re-checks all of it offline: digests recompute, chains recompute,
verdicts consistent, signature valid (if present). A bundle with no declared
invariants verifies as **"nothing asserted"** — a loud state, not a quiet pass.

For CI: `prove --junit verdicts.xml` (test report) or `--sarif findings.sarif`
(code scanning); the exit code gates the merge. Sign with `prove --sign`
(Ed25519, key auto-created under `~/.evarness/keys/`) and pin the signer with
`verify --pubkey … --require-signature`.

### Browse a bundle

```bash
evarness render proof.json -o proof.html
```

The **proof browser** is the render artifact applied to a whole bundle: the
tri-state verdict badge, then one viewer per scenario — canvas, playhead over
that scenario's canonical events, reproduction status, and its invariant
verdicts — with the bundle's `not_proven` section rendered verbatim as the
footer. Same self-containment guarantees as `run --html`, plus three rules of
its own:

- **The canvas never lies about identity.** A graph is drawn only when its
  hash matches the bundle's pinned `graph_sha256`: the pattern named by the
  bundle is auto-resolved and hash-checked; `--graph PATH` is hash-checked and
  a mismatch is a loud error; no matching graph means the canvas is honestly
  omitted, never substituted.
- **The page never vouches for itself.** A signed bundle is labeled
  "signature NOT checked by this page" — checking is `evarness verify`'s job.
- **The island IS the bundle.** The full bundle rides in the data island:
  extract its `bundle` key to a file and `evarness verify` re-checks it
  offline, signature included. The HTML file alone carries a verifiable proof.

## 5. The honesty rules

Worth knowing because they shape every command's behavior:

- **Refuse, don't degrade.** Real providers (`anthropic:…`, `ollama:…`) and
  `mode: real` tools are refused with remediation — they haven't shipped in this
  release, and a silent sim fallback would make the trace lie about what ran.
- **Side effects need explicit approval.** A tool whose manifest declares
  `write`/`destructive` side effects refuses to run unless the node sets
  `approve_side_effects: true` — even in simulation.
- **Every bundle states what it doesn't prove.** Scripted scenarios ≠ universal
  safety; a signature ≠ the runs happened. The `not_proven` section and the
  `verify` output say so explicitly.

## Data locations

`~/.evarness/` holds the activity log (`evarness.db`, override with
`$EVARNESS_DB`), user patterns, user overlays (`invariants.yaml`,
`prompts.yaml`, `tools.yaml`, …), and signing keys. The overlays merge over the
packaged defaults; every one has an `$EVARNESS_*` env override.
