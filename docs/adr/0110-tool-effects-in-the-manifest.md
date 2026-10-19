# ADR-0110: Tool effects and loop guidance are declared in the manifest; the loop mechanics are the core's

Status: Accepted (2026-09-14) — the owner's decision 11 in the multi-step loop plan; implemented in the PR that carries this status line, after #480, #481, #482 merged.
Relates to: multi-step-loop-plan (in the private project log) (decisions 4, 8, 10), [plugin-contract](../architecture/plugin-contract.md) (the tool registration kind), ADR-0077 (the unified loop and its tool shortlist), ADR-0106/0107/0108 (pause, resume, approvals), the owner's rule of 2026-09-13 (vocabulary in YAML, the core carries no plugin's words).
Supersedes nothing. Narrows PR 3 of the loop plan (#481): the confirm-once mechanism stays; how a tool joins it changes.

## Context

The loop plan's acceptance run (2026-09-14) established that "find the dues, then a reminder
for each" fails for reasons that belong to the agent core, not to finance or calendar:

- the prompt handed the model the question fresh on every iteration (fixed, #482);
- the loop had no way to make a write tool ask first (fixed, #481);
- the tool shortlist could drop `ask_user` (fixed, #482).

Those fixes are generic. What is not generic is how a tool *opts in* to them:

| Where the core still knows a plugin's tool | Why it is wrong |
|---|---|
| `DEFAULT_CONFIRM_ONCE_TOOLS = {"create_reminder"}` in `kernel/governance/wiring.py` | a new plugin write tool gets the confirm-once rule only if an operator edits an env var |
| "ask first when creating several" written into one tool's description | every plugin author must remember to write the same prose |
| the prompt's cross-domain guidance names `finance_lookup`, `upcoming_dues`, `search_inbox`, `daily_plan`, `calendar_lookup` | the core carries plugin tool names and routing prose (predates the plan; #482 extended it) |
| `_REACT_CORE_TOOL_NAMES` pins `search_inbox`; `_REACT_RESERVED_TOOL_NAMES` lists plugin tools | same smell, smaller; a follow-up |

The owner's framing: *"This defines how the prompts should be reconstructed for multi-turn or
multi-task problems the agent must handle. Make it generic so all tools and plugins adopt it —
ownership of the agent core, not plugins and tools."* And on where a tool declares itself:
*"It should be in the manifest file; register_tool should take the effects into account."*

## Decision

### 1. The manifest declares each tool's effect (and, optionally, its guidance)

```yaml
provides:
  - tool
tools:
  calendar_lookup:
    effect: read
  create_reminder:
    effect: write            # changes something on the user's behalf
    confirm: once            # default for write: ask the user once per run before the first write
  daily_plan:
    effect: read
    guidance: >-             # shown in the prompt only while this tool is on the loop
      For a whole-day question ('how is my day', 'what's on today', 'plan my day') call
      daily_plan: it already merges calendar, tasks, followups and bills, so do not also call
      calendar_lookup or finance_lookup for the same question.
```

- `effect`: `read` | `write`. Required for every tool the plugin registers. (ADR-0118 adds
  `destructive`, approved per call rather than confirmed.)
- `confirm`: `once` | `never`. Only meaningful for `write`; defaults to `once`. `never` is for a
  write the user's own sentence already authorises ("forget my address" → `memory_forget`);
  the plugin says so explicitly rather than the core guessing.
- `guidance`: optional prose the core appends to the prompt while the tool is on the loop. This
  is where the cross-domain sentences move. The core renders it; it does not author it.
- `pinned`: optional, default false. The tool shortlist never drops it from the menu. For a
  tool other tools' guidance sends the model to (`search_inbox` after a finance miss,
  `research` for anything current). Added after the implementation landed: it replaced the
  core's own always-kept list, which had named `search_inbox` and `research`.
- `answers_directly`: optional, default false, `read` tools only. The tool's output is already
  the user's answer (a narrated digest, not rows to interpret). Added 2026-09-16, after the call
  trace showed a "summarize my latest emails" turn paying three model calls for one answer:
  qwen2.5:7b picked `inbox_digest`, then re-issued it despite the prompt's "do not repeat a
  completed action", and the repeat guard spent a full synthesis call to have the model copy
  the observation out. The log held 95 such forced syntheses. See §3 for what the loop does with it.

  > **Amendment (2026-09-22, owner, promo-classification PR 3b): also a `write` with
  > `confirm: never`.** The original rule said a write's output only reports what was done
  > and the model still owes the user an account of it. The probe for `trash_category` showed
  > the opposite: its output *is* the account (how many moved, a sample, how many are left,
  > how to undo), and qwen2.5's retelling in 6 of 6 runs dropped the count and the remainder
  > ("your promotional emails for this week have been moved" with 80 still in the inbox), and
  > twice said "deleted" for "moved to Trash". A write that asks first (`confirm: once`) or
  > waits on a card (`approval: pinned`, `destructive`) is not finished when it returns, so
  > those stay refused. The rule is now: `answers_directly` needs an effective gate of
  > `never`. The repeat rule matters more for a write: a second call in the same turn returns
  > the first result instead of trashing the next batch unasked.

`provides: tool` stays as the coarse declaration the drift panel already reads.

### 2. `register_tool` takes the effects into account

`PluginAPI.register_tool(name, description, call)` keeps its signature. At registration the
API looks the tool up in its own plugin's manifest:

- **declared** → the `ToolSpec` carries the declared `effect`, `confirm` and `guidance`;
- **not declared** → the registration is refused with a degraded-plugin row: *"tool X is
  registered by plugin P but not declared in its manifest"*. Refused, not silently `read`: an
  undeclared write tool would bypass the confirm-once rule, which is the failure this ADR
  exists to prevent;
- **declared but never registered** → the existing drift panel reports it, the way it reports
  a `provides` kind with no registration. The stale-handler trap the plan doc recorded (a
  manifest row pointing at code that no longer exists) is caught here, in the same place.

Core built-in tools (`react_tools.py`) declare their effects in code, because they are the
core's own: `memory_correct`, `memory_forget`, `memory_restore`, `record_feedback` are `write` with
`confirm: never` (the sentence is the authorisation); `promote_recommendation`,
`propose_skill_from_sandbox`, `invoke_pending_action` are `write` with `confirm: once` (they
act on something outside the conversation); everything else is `read`. `ToolSpec` gains the three
fields with defaults (`read`, `once`, `""`) so skills and tests that build a `ToolSpec` directly
are unchanged.

### 3. The core derives the mechanics from the declaration

- **Confirm once per run**: the tool policy's set is *every registered tool with `effect: write`
  and `confirm: once`*, read live from the plugin registry after mount.
  `IRIS_GOVERNANCE_CONFIRM_ONCE_TOOLS` becomes an operator override (add or, with `none`,
  clear), no longer the source, and the core's default names no plugin tool.
- **The prompt**: write tools are marked as such in the tool list, and one generic rule replaces
  the per-tool prose: *before a run of writes on the user's behalf, ask once with the full list
  of what you will create or change; after the answer, call the tool once per item.* The
  scratchpad rule (#482) and the pinned `ask_user` (#482) stay as they are.
- **Guidance**: the prompt appends each on-loop tool's `guidance` (deduplicated, in registration
  order) and the hardcoded cross-domain block in `_build_react_prompt` is deleted. The finance,
  email and planner sentences it held move to those plugins' manifests, unchanged in wording.
- **The shortlist**: the core keeps its own `memory_search` and `ask_user`; every other
  always-kept tool is one whose manifest says `pinned: true`. **Skill shadowing** is decided
  per turn from the pool itself: a skill tool may not take a name already in the pool
  (built-ins plus mounted plugins' tools). The static reserved-name list is gone.
- **Answers directly**: when an `answers_directly` tool is the run's first tool (asking the user
  does not count) and returns something usable (not an error or usage message), both loops end
  the run on that output, after the evaluator's post-step has had its say. A later call to it is
  one step of a larger answer, so the model still writes that answer. A *repeat* of such a tool,
  with the same arguments, returns the result already in hand: no forced-synthesis call in the
  sync loop, and no second execution in the streaming loop, which has no action ledger.
  `inbox_digest` and `trash_category` declare it.
- **A plugin agent's own loop**: an agent that runs its own loop over tools it built (the email
  agent) passes them through `PluginAPI.declare_tool`, the same function `register_tool` uses.
  Before this the email agent's loop ignored the manifest entirely: its tools carried the
  `ToolSpec` defaults, whatever the manifest said.

### 4. Migration

One core PR, with the manifests of the plugins that register tools updated in the same change
so nothing is refused at mount:

| Plugin | read | write |
|---|---|---|
| calendar | `calendar_lookup` | `create_reminder` |
| finance_workflows | `finance_lookup`, `upcoming_dues`, `portfolio`, `net_worth_trend` | — |
| email_workflows (the email tools are built by the core email library and registered by this plugin, not by `gmail`) | `search_inbox`, `read_email`, `find_attachment`, `inbox_digest`, `list_by_category` | — |
| planner | `daily_plan` | `configure_brief` (edits the brief's sections; `confirm: never`, the sentence asks for it) |
| system | `system_health` | — |
| research | `research` | — |
| code_exec | `code_exec` (sandboxed; its artifacts are outputs, nothing of the user's changes) | — |

`code_exec` as `read` is a judgment call recorded here; the owner may move it.

## Consequences

- A plugin author declares two words per tool and gets the pause-before-fan-out, the
  scratchpad continuation and the shortlist protection without knowing the core exists.
- The core's prompt stops naming any plugin tool. Its own tools it still names, because they
  are its own.
- A manifest can no longer drift from the code in the dangerous direction: an undeclared tool
  does not mount.
- Tests: manifest validation (unknown effect, `confirm` on a read tool), registry refusal and
  the drift row, the policy set derived from the registry, the prompt's write marking and
  guidance rendering (the existing plan-nudge tests move from the prompt module to the
  planner manifest), every plugin's standalone test asserting its declared tools mount.

## Open for the owner

Whether `confirm: once` should be the default for `write` (this ADR says yes: the safe default
is to ask, and a plugin opts out per tool), or whether `write` alone should mean "ask" with no
`confirm` key at all.
