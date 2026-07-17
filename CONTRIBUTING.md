# Contributing to Evarness

Thanks for your interest in improving Evarness. This document explains how the
project works, how to get a change merged, and the design rules every contribution
must respect.

## Ways to contribute

- **Bug reports** — open an issue with the reproduction steps: graph, fixture, seed,
  and the trace excerpt or digest if you have one. Deterministic repros are the
  house currency.
- **Fixtures, patterns, and invariant contracts** — new simulation fixtures, attack
  scenarios, and contract suites are as valuable as engine code.
- **Docs** — `docs/` is a first-class surface; unclear docs are bugs.
- **Engine changes** — see the design rules below before writing code.

## Development setup

```bash
# Python >= 3.10
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q            # all tests must pass
```

Everything works headless through the CLI:
`evarness validate|run|trace|prove|verify|patterns`.

## Design rules (enforced by review)

These are the architecture invariants recorded in `DECISIONS.md`. Changes that break
them will be asked to rework, however good the feature:

1. **Determinism contract.** Same graph + fixture + seed ⇒ identical canonical
   trace, named by a versioned digest. If your change touches execution order,
   event payloads, or canonicalization, run the determinism tests and explain the
   effect in your PR. Anything that changes canonical output bumps the
   canonicalization version.
2. **Verdicts never touch the evidence.** Invariant verdicts live outside the event
   stream — checking a run must never alter its digest.
3. **Governance honesty.** Policy blocks, failures, and budget overflows are
   first-class trace events, never silent. A blocked run must never reach the model.
4. **Refuse, don't degrade.** A capability the environment can't honor produces a
   loud, specific error — never a silent fallback.
5. **Extension points over inline logic.** A capability lands as a registry entry +
   packaged YAML (with a `~/.evarness/` user overlay) + graph config — not as a
   hardcoded branch. Misconfiguration is traced, never silently ignored.
6. **Claims need evidence.** A doc statement about behavior, performance, or safety
   traces to a test or a recorded run. Anything not measured is stated as not
   measured.

Deliberate pre-1.0 simplifications (stdlib sqlite3, no ORM, approximate token
counting) are documented in `DECISIONS.md` — please read the relevant entry before
"fixing" one.

## Pull request process

1. Fork and create a topic branch (`feat/...`, `fix/...`, `docs/...`).
2. Keep PRs focused — one logical change per PR.
3. Before pushing: `python3 -m pytest tests/ -q` must be clean.
4. If you made a design decision, add an entry to `DECISIONS.md` — the log is part
   of the product.
5. A maintainer will review; expect questions about determinism and traceability.

## Licensing of contributions

By submitting a contribution you agree that it is licensed under the
[Apache License 2.0](LICENSE), per Section 5 of that license. No separate CLA is
required.

## Questions

Open a GitHub issue or discussion — design questions are welcome before you write
code.
