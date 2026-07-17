# Evarness

**Prove an AI agent harness before it touches real data.**

An agent is a harness plus a model: the model reasons, but the harness — the
deterministic scaffolding that routes, gates, grounds, and audits every turn — is
what decides whether the system is safe to run. Evarness makes the harness's
guarantees *checkable*:

- **Canonical traces** — the same graph, fixture, and seed always produce the same
  canonical event stream, named by a versioned digest.
- **Invariant contracts** — the guarantees a harness claims ("a blocked run never
  reaches the model") are declared as contracts over the event stream, not prose.
- **Proof bundles** — `evarness prove` runs every scenario, demonstrates digest
  reproduction, evaluates the contracts, and emits a portable bundle — including a
  mandatory section stating what was *not* proven.
- **Offline verification** — `evarness verify` re-checks a bundle's digests, event
  chains, and verdicts anywhere, with no network and no trust in the machine that
  produced it.

All execution is **simulation-first**: every model and tool response comes from a
scripted fixture, so scenarios — including the hostile ones — are exact, repeatable,
and safe to run.

## Status

Pre-release. The first capability (the assurance spine above) is landing now;
installation and a runnable example ship with it. Until a tagged release exists,
expect `main` to move.

## License

[Apache-2.0](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md).
