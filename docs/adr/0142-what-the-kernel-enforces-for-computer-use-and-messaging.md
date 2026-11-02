# ADR-0142: What the kernel enforces for computer use and messaging

Status: Accepted (2026-11-01), not implemented. Work package 6 of the Evarness Next
specification (`docs/architecture/evarness-next-spec.md`, sections 8, 9 and 10, and
decision D5).
Relates to: `llm/locality.py` (`local`, `cloud`), `kernel/governance/mcp_signing/config.py`
(`SigningMode`, default `shadow`), `kernel/governance/hooks/types.py` (`PRE_LLM_CALL`,
`PRE_TOOL_USE`), the external-content envelope (`docs/concepts/governance.md`, "The
external-content floor"), `PluginManifest` (`content: external`).

## Context

The browser plugin, the desktop plugin, the message tool and the A2A adapters are the
platform's (eOS, spec section 2). They hold the sessions, the actions and the messages
in their own tables, which the kernel does not read. But a screenshot reaching a cloud
model is an egress, a click is a write, and a page that says "transfer the money" is
external content. Those are kernel decisions, and they must be taken at the call, where
the kernel sees them, not in the plugin, where the plugin host's fault boundary is the
only guard.

## Decision

The kernel owns these checks; the plugin host owns the rest (C1, C2, C4, C5 in spec
section 8), with a conformance test in `evarness.testing`.

- Locality gains `trusted` beside `local` and `cloud`, and the provider is generic
  (decision D5). A bot's definition lists which localities it may use; the gate reads
  the list.
- C3. Before a screenshot reaches a `cloud` or `trusted` tier, local OCR runs and the
  owner-PII matchers (ADR-0125) read the text. `personal` blocks the image. `local`
  tiers skip the check. A kernel hook at `pre_llm_call`.
- C6. A `write` or `destructive` computer action has a write-ahead row like any tool
  step (ADR-0141, W4). A kernel hook at `pre_tool_use`.
- C7. Page text, OCR text and a download are `content: external`: the untrusted-content
  envelope and the tripwire apply, and an instruction found there never bypasses the
  grant matcher (ADR-0139, 5.4).
- M1 to M3. A message send is a `write` with `subject_kind = message` whose class is the
  highest part class, so the grant matcher applies; the per-pair rate limit and the hop
  count are checked before any model sees the message; the receiver treats every part
  as external content.
- X3. MCP signing runs with `mode: enforce` for any bot with `computer_use` or
  `messaging` set. The shipped default stays `shadow` for everything else.
- X4. An MCP tool without a declared effect is denied (ADR-0141, D15).

The effect of a computer action is derived, not declared by the model (spec 8.3):
`navigate`, `read`, `screenshot` are `read`; `click`, `type`, `key`, `download` are
`write`; `submit` on a host in the bot's `transaction_domains` is `destructive`, elsewhere
`write`.

## Alternatives

- Let the browser plugin run the OCR gate itself. The plugin is above the fault
  boundary; a plugin failure would degrade the gate to "no gate", which is the one
  failure mode a gate may not have.
- Treat a screenshot as an image the classifier cannot read and allow it. The owner's
  screen is the most personal surface the system sees.
- Make signing `enforce` the default everywhere now. The rollout plan for signing
  (shadow, then enforce once servers are signed and the audit is clean) is unchanged;
  bots that drive a browser or talk to other bots are the first to need it.

## Consequences

- The OCR engine and its measured miss rate on the owner's screens are open (spec
  section 12); the gate ships with a measured number or it does not ship.
- Which A2A binding to serve first is open; JSON-RPC is the common deployment.
- The queue TTL after which a queued message expires, and the queue depth per pair, are
  open.
- The platform's tables carry `bot_id` or a key to a row that does (spec X1), so the
  audit row the kernel writes for a C3 or C6 decision can be joined to the session the
  plugin recorded.
