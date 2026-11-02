# ADR-0140: A bot never holds a secret: credential leases and a broker

Status: Accepted (2026-11-01), not implemented. Work package 4 of the Evarness Next
specification (`docs/architecture/evarness-next-spec.md`, section 6).
Relates to: `kernel/governance/vault/handle.py` (`VAULT_PREFIX`),
`kernel/governance/vault/store.py` (`VaultStore`), `foundation/capabilities.py`
(`capability_tool_name`), `kernel/governance/caller_policy.py` (the seam pattern), the
kernel table `credential_leases` (new), a broker in `services` (new).

## Context

Secrets are handles today: a tool argument carries `vault://<handle>` and the kernel
resolves it at `pre_tool_use`, so the model never sees the value. That is one owner and
one vault. With many bots, a research bot that learns the finance bot's handle can pass
it in an argument, and the kernel would resolve it: handles are not secrets, and nothing
ties a handle to a bot. The `pre_response` hook scans for credentials on the way out, but
the exposure is on the way in.

## Decision

A bot never holds a secret. It asks a broker to perform a method of a capability
(decision D13): `broker.execute(principal, capability, method, resource, run_id,
task_id, args)`. The broker issues a lease, resolves the handle inside the kernel,
performs the method and returns a sanitized result.

A lease is a kernel row (spec 6.2): `bot_id`, the bot's `vault_namespace`, a handle
inside that namespace, the `capability` and the methods it covers, the `resource`
(an account label), `run_id` and `task_id`, fifteen minutes of life renewed fifteen
minutes at a time while the run is not terminal, and a use ceiling of one hundred.
States: `active`, then `expired`, `exhausted` or `revoked`; all three terminal; the
broker issues a new lease.

The broker lives in `services`. It writes `credential_leases` and resolves the handle
through the vault, both in the kernel below it. Its enforcement point is a kernel hook at
`pre_tool_use` on the capability call `capability:<domain.verb>.<method>`. The hook reads
only the `credential_leases` table; the kernel never imports the broker, as
`caller_policy.py` asks for a policy rather than importing the plugin host.

The kernel enforces:

- L1. A lease is refused before the handle is resolved when `vault_namespace` is not the
  bot's own. The research bot gets a denial row, not a secret.
- L2. Revoking a bot revokes its leases in the same transaction (ADR-0138, X5).
- L3. The secret value never appears in a tool argument, a tool result, a prompt or a
  log. The lease path adds no exception to the `pre_response` scan.
- L4. The file vault derives its key with a random salt per vault, stored beside the
  database. The salt is not secret. Vault backends stay pluggable (decision D14).

## Alternatives

- Per-bot vaults, one master key each. Every backend would need multi-tenancy, and the
  owner would hold one key per bot.
- Scope the handle to the bot by naming convention only. A convention the kernel does not
  check is a convention a compromised bot ignores.
- Hand the bot a short-lived token instead of a lease. A token in the bot's context is a
  secret in a prompt; the lease stays in the kernel and the bot holds only a lease id.

## Consequences

- The handle layout must make the namespace readable (`vault://bot:<name>/<label>`) or
  the vault gains a namespace column: `VaultStore` keys secrets by one flat handle today.
  Open point, spec section 12.
- `VaultStore` uses the Fernet master key directly, with no derivation; L4 describes the
  file backend to build, not the one that exists.
- A capability a bot may be leased is listed in its definition; the broker refuses one
  that is not, before any lease is written.
- The lease's clock follows the run: a terminal run revokes its leases with
  `revoke_reason = run_finished`, which ties this package to the workflow store
  (ADR-0141).
