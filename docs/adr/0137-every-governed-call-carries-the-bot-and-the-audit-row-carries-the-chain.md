# ADR-0137: Every governed call carries the bot, and the audit row carries the chain

Status: Accepted (2026-11-01), not implemented. Work package 1 of the Evarness Next
specification (`docs/architecture/evarness-next-spec.md`, sections 3 and 10).
Relates to: `agent/tool_runner.py` (`caller`, stamped by `GovernedToolRunner`),
`runtime/plugin_host/api.py` (the `plugin:` caller), `kernel/governance/caller_policy.py`
(the `mcp:` caller, `MCP_CALLER_PREFIX`), `kernel/governance/audit/`.

## Context

Evarness becomes the foundation for many bots. Today a governed call carries one caller
string, and three forms exist: `model:<agent_type>` from the agent loop, `plugin:<name>`
from a plugin's `api.tools`, `mcp:<client>` from an outside MCP client. There is no bot.
When two bots run in one process, the egress gate, the approval hook and a lease check
need to know which bot a call belongs to, and the audit row needs to show it afterwards.
A tool could claim a bot in an argument; a hook could parse the caller string. Neither
is something the kernel can trust.

The audit log is best-effort and per row. A proof bundle can show what a row says, not
that no row was removed between two others.

## Decision

Every governed call carries one caller, stamped by the kernel. No caller sets it. Four
forms are added to the three that exist: `owner` (the one owner, on a paired device),
`device:<device_id>` (a paired device acting without a bot), `bot:<bot_name>` (a call a
bot's runtime starts on its own: a routine firing, a message received) and
`a2a:<agent_name>` (an outside agent over A2A). The formats are closed; a value not
listed is a validation error (spec section 3).

The call context gains three fields: `bot_id`, `run_id`, `task_id`. The egress gate, the
grant hook (ADR-0139) and the lease check (ADR-0140) decide by `bot_id`, never by a
tool's claim and never by parsing the caller. A `plugin:` or `model:` call inside a bot's
run carries that bot's `bot_id`.

The audit row carries the full chain: owner, device, bot, run, step, tool, resource,
action (spec X1). Every row the specification adds carries `bot_id` or a foreign key to a
row that does.

The audit log is hash-chained, and the proof bundle verifier checks the chain (spec X2,
decision D18).

## Alternatives

- Derive the bot from the caller string (`bot:finance` means finance). A `model:` call
  inside a bot's run would then have no bot, which is exactly the call the gate must
  attribute.
- Let the tool pass `bot_id` as an argument. The kernel would be enforcing a claim.
- Keep the audit log unchained and rely on the proof bundle's row digests. A missing row
  leaves no gap to find.

## Consequences

- Hooks that today read `caller` to decide keep working: the caller is still there. New
  decisions read `bot_id`.
- The `caller_policy` seam (the kernel asks for a policy rather than importing the plugin
  host) is the pattern for every new caller form; nothing above the kernel is imported.
- A chained audit log makes an unavailable log a decision, not a detail. What an
  unavailable log does to a `read` step and a model call is an open point (spec section
  12); `AuditKeyUnavailable` today covers only a missing key.
- One owner (decision D2). The `owner` caller has no identifier because there is one.
