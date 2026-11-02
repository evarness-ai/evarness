# Evarness Next: specification and formal schemas

Status: draft for review (2026-11-01). Precedes the ADR set. Records decisions D1 to D21
from the design discussion and gives each entity a formal schema.

## 1. Purpose

Evarness becomes the governed execution foundation for a self-hosted platform of many
bots on frontier and local models, with browser and desktop use, credential delegation
and bot-to-bot handoff. This document fixes the entities, their fields, their states and
the invariants the kernel enforces, so the ADRs and the two repositories can be cut from
it.

Writing conventions. Field names are `snake_case`. Identifiers are text ULIDs.
Timestamps are ISO 8601 in UTC, stored as text. Enumerations are closed: a value not
listed is a validation error. "Must" is an invariant with a test. "Should" is a default.
Vocabulary that exists in the code keeps the code's name: tool effects are `ToolEffect`
(`read`, `write`, `destructive`), data classes are `EgressDataClass` (`public`,
`internal`, `personal`; `secret` never enters a prompt), hook points are the names in
`docs/concepts/governance.md`, and a capability call is `capability:<domain.verb>.<method>`
(`foundation/capabilities.py`).

## 2. The boundary rule

> What the kernel must enforce lives in Evarness. What a bot is and does lives in the
> platform.

The kernel cannot enforce what it cannot see. Identity, grants, leases, memory scopes,
workflow state and audit are below the SDK. Bot definitions, the message tool, the
browser and desktop plugins, A2A adapters, the learning job for grants and the console
are above it, in a separate repository, eOS (Evarness OS), that imports only `evarness.sdk` and
`evarness.testing`. eOS is pinned to an Evarness version. "The platform" below means eOS.
"Core" below means below the SDK; each core section names the layer its table lives in,
and that layer is one every path that writes or checks the table can import (the layer
order is `[tool.importlinter]` in `pyproject.toml`: `runtime` above `agent` above
`services` above `memory` above `kernel` above `foundation`).

| Entity | Owner | Storage |
|---|---|---|
| BotDefinition | platform | `bots/<name>.yaml` in the Evarness home |
| BotIdentity | core | kernel table `bot_identity` |
| CapabilityGrant | core | kernel table `grants` |
| CredentialLease | core | kernel table `credential_leases` |
| WorkflowRun, WorkflowStep | core | tables `workflow_runs`, `workflow_steps` in `memory/state/`, beside the checkpoint store |
| ComputerSession, ComputerAction | platform plugin; lifetime held by the plugin host | plugin tables `computer_sessions`, `computer_actions` |
| Message | platform sends; core classifies and audits | platform table `messages`; one audit row per message |

The kernel tables are the ones a kernel hook reads on its own: `bot_identity` (B1),
`grants` (5.4), `credential_leases` (6.1). The workflow tables are written by the tool
runner (`agent/`), which sits above `memory/state/`; what the kernel enforces for a step
is the side-effect ledger row (W4), which is a kernel table already.

## 3. Principals and the caller chain

Every governed call carries one caller. The kernel stamps it; no caller sets it.

| Caller format | Meaning |
|---|---|
| `owner` | The one owner, on a paired device |
| `device:<device_id>` | A paired device acting without a bot |
| `bot:<bot_name>` | A call a bot's runtime starts on its own: a routine firing, a message received |
| `model:<agent_type>` | The agent loop's own tool call (stamped today by `GovernedToolRunner`) |
| `plugin:<name>` | A plugin's call through `api.tools` (stamped today by the plugin host) |
| `mcp:<client>` | An outside MCP client served by `evarness mcp serve`; `<client>` is the operator's name for it |
| `a2a:<agent_name>` | An outside agent over A2A |

`owner`, `device:`, `bot:` and `a2a:` are new; `model:`, `plugin:` and `mcp:` exist
(`agent/tool_runner.py`, `runtime/plugin_host/api.py`, `kernel/governance/caller_policy.py`).

The call context gains three fields: `bot_id`, `run_id`, `task_id`. The audit row gains
the full chain: owner, device, bot, run, step, tool, resource, action. The egress gate,
the grant hook and the lease check decide by `bot_id`, never by a tool's claim and never
by parsing the caller: a `plugin:` or `model:` call inside a bot's run carries that bot's
`bot_id`.

## 4. BotDefinition and BotIdentity

### 4.1 BotDefinition (platform, YAML)

One file per bot. Validated against this schema before any workspace is created. A bot
definition holds no grants, no leases and no credentials.

| Field | Type | Required | Constraint |
|---|---|---|---|
| `name` | slug | yes | `^[a-z][a-z0-9_-]{1,31}$`, unique |
| `version` | integer | yes | Increments on every change |
| `description` | string | yes | Shown on the status card |
| `template` | slug | no | A template this definition was created from (D21) |
| `soul` | path | yes | The bot's system prompt file, inside the workspace |
| `tiers` | map role to tier name | yes | Roles: `router`, `executor`, `judge`. Tier names from `config/llm_tiers.yaml` |
| `localities_allowed` | list of enum | yes | Subset of `local`, `trusted`, `cloud` (D5). `trusted` is new; `llm/locality.py` has `local` and `cloud` |
| `plugins` | list of `{name, tools}` | yes | Only declared tools reach this bot; `tools: all` is refused |
| `capabilities` | list of `domain.verb` | no | Capabilities the broker may lease to this bot (D13) |
| `channels` | list of channel names | yes | From `config/channels.yaml` |
| `routines` | list of Routine | no | See 4.2 |
| `budget` | Budget | yes | See 4.3 |
| `memory` | `{read: [own, shared], write: [own]}` | yes | `personal` is never listed (D17) |
| `computer_use` | ComputerUsePolicy | no | See 4.4 |
| `messaging` | MessagingPolicy | no | See 4.5 |

### 4.2 Routine

| Field | Type | Required | Constraint |
|---|---|---|---|
| `name` | slug | yes | Unique within the bot |
| `cron` | string | yes | Five fields; minimum interval 15 minutes |
| `prompt` | string | yes | Display-masked in the ledger |
| `tools` | list | no | Subset of the bot's tools |
| `enabled` | boolean | yes | Default false until the owner enables it |

### 4.3 Budget

| Field | Type | Required | Constraint |
|---|---|---|---|
| `tokens_per_day` | integer | yes | Enforced by the cost limiter at `pre_llm_call` |
| `cost_per_day` | decimal | no | In the owner's currency; no implicit FX |
| `max_parallel_runs` | integer | yes | 1 to 4; the workflow store refuses a claim above it |

### 4.4 ComputerUsePolicy

| Field | Type | Required | Constraint |
|---|---|---|---|
| `browser` | enum | yes | `none`, `owner_profile`, `bot_profile` (D6, D20) |
| `allowed_domains` | list of host patterns | if browser not `none` | Exact hosts or `*.example.com`; no bare `*` |
| `transaction_domains` | list of hosts | no | On these, `submit` has effect `destructive` |
| `desktop` | boolean | yes | Default false (D7) |

### 4.5 MessagingPolicy

| Field | Type | Required | Constraint |
|---|---|---|---|
| `may_send_to` | list of bot names | no | Empty means the bot sends to nobody |
| `may_receive_from` | list of bot names | no | Empty means the bot receives from nobody |
| `max_hops` | integer | yes | 1 to 4, default 2 |
| `rate_per_hour` | integer | yes | Per target bot, default 20 |

### 4.6 BotIdentity (core, kernel table `bot_identity`)

Written once when the platform registers a bot through the SDK. The only core row a
new bot creates.

| Column | Type | Constraint |
|---|---|---|
| `bot_id` | TEXT PK | ULID |
| `name` | TEXT | Unique, equals the definition's `name` |
| `status` | TEXT | `active`, `disabled`, `revoked` |
| `workspace_path` | TEXT | Under the Evarness home, one folder per bot: exactly `<home>/bots/<name>` |
| `vault_namespace` | TEXT | Equals `bot:<name>`; unique |
| `memory_scope` | TEXT | Equals `bot:<name>`; unique |
| `definition_sha256` | TEXT | Hash of the validated definition in force |
| `created_at`, `updated_at` | TEXT | |
| `revoked_at` | TEXT | Null unless revoked |

States. `active` and `disabled` are the owner's switch, in both directions. `revoked` is
terminal: it sets `revoked_at` and runs the cascade in X5. A `disabled` bot is denied
like a revoked one (B1) but keeps its grants, leases and sessions.

Invariants.

- B1. A call whose `bot_id` names an identity that is not `active` is denied at
  `pre_tool_use` and `pre_llm_call`, mid-run included.
- B2. A definition change never changes a grant or a lease. A grant whose tool is no
  longer in the definition becomes `dormant` and stays dormant if the tool returns.
- B3. The workspace, the vault namespace and the memory scope of two bots never share a
  path or a name.
- B4. Adding a bot adds a definition file, a workspace folder and one identity row. If
  adding a bot needs a code change, the seam is in the wrong place.

## 5. CapabilityGrant (core, kernel table `grants`)

A grant is the owner's remembered answer to an approval card (D9). It relaxes approval
only. It never changes classification, egress or locality (D10). A card is a row of
`approval_queue` (`kernel/governance/approvals/schema.sql`); `approval_id` below is its
key.

### 5.1 Approval answers

| Answer | Writes a grant | Scope of the grant |
|---|---|---|
| `once` | no | |
| `task` | yes | `task_id` set; the row is set `expired` when the run reaches a terminal state |
| `always` | yes | `task_id` null; `expires_at` at most 30 days (D12) |
| `deny_remember` | yes, `decision: deny` | The card carries the same `task` or `always` choice; the rules of that row apply |
| `not_now` | no | The call is rejected, nothing is remembered |

### 5.2 Schema

| Column | Type | Constraint |
|---|---|---|
| `grant_id` | TEXT PK | |
| `bot_id` | TEXT | FK `bot_identity`; never null, never a wildcard |
| `subject_kind` | TEXT | `tool`, `capability`, `message` |
| `subject` | TEXT | Tool name, `domain.verb`, or target bot name |
| `effect` | TEXT | `ToolEffect` minus one: `read`, `write`. Never `destructive` |
| `scope_json` | TEXT | JSON object of literal matchers, see 5.3 |
| `data_class_max` | TEXT | `EgressDataClass`: `public`, `internal`, `personal`, in that order; the call's class must be at or below it |
| `decision` | TEXT | `allow`, `deny` |
| `granted_by` | TEXT | `owner`, `owner_via_proposal` |
| `approval_id` | TEXT | FK `approval_queue`; the card the owner answered; required |
| `task_id` | TEXT | Null for `always`; set for `task` |
| `created_at` | TEXT | |
| `expires_at` | TEXT | Required for every row, `task` included; at most `created_at` plus 30 days |
| `last_used_at` | TEXT | |
| `use_count` | INTEGER | Default 0 |
| `status` | TEXT | `active`, `expired`, `revoked`, `dormant` |
| `revoked_at`, `revoke_reason` | TEXT | |

States. A row starts `active`. It becomes `expired` when it is read after `expires_at`,
or when its `task` run ends; `revoked` when the owner revokes it or X5 runs; `dormant`
under B2. The three are terminal for the row: a later answer writes a new row (G4).

### 5.3 Scope matchers

`scope_json` is a flat object. Every key present must match the call exactly. Allowed
keys: `host`, `path_prefix`, `mailbox`, `label`, `target_bot`, `resource`,
`arg:<name>` for one literal argument value. No regular expressions, no wildcards except
the leading `*.` on `host`. An empty scope object is refused for `write`.

### 5.4 Matching algorithm, deterministic

1. Load grants for `bot_id`, `subject_kind`, `subject` with status `active` and
   `expires_at` in the future.
2. If any `deny` row matches, deny and audit. Deny wins.
3. If any `allow` row matches and the call's data class is at or below
   `data_class_max`, allow without a card, update `last_used_at` and `use_count`.
4. Otherwise raise the card. No match means ask.

Invariants.

- G1. No grant exists with `effect = destructive`. The schema check and the hook both
  refuse it.
- G2. No grant is consulted for a bot other than its `bot_id`.
- G3. A grant never allows a model call or an egress that the gate refuses. The gate runs
  first.
- G4. On `expires_at`, the next matching call raises one re-confirmation card. The answer
  writes a new grant row; the old row stays for the ledger.
- G5. The model never reads the grants table. It receives `allowed` or `ask`.
- G6. A learning proposal (D11) writes no grant. It writes an approval card. The owner's
  answer writes the grant with `granted_by = owner_via_proposal`.

### 5.5 Learning thresholds (D11)

| Signal | Proposal |
|---|---|
| 5 consecutive `once` approvals, same bot, subject and scope, zero rejections, zero failed side effects | Propose `always` |
| 2 rejections, same scope | Propose `deny_remember` |
| Active grant unused for 30 days | Propose revoke |
| Calls under a grant start to miss its scope | Propose a narrower grant |

## 6. CredentialLease (core, kernel table `credential_leases`)

A bot never holds a secret. It asks the broker to perform a method of a capability. The
broker issues a lease, resolves the `vault://` handle inside the kernel, performs the
method and returns a sanitized result (D13).

### 6.1 Broker interface

```
broker.execute(
    principal   = "bot:finance",
    capability  = "mail.read",
    method      = "search",
    resource    = "work-mail",
    run_id      = run_id,
    task_id     = task_id,
    args        = {...},
)
```

The broker is in `services`. It writes `credential_leases` and resolves the handle
through the vault, both in the kernel below it. Its enforcement point, the lease check,
is a kernel hook at `pre_tool_use` on the capability call
`capability:mail.read.search` (`capability_tool_name` in `foundation/capabilities.py`).
The hook reads only the `credential_leases` table; the kernel never imports the broker,
as `caller_policy.py` already asks for a policy rather than importing the plugin host.

### 6.2 Schema

| Column | Type | Constraint |
|---|---|---|
| `lease_id` | TEXT PK | |
| `bot_id` | TEXT | FK `bot_identity` |
| `vault_namespace` | TEXT | Must equal the bot's `vault_namespace` |
| `handle` | TEXT | A `vault://` handle (`VAULT_PREFIX` in `kernel/governance/vault/handle.py`) inside that namespace; layout in 12 |
| `capability` | TEXT | `domain.verb` listed in the bot's definition |
| `methods` | TEXT | JSON list; the capability's methods this lease covers |
| `resource` | TEXT | Account label, for example `work-mail` |
| `run_id`, `task_id` | TEXT | Required |
| `issued_at` | TEXT | |
| `expires_at` | TEXT | Default `issued_at` plus 15 minutes; renewed by 15 minutes at a time while the run is not terminal |
| `max_uses` | INTEGER | Default 100 |
| `use_count` | INTEGER | Default 0 |
| `status` | TEXT | `active`, `expired`, `revoked`, `exhausted` |
| `revoked_at`, `revoke_reason` | TEXT | |

States. `active` becomes `expired` when read after `expires_at`, `exhausted` when
`use_count` reaches `max_uses`, `revoked` when the bot is revoked (L2) or the run
reaches a terminal state (`revoke_reason = run_finished`). All three are terminal; the
broker issues a new lease.

Invariants.

- L1. A lease is refused before the handle is resolved when `vault_namespace` is not the
  bot's own. A compromised research bot that knows the finance handle gets a denial row,
  not a secret.
- L2. Revoking a bot revokes its leases in the same transaction.
- L3. The secret value never appears in a tool argument, a tool result, a prompt or a
  log. The `pre_response` hook already scans for credentials; the lease path adds no
  exception.
- L4. The file vault derives its key with a random salt per vault, stored beside the
  database. The salt is not secret. This describes the file backend of D14; today's
  `VaultStore` uses the Fernet master key directly, with no derivation.

## 7. WorkflowRun and WorkflowStep (core, `workflow_runs`, `workflow_steps` in `memory/state/`)

The authoritative state of long-running work (D16). Built on what exists: the checkpoint
store (`memory/state/store.py`, keyed by `(run_id, step_id)`), the continuation registry
(`runtime/continuations.py`, one open question per session, used for `waiting_input`)
and the side-effect ledger and its probes (`kernel/governance/side_effects/`). The
heartbeat scheduler decides when work starts. The workflow store decides what state it
is in. A worker supervisor claims, runs, retries and cancels.

### 7.1 WorkflowRun

| Column | Type | Constraint |
|---|---|---|
| `run_id` | TEXT PK | |
| `bot_id` | TEXT | FK `bot_identity` |
| `kind` | TEXT | `turn`, `routine`, `handoff`, `resume` |
| `parent_run_id` | TEXT | Set for a handoff; the ledger links parent and child |
| `a2a_task_id` | TEXT | Set when the run is an A2A Task, inbound or outbound (D4) |
| `origin` | TEXT | A channel name or `bot:<name>` |
| `goal_masked` | TEXT | Display-masked summary |
| `status` | TEXT | See 7.3 |
| `claimed_by` | TEXT | Worker id |
| `claimed_at`, `claim_expires_at` | TEXT | Worker claim, default 5 minutes, renewed each step. "Lease" is kept for credentials |
| `checkpoint_id` | TEXT | The checkpoint the run resumes from, in the form `approval_queue.checkpoint_id` carries |
| `budget_spent_tokens` | INTEGER | |
| `created_at`, `updated_at`, `finished_at` | TEXT | |
| `error` | TEXT | |

### 7.2 WorkflowStep

| Column | Type | Constraint |
|---|---|---|
| `step_id` | TEXT PK | |
| `run_id` | TEXT | FK `workflow_runs` |
| `seq` | INTEGER | Unique within the run; the INTEGER `step_id` the side-effect ledger, the checkpoint store and the approval queue carry |
| `subject` | TEXT | Tool name or `capability:<domain.verb>.<method>` |
| `effect` | TEXT | `ToolEffect`: `read`, `write`, `destructive`; an undeclared effect is refused (D15) |
| `side_effect_id` | TEXT | FK `side_effect_ledger`; required for every `write` and `destructive` |
| `idempotency_key` | TEXT | Set when the target supports one |
| `approval_id`, `grant_id`, `lease_id` | TEXT | Whichever authorised the step |
| `status` | TEXT | `pending`, `ran`, `failed`, `ambiguous`, `skipped` |
| `started_at`, `finished_at` | TEXT | |
| `result_digest` | TEXT | SHA-256 of the result, never the result |

Step states. `pending` is written before the tool runs. `ran` and `failed` are the
outcome. `ambiguous` is a write-ahead row with no completion (the ledger row still
`pending` after a crash). `skipped` is a step the owner or a denial removed. The ledger's
own statuses are `pending`, `completed`, `not_completed`, `ambiguous`, `error`; W2 maps
them.

### 7.3 Run states and transitions

```
pending -> claimed -> running -> completed
             |          |-> waiting_approval -> running | cancelled   (answer, or timeout)
             |          |-> waiting_input    -> running | cancelled
             |          |-> suspended        -> pending   (process restart, claim expired)
             |          |-> uncertain        -> running | cancelled   (owner decides)
             |          |-> failed           (retries exhausted, or budget spent)
             |-> suspended                   (claim expired before the first step)
any non-terminal -> cancelled   (owner, or bot revoked)
```

Terminal states: `completed`, `failed`, `cancelled`.

Claiming is one SQL statement: update to `claimed` where `status = 'pending'` and the
worker claim is null or expired, and the bot's active runs are below
`max_parallel_runs`. Two workers cannot both win. The supervisor marks a `claimed` or
`running` row whose `claim_expires_at` has passed `suspended`, then `pending`, so a
dead worker's run is claimed again.

A `waiting_approval` run leaves on the card's answer or on its `timeout_at`
(`approval_queue.policy_on_timeout`, default `fail_closed`): an allow runs the step, a
deny or a timeout marks the step `failed`. A failed step is retried inside `running`; the
run goes `failed` when the retries are exhausted or the budget is spent.

### 7.4 Resume rules

- W1. A step with status `ran` is never run again.
- W2. A step with status `ambiguous` (the write-ahead row exists, no completion) is
  checked with the tool's `verify:` probe (`ProbeResult` in
  `kernel/governance/side_effects/probes.py`). `completed`: mark `ran`. `not_completed`:
  mark `failed`, the run may retry. `ambiguous`, which is also what no probe, an unknown
  probe and a probe that raises return: the run becomes `uncertain` and waits for the
  owner.
- W3. A run that resumes re-checks every grant and lease it used. An expired or revoked
  one stops the run at `waiting_approval`.
- W4. Writing the workflow row and the ledger row is part of the step. If the workflow
  store or the side-effect ledger is unavailable, a `write` or `destructive` step fails
  closed before the tool runs.

## 8. ComputerSession and ComputerAction (platform plugin)

A browser or desktop session a bot drives through an MCP server (D6, D7, D8, D20). The
plugin host holds the session's lifetime. The plugin records the actions. The tables are
the plugin's, so the kernel does not read them: C3 and C6 are kernel hooks on the call
(`pre_llm_call`, `pre_tool_use`); C1, C2, C4 and C5 are the plugin host's, with a test in
`evarness.testing`.

### 8.1 ComputerSession

| Column | Type | Constraint |
|---|---|---|
| `session_id` | TEXT PK | |
| `bot_id`, `run_id` | TEXT | Required; one bot, one run |
| `kind` | TEXT | `browser`, `desktop` |
| `mode` | TEXT | `owner_profile`, `bot_profile` |
| `profile_path` | TEXT | For `bot_profile`, under the bot's workspace; never shared |
| `allowed_domains` | TEXT | JSON list, copied from the definition at open |
| `transport_session_id` | TEXT | The long-lived MCP stdio session |
| `status` | TEXT | `open`, `paused_for_owner`, `closed`, `revoked` |
| `opened_at`, `expires_at`, `closed_at` | TEXT | `expires_at` follows the run's `claim_expires_at`; a session whose run is terminal is `closed` |
| `action_count` | INTEGER | |
| `recording_path` | TEXT | Optional screenshots and action log, inside the workspace |

States. `open` becomes `paused_for_owner` under C1 or C2 and returns to `open` when the
owner resumes it; `closed` when the run ends, the owner closes it or `expires_at`
passes; `revoked` under C5. `closed` and `revoked` are terminal.

### 8.2 ComputerAction

| Column | Type | Constraint |
|---|---|---|
| `action_id` | TEXT PK | |
| `session_id` | TEXT | FK `computer_sessions` |
| `seq` | INTEGER | Unique within the session |
| `kind` | TEXT | `navigate`, `read`, `screenshot`, `click`, `type`, `key`, `submit`, `download` |
| `effect` | TEXT | Derived, see 8.3 |
| `target` | TEXT | URL, selector or coordinates; display-masked |
| `approval_id`, `grant_id` | TEXT | Whichever authorised it |
| `screenshot_digest` | TEXT | SHA-256 only |
| `ocr_verdict` | TEXT | `clean`, `personal`, `not_run` |
| `status` | TEXT | `ran`, `denied`, `failed`, `paused` |
| `at` | TEXT | |

### 8.3 Effect derivation, deterministic

| Kind | Effect |
|---|---|
| `navigate`, `read`, `screenshot` | `read` |
| `click`, `type`, `key` | `write` |
| `submit` on a host in `transaction_domains` | `destructive` |
| `submit` elsewhere | `write` |
| `download` | `write` |

Invariants.

- C1. `navigate` to a host outside `allowed_domains` is denied. A redirect that lands
  outside them sets the session to `paused_for_owner`.
- C2. A login page or a 2FA prompt sets `paused_for_owner`. The owner acts; the bot
  never types a credential.
- C3. Before a screenshot reaches a `cloud` or `trusted` tier, local OCR runs and the
  owner-PII matchers read the text. `personal` blocks the image. `local` tiers skip the
  check.
- C4. Two bots never share a profile, a session or a transport.
- C5. Revoking the bot sets every open session to `revoked` and closes the transport, as
  the plugin host's part of X5.
- C6. A `write` or `destructive` action has a write-ahead row like any tool step.
- C7. Page text, OCR text and a download are `content: external` (`PluginManifest`): the
  untrusted-content envelope and the tripwire apply, and an instruction found there never
  bypasses 5.4.

## 9. Message (platform, A2A-shaped)

Bots never call each other. They send messages (D3). A message is a `write` tool call
by the sender and a new run for the receiver. Objects follow A2A 1.0 so the same shape
goes over the wire later (D4).

| Field | Type | Constraint |
|---|---|---|
| `message_id` | ULID | |
| `from_bot`, `to_bot` | bot name | `to_bot` in sender's `may_send_to`; `from_bot` in receiver's `may_receive_from`; both resolve to a `bot_id` at send |
| `a2a_task_id` | string | Set for a handoff; the receiver's run carries it |
| `parts` | list of Part | See below |
| `hop_count` | integer | Incremented by each forward; refused above the receiver's `max_hops` |
| `created_at`, `delivered_at` | timestamp | |
| `status` | enum | `queued`, `delivered`, `refused`, `expired` |
| `refusal_reason` | string | |

Part: `{kind: text | artifact | data, data_class: public | internal | personal,
digest, size_bytes, inline_or_path}`. An artifact crosses as a copy into the receiver's
workspace (D19), never as a shared path.

States. `queued` becomes `delivered` when the receiver's run is created, `refused` under
M1 or M2, `expired` when the queue TTL passes (12). All three are terminal.

Invariants.

- M1. Each part is classified before send. The send is a `write` with `subject_kind`
  `message` whose class is the highest part class, so 5.4 applies: a `personal` part
  needs a card or a grant with `data_class_max = personal` for that `target_bot`. A
  refused send is audited.
- M2. Per-pair rate limit and `hop_count` are checked before any model sees the message.
- M3. The receiver treats every part as external content: tripwire scan and re-entry scan
  apply.
- M4. A group room is a view over this table. Bots never read the room.

## 10. Cross-entity invariants

- X1. Every row above carries `bot_id`, or a foreign key to a row that does
  (`workflow_steps` through `run_id`, `computer_actions` through `session_id`, `messages`
  through the two bot names), and every audit row carries the caller chain.
- X2. The audit log is hash-chained. The proof bundle verifier checks the chain (D18).
- X3. MCP signing runs with `mode: enforce` (`SigningMode` in
  `kernel/governance/mcp_signing/config.py`; the shipped default is `shadow`) for any bot
  with `computer_use` or `messaging` set.
- X4. An MCP tool without a declared effect is denied (D15).
- X5. Revocation of a bot cascades. The core part is one transaction: identity, grants,
  leases, runs to `cancelled`. The plugin host then sets sessions to `revoked` (C5); until
  it has, B1 denies every call the session makes. Audit rows stay.

## 11. Verification matrix

Each row is an acceptance test. The platform is not ready while any row fails.

| Scenario | Expected | Invariant |
|---|---|---|
| Bot reads another bot's memory scope | Denied and audited | B3 (D17) |
| Bot uses another bot's vault handle | Denied before resolution | L1 |
| Cloud tier requested for personal data | Denied, no grant can allow it | G3 |
| Hostile text on a web page asks for an action | No write without a card or a matching grant | C7, 5.4 |
| Worker killed after a payment submit, before completion | On resume, step `ambiguous`, probe or owner decides, never a repeat | W1, W2 |
| MCP tool with undeclared effect | Denied | X4 |
| Owner revokes a bot mid-run | Next call fails, sessions closed | B1, X5, C5 |
| Process restarts mid-workflow | Run resumes from checkpoint, grants re-checked | 7.3, W3 |
| Learning job proposes a grant | A card, not a grant | G6 |
| Workflow store or side-effect ledger unavailable | Write steps fail closed | W4 |
| Message crosses a forbidden pair | Refused and audited | M1, M2 |
| Personal screenshot to a cloud tier | Blocked by OCR gate | C3 |
| Write ran without a fresh card | Traces to a grant in scope and inside TTL | 5.4, G4 |

## 12. Open points

- The OCR engine for C3 and its measured miss rate on the owner's screens.
- Whether `max_parallel_runs` above 4 is ever allowed on one laptop. Measure with five
  bots first.
- Which A2A binding to serve first: JSON-RPC is the common deployment.
- The handle layout for a vault namespace. `VaultStore` keys secrets by one flat
  `vault://<handle>` string with no namespace; L1 needs the namespace readable from the
  handle (for example `vault://bot:<name>/<label>`) or a namespace column in the vault.
- Whether a revoked bot's `name` may be reused. `bot_identity.name`, `vault_namespace`
  and `memory_scope` are unique and the revoked row stays, so today it may not.
- The queue TTL after which a `queued` message is `expired`, and the queue depth per pair.
- Whether MessagingPolicy needs a per-pair data class ceiling of its own, or whether a
  grant's `data_class_max` on `subject_kind = message` (M1) is enough.
- What an unavailable audit log does to a `read` step and a model call. W4 covers the
  workflow store and the ledger; `audit/digest.py` today raises `AuditKeyUnavailable` only
  for a missing key.
- Whether a `disabled` bot's leases expire on their own clock or are revoked with the
  switch.

## 13. Decision index

D1 bot as workspace. D2 one owner. D3 messages, not calls. D4 A2A objects inside.
D5 generic provider and `trusted` locality. D6 owner-held browser profile first.
D7 OCR gate on screenshots. D8 long-lived MCP sessions. D9 five approval answers.
D10 grant limits. D11 grant learning. D12 30-day TTL, model never sees grants.
D13 leases and broker. D14 vault backends stay pluggable. D15 fail closed on undeclared
effect, write-ahead on every write. D16 durable workflow store. D17 memory scopes.
D18 audit chain, MCP signing enforce, planner validation. D19 artifact handoff by copy.
D20 per-bot browser session. D21 bot templates.
