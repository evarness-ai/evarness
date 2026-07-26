# Security Policy

## Supported versions

Evarness is pre-1.0. Only the latest release (and `main`) receive security fixes.

| Version | Supported |
| ------- | --------- |
| latest release / `main` | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/evarness-ai/evarness/security/advisories/new)
("Report a vulnerability" on the repository's Security tab).

Include what you can of: affected component, a reproduction (graph + fixture + seed
where applicable), and impact. You can expect an acknowledgment within a few days;
fixes are coordinated before public disclosure.

## Scope

Evarness currently executes harness graphs **in simulation only**: every model and
tool response comes from a scripted fixture, and nothing in this package makes
network calls or touches files outside its own data directory (`~/.evarness/`).
That keeps the attack surface deliberately small, and it means:

- **Simulation results are not a safety proof.** A proof bundle demonstrates that
  declared invariants held over scripted scenarios, reproducibly. It does not
  establish that an agent is universally safe. Every bundle carries a
  "not proven" section stating exactly this.
- **Signing proves integrity, not execution.** A signed bundle proves the bundle is
  unaltered since signing — not that the runs happened as described. This is stated
  in the bundle and in `verify` output.

Reports that demonstrate a violation of a **stated** guarantee are very much in
scope — for example: two runs with the same graph, fixture, and seed producing
different canonical digests; an invariant verdict that alters a run's digest; a
policy block that fails to prevent a simulated model call; a `verify` pass on a
tampered bundle; or a trace event that silently drops a failure.
