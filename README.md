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
pip install git+https://github.com/evarness-ai/evarnesslab
# signing support:
pip install "evarness[sign] @ git+https://github.com/evarness-ai/evarnesslab"
```

Python ≥ 3.10. Two runtime dependencies (`pydantic`, `pyyaml`).

## Sixty seconds to a verified proof

```bash
evarness patterns                          # what's runnable out of the box
evarness prove approval_gated_send -o proof.json
evarness verify proof.json
```

`approval_gated_send` is a harness whose contract is that **nothing sends without
a human approval**. The proof bundle demonstrates its three declared invariants
held over the scripted scenario, that the run reproduces digest-for-digest, and —
in its `not_proven` section — exactly what none of this establishes.

Every command works headless: `evarness validate | run | prove | verify | patterns`.
See [docs/GUIDE.md](docs/GUIDE.md) for the full walkthrough — running graphs,
reading traces, declaring your own invariants, exporting to JUnit/SARIF/OTLP, and
wiring `prove` into CI as a merge gate.

## What this does — and does not — establish

A proof bundle demonstrates that declared invariants held over scripted
scenarios, deterministically and reproducibly, with the trace as evidence. It
does **not** establish that an agent is universally safe, and a signature proves
the bundle is unaltered — not that the runs happened. Every bundle says this
about itself. See [SECURITY.md](SECURITY.md) for the scope.

## Status

First capability release: the assurance spine (canonical traces, invariant
contracts, proof bundles, offline verification), simulation-only. Real model
providers, real tool execution with OS-enforced sandboxing, adapters for foreign
agents, and the visual builder exist on the roadmap and arrive as separate,
individually-proven releases. Not yet on PyPI — that happens after this has been
evaluated in the open.

## License

[Apache-2.0](LICENSE). Contributions welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md); the design rules there (determinism contract,
verdicts never touch evidence, refuse-don't-degrade) are enforced in review.
