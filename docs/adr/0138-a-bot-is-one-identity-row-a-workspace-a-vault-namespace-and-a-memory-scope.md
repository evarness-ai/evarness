# ADR-0138: A bot is one identity row, a workspace, a vault namespace and a memory scope

Status: Accepted (2026-11-01), not implemented. Work package 2 of the Evarness Next
specification (`docs/architecture/evarness-next-spec.md`, sections 2, 4.6 and 10).
Relates to: the kernel table `bot_identity` (new), `kernel/governance/hooks/types.py`
(`PRE_TOOL_USE`, `PRE_LLM_CALL`), `kernel/governance/vault/handle.py`, `memory/`.

## Context

A bot is defined by the platform: a YAML file with its prompt, tiers, plugins, budget and
routines (spec section 4.1). The kernel cannot read that file and should not: what a bot
is and does lives above the SDK. But the kernel must deny a call from a bot that was
revoked a second ago, must keep one bot's secrets and memory from another, and must do
both without a code change when a bot is added. It needs one row it owns.

## Decision

The only core row a new bot creates is `bot_identity` (spec section 4.6): `bot_id`, the
unique `name`, `status`, `workspace_path`, `vault_namespace` and `memory_scope` (both
equal `bot:<name>` and both unique), the hash of the validated definition in force, and
the timestamps. The platform writes it once through the SDK when it registers a bot.

States: `active` and `disabled` are the owner's switch, in both directions; `revoked` is
terminal and runs the cascade.

The kernel enforces four invariants:

- B1. A call whose `bot_id` names an identity that is not `active` is denied at
  `pre_tool_use` and `pre_llm_call`, mid-run included. A `disabled` bot is denied like a
  revoked one but keeps its grants, leases and sessions.
- B2. A definition change never changes a grant or a lease. A grant whose tool left the
  definition becomes `dormant` and stays dormant if the tool returns.
- B3. The workspace, the vault namespace and the memory scope of two bots never share a
  path or a name (decision D17: a bot reads its own scope and the shared one, writes its
  own, and `personal` is never listed).
- B4. Adding a bot adds a definition file, a workspace folder and one identity row. If
  adding a bot needs a code change, the seam is in the wrong place (decision D1).

Revocation cascades in one transaction (spec X5): identity, grants (ADR-0139), leases
(ADR-0140), runs to `cancelled` (ADR-0141). The plugin host then revokes the bot's
sessions; until it has, B1 denies every call a session makes. Audit rows stay.

## Alternatives

- Make the definition file the identity and have the kernel read it. The kernel would
  depend on a platform schema and on a file a bot's workspace could edit.
- Put identity in the platform's own table. B1 is a kernel hook; a hook that reads a
  platform table is a hook the platform can starve.
- Derive the vault namespace and memory scope from the name at call time instead of
  storing them. Storing them is what lets uniqueness be a constraint and L1 (ADR-0140) a
  comparison of two stored strings.

## Consequences

- Whether a revoked bot's `name` may be reused is open (spec section 12): the row stays,
  and three unique columns say no today.
- Whether a `disabled` bot's leases expire on their own clock or are revoked with the
  switch is open (spec section 12).
- The memory layer gains a scope it does not have today: memory is scoped by subject
  (`memory/fact_keys.py`, `subject_scope`), not by who may read. B3 adds the second axis.
- The definition hash on the row is what the platform compares to decide B2; the kernel
  stores it and never parses the definition.
