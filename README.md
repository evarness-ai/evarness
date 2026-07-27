# Evarness

**Prove an AI agent harness before it touches real data.**

An agent is a harness plus a model: the model reasons, but the harness — the
deterministic scaffolding that routes, gates, grounds, and audits every turn — is
what decides whether the system is safe to run. Evarness makes the harness's
guarantees *checkable*:

- **Canonical traces** — the same graph, fixture, and seed always produce the same
  canonical event stream, named by a versioned digest (`c1:sha256:…`). The digest
  is identical across machines, operating systems, and Python versions.
- **Invariant contracts** — the guarantees a harness claims ("a blocked run never
  reaches the model", "approval precedes send") are declared as contracts over the
  event stream, not prose — and evaluated on every run.
- **Proof bundles** — `evarness prove` runs every scenario twice, demonstrates
  digest reproduction, evaluates the contracts, and emits a portable bundle —
  including a mandatory section stating what was *not* proven.
- **Offline verification** — `evarness verify` re-checks a bundle's digests, event
  chains, and verdicts anywhere, with no network and no trust in the machine that
  produced it. `--sign` adds an Ed25519 attestation; `verify --require-signature`
  pins it.

All execution is **simulation-first**: every model and tool response comes from a
scripted fixture, so scenarios — including the hostile ones — are exact,
repeatable, and safe to run. Real providers and real tool execution are
recognized and *refused* with an actionable error: this package makes no network
calls.

## Install

```bash
pip install git+https://github.com/evarness-ai/evarness
# signing support:
pip install "evarness[sign] @ git+https://github.com/evarness-ai/evarness"
```

Python ≥ 3.10. Two runtime dependencies (`pydantic`, `pyyaml`).

## Sixty seconds to a verified proof

```bash
evarness patterns                          # what's runnable out of the box
evarness prove approval_gated_send -o proof.json     # → PROOF: PENDING
evarness prove approval_gated_send --approve n3=approve -o proof.json
evarness verify proof.json
```

`approval_gated_send` is a harness whose contract is that **nothing sends without
a human approval** — so the first `prove` pauses at that human gate and says so:
`PROOF: PENDING`, because a paused scenario's invariants were never evaluated and
a proof that checked nothing must not claim anything. Supplying the decision with
`--approve` produces the real thing: the bundle demonstrates the three declared
invariants held over the scripted scenario, that the run reproduces
digest-for-digest, and — in its `not_proven` section — exactly what none of this
establishes.

Every command works headless: `evarness validate | run | render | prove | verify | export | patterns`.
`export proof.json` unpacks a **verified** bundle into standard interchange
files — per-scenario canonical JSONL and OTLP traces, JUnit/SARIF verdicts,
and a manifest with sha256 receipts — so other frameworks and pipelines can
consume proofs without adopting our formats; a bundle that fails verification
is refused with nothing written.
`run --html run.html` writes the run as a **self-contained HTML artifact** — the
graph canvas, a playhead over the canonical events, and the invariant verdicts
in one file with no external requests; the digest travels inside and is
recomputable from the artifact alone. `evarness render graph.json` draws a
graph without executing it, and `evarness render proof.json` produces the
**proof browser**: one viewer per scenario under the bundle's verdict badge,
with the whole bundle embedded — extract it from the page and `evarness
verify` re-checks it offline, signature included.
See [docs/GUIDE.md](docs/GUIDE.md) for the full walkthrough — running graphs,
reading traces, declaring your own invariants, exporting to JUnit/SARIF/OTLP, and
wiring `prove` into CI as a merge gate — and [docs/E2E.md](docs/E2E.md) to
exercise every feature end to end with verified commands, expected outputs, and
the reference digests your machine should reproduce byte-for-byte.

## Built to be extended

The package is layered ([ARCHITECTURE.md](ARCHITECTURE.md)): `evarness.core` is
a domain-agnostic kernel — graphs, events, digests, contracts, proofs — and
**agents are its first domain**, contributed entirely through typed extension
seams: node registries, a provider factory, determinism inspectors, contract
libraries, config overlays, and pip entry points (`evarness.plugins`). The same
kernel can govern other domains — an ML pipeline where *"no deploy without a
passing eval"* is a declared, per-run-verified contract is the same machinery
as *"a blocked run never reaches the model"*. If you want to build a domain or
a node set, `evarness.core` never loads agents behind your back, the seams are
`Protocol`-typed, and the package ships `py.typed`.

## What this does — and does not — establish

A proof bundle demonstrates that declared invariants held over scripted
scenarios, deterministically and reproducibly, with the trace as evidence. It
does **not** establish that an agent is universally safe, and a signature proves
the bundle is unaltered — not that the runs happened. Every bundle says this
about itself. See [SECURITY.md](SECURITY.md) for the scope.

## Status

First capability release: the assurance spine (canonical traces, invariant
contracts, proof bundles, offline verification), simulation-only. Real model
providers, real tool execution with OS-enforced sandboxing, and adapters for
foreign agents exist on the roadmap and arrive as separate, individually-proven
releases. Not yet on PyPI — that happens after this has been evaluated in the
open.

The optional visual builder and proof browser are available separately as
[**Evarness Studio**](https://github.com/evarness-ai/evarness-studio), an
Alpha local development client. It is not required for any CLI or library
workflow — everything here runs headless.

## License

[Apache-2.0](LICENSE). Contributions welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md); the design rules there (determinism contract,
verdicts never touch evidence, refuse-don't-degrade) are enforced in review.
