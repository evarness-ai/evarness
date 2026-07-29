# Evarness

**Prove an AI agent harness before it touches real data.**

[![ci](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml/badge.svg)](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml)
[![pypi](https://img.shields.io/pypi/v/evarness)](https://pypi.org/project/evarness/)
[![release](https://img.shields.io/github/v/release/evarness-ai/evarness)](https://github.com/evarness-ai/evarness/releases)
[![license](https://img.shields.io/github/license/evarness-ai/evarness)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10–3.14-blue)](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml)

[Documentation](https://evarness-ai.github.io/evarness/) ·
[Live demos](https://evarness-ai.github.io/evarness/demos/) ·
[Tutorials](https://evarness-ai.github.io/evarness/tutorial-custom-node/) ·
[Blog](https://evarness-ai.github.io/evarness/blog/) ·
[Discussions](https://github.com/evarness-ai/evarness/discussions) ·
[Studio](https://github.com/evarness-ai/evarness-studio)

> **New to AI agents?** An AI agent is software that uses an AI model (LLM)
> not just to chat, but to do things for you — read an inbox, fill a form,
> send a message. The model thinks; a layer of plain, predictable
> software around it decides what the AI is actually allowed to do. That
> layer is called the **harness** — the model is the engine, the harness is
> the car: the brakes, the seatbelts, the steering. Evarness is a crash-test
> rig for that car. It runs the harness through practice scenarios where
> everything is staged — no real email, no real money — writes down every
> step like a plane's flight recorder, checks simple written rules such as
> "a human must approve before anything is sent" (the way a bank asks for
> two signatures before moving a large amount), and produces a report anyone
> can double-check on their own laptop, no internet needed. "We watched it
> and it seemed fine" is a demo, not proof; the report is the proof.

Your agent has an approval gate in front of its send tool — but yesterday's
refactor may or may not have kept every path behind it, and when a security
review asks you to *demonstrate* that nothing sends without a human, a green
test suite isn't a demonstration. Evarness is **harness assurance**: run the
harness in simulation, prove its declared rules held, and hand anyone a
proof bundle they can verify on their own machine. It turns its safety
story into **evidence anyone can check**.

## Why Evarness

- **Canonical traces** — same graph, fixture, and seed ⇒ the same digest
  (`c1:sha256:…`) — designed and regression-tested to stay identical across
  supported machines, OSes, and Pythons. Reproduced, not asserted.
- **Invariant contracts** — "a blocked run never reaches the model",
  "approval precedes send": declared in YAML, checked on every run.
- **Proof bundles** — `evarness prove` runs every scenario twice, evaluates
  the contracts, and states what was **not** proven, in the bundle itself.
- **Offline verification** — `evarness verify` re-checks any bundle with no
  network and no trust in the machine that produced it. Signing optional.

## Simulation is the point, not the limitation

Every model and tool response comes from a scripted fixture — which is
precisely what makes hostile scenarios exact, repeatable, and safe to run a
thousand times. The harness is the *deterministic* part of an agent: the
gates, routes, redactions, and budgets. If you can't prove the deterministic
part holds, the model's quality is irrelevant — a leak through an unguarded
edge doesn't care how good the model was. You're proving the seatbelt, not
the driver. Real providers and real tools are refused with an actionable
error, never silently simulated: this package makes no network calls, by
design.

## Try it in sixty seconds

```bash
pip install evarness==0.1.0a1

# expected: PROOF: PENDING, exit 1 — nothing sends without human approval
evarness prove approval_gated_send -o proof.json

evarness prove approval_gated_send --approve n3=approve -o proof.json
evarness verify proof.json          # → VERIFY: OK, offline
```

**The digest challenge:** your digest should match the
[v0.1.0 release receipt](https://github.com/evarness-ai/evarness/releases/tag/v0.1.0)
byte for byte. If it doesn't, that's a bug —
[file it](https://github.com/evarness-ai/evarness/issues). If it does, you
just reproduced the product's central claim on your own machine. Every
command runs headless:
`validate | run | render | prove | verify | export | patterns`.

## See it before you install it

The [live demos](https://evarness-ai.github.io/evarness/demos/) are generated
by Evarness itself when the docs site builds — a replay you can scrub, a
[blocked run](https://evarness-ai.github.io/evarness/demos/blocked.html)
stopping before the model, and a proof browser carrying a real bundle you can
extract and verify. The site build **fails** if their digests drift.

## Learn

- [Guide](docs/GUIDE.md) — run graphs, read traces, declare invariants, gate CI
- [End-to-end walkthrough](docs/E2E.md) — every feature, with the digests your machine should reproduce
- [Build a custom node in five minutes](docs/tutorial-custom-node.md) · [a domain plugin, without touching core](docs/tutorial-domain-plugin.md)
- [Architecture](ARCHITECTURE.md) · [Decision log](DECISIONS.md) — every rule the engine enforces traces to a reasoned entry

## Build with us

Evarness is a young project with a big thesis: `evarness.core` is a
domain-agnostic assurance kernel — graphs, events, digests, contracts,
proofs — and **agents are its first domain**, built entirely through public,
typed extension seams. The same machinery can govern ML pipelines, data
flows, any workflow where "we always do X before Y" deserves to be a
verified contract instead of a wiki page. That future is exactly what we
want to build with the community:

- **Run it and tell us.** `prove` → `verify` on your machine — did the
  digest match the release receipt? Either answer helps.
- **Build a node.** The [five-minute tutorial](docs/tutorial-custom-node.md)
  is real: three files, no core changes, your event in the canonical trace.
- **Dream up a domain.** If your pipeline has rules worth proving, [start a
  discussion](https://github.com/evarness-ai/evarness/discussions) — domain
  packs are the project's future, and early ideas shape the seams.
- **Found something wrong?** A graph, a fixture, and a seed make a perfect,
  deterministic bug report — [open an issue](https://github.com/evarness-ai/evarness/issues).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the design rules (determinism
contract, verdicts never touch evidence, refuse-don't-degrade) — they're
what make the project provable, and they're enforced kindly but firmly.

## What a proof establishes — and what it doesn't

A bundle demonstrates that declared invariants held over scripted scenarios,
reproducibly, with the trace as evidence. It does **not** establish universal
safety, and a signature proves integrity — not that the runs happened. Every
bundle says this about itself. Scope in [SECURITY.md](SECURITY.md).

## Status

**Alpha, evaluated in the open.** This first release is the assurance spine,
simulation-only — on PyPI as the pre-release `evarness==0.1.0a1`. Real
providers, sandboxed tools, and adapters for other agent frameworks are on
the roadmap, each arriving as a separately proven release. The **stable**
0.1.0 follows community evaluation — that gate hasn't moved. The optional visual client
is [Evarness Studio](https://github.com/evarness-ai/evarness-studio) —
nothing here requires it.

## License

[Apache-2.0](LICENSE).
