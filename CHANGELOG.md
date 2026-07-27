# Changelog

Notable changes, newest first. The decision log ([DECISIONS.md](DECISIONS.md))
records *why*; this file records *what shipped*.

## Unreleased

## 0.1.0a1 — 2026-07-27 — Alpha pre-release, first release on PyPI

The assurance spine, simulation-only. A validated Alpha entering community
evaluation; the stable 0.1.0 follows that evaluation.

- Contributor tutorials: a custom node in five minutes, and a domain plugin
  without touching core.
- The CLI loads entry-point plugins at startup (restores plugin discovery
  after the top-level import surface became lazy).
- Everything in the assurance spine below:

- **Canonical traces** — versioned digests (`c1:sha256:…`) byte-identical
  across machines, plus a rolling event chain.
- **Invariant contracts** — `never` / `eventually` / `every` / `precedes`
  over the event stream; verdicts live outside the trace.
- **Proof bundles** — `evarness prove` runs each scenario twice, checks
  reproduction and contracts, and records a mandatory `not_proven` scope;
  tri-state verdict (HOLDS / PENDING / FAILED).
- **Offline verification** — `evarness verify` recomputes digests, chains,
  and verdict consistency from the bundle alone; optional Ed25519 signing.
- **The agents domain** — 30-plus node types (routing, governance, memory,
  RAG, tools, judges) with packaged patterns and deterministic simulation;
  real providers and real tools refuse with actionable errors.
- **Render artifacts** — graph, run, and verdicts as one self-contained
  HTML file; the proof browser renders a bundle as one page.
- **Interchange export** — a verified bundle unpacked to JSONL, OTLP,
  JUnit, and SARIF with per-file receipts; export refuses unverified bundles.
- **Extension seams** — registries and entry-point plugins for nodes, lint
  rules, contracts, context state, and proof subject pinning; the
  core/domain boundary is enforced by tests.
