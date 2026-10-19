# ADR-0074: Per-agent console — an ops dashboard per registered agent

Status: Accepted (2026-06-22) — slices landing on `feat/agent-console-*`

**Status pass (2026-10-18):** the decision holds; the code now lives in `agent/agent_metadata.py`, `runtime/agent_console.py`, `harness_api/agent_routes.py`. one drift: per ADR-0120 toggle edits are saved in the settings store, not `config/agent_settings.local.yaml`

Context: ADR-0073 shipped the Action Center (pending actions across agents) and
reserved a per-agent console as Phase 2. The ask (original design review): "each
agent should have a dashboard." Today agents are runtime-only — registered in
`bootstrap.py` via `AgentExecutor.register(name, handler)` — with no metadata and
no way for the Web UI to enumerate them. There is no `GET /agents`.

Decisions (design review, 2026-06-22):

- **Ops-focused MVP**, not metrics or config first. Each agent's dashboard shows
  what an operator needs to keep it running: its **pending actions**, **recent
  runs**, **last error**, and its **key stores** (finance: accounts + statement
  inventory). It leans on data already exposed (`/actions`, `/tasks`, finance
  digests, heartbeat runs) — minimal new instrumentation. Metrics (trace
  aggregation) and per-agent settings are deferred follow-ups.
- **Enumerate from the runtime registry**, not a YAML manifest. `GET /agents`
  serializes `AgentExecutor.registered_agents()` so the console reflects exactly
  what is wired in this process — single source of truth, no drift.

## Decision

### 1. `GET /agents` — the runtime registry, with presentation metadata

The live registry (`registered_agents()`) is the source of truth for *which*
agents exist. A small static **catalog** (`src/iris_harness/core/agent_metadata.py`)
supplies presentation metadata — `title`, `description`, and the optional
`source_kind` that ties an agent to its pending actions (finance →
`finance-statements`). The endpoint merges them: every registered agent is
returned; ones without catalog metadata fall back to name-only. Agents in the
catalog but not registered in this process are omitted (the registry wins).

```python
GET /agents -> { "agents": [
  {"name": "finance", "title": "Finance",
   "description": "Net-worth / portfolio / dues digest over the local store",
   "source_kind": "finance-statements"},
  {"name": "email", "title": "Email", "description": "...", "source_kind": null},
  ... ] }
```

`source_kind` is the join key the dashboard uses to filter `/actions` and
`/tasks` to that agent's items — the ADR-0073 provider seam, reused.

### 2. Per-agent dashboard data (slice 2)

`GET /agents/{name}` returns the ops snapshot, composed from existing surfaces so
no new persistence is added:

- **pending actions** — `TaskStore.list(source_kind=…, has_action=True)` (open).
- **recent runs / last error** — heartbeat runs filtered to the agent's tick(s)
  (finance → `finance_ingest_tick`); reuses the in-memory run history.
- **key stores** — agent-specific. Finance is the reference: account count +
  statement inventory (reusing ADR-0015 `build_statements_digest`).

Non-finance agents render the generic panels (pending actions + recent runs) and
omit the store panel until they grow one. 404 for an unregistered name.

### 2c. Non-web surfaces — the console is not web-only

Like the Action Center, the per-agent console is a harness capability, not a web
feature. The composition lives in `src/iris_harness/agent_console.py` (`list_agents`,
`agent_pending_actions`, `agent_settings`, renderers) — mostly composable from the
catalog + config + task store, so it works without a live runtime. Three surfaces
share it: the API (`GET /agents`, `GET /agents/{name}` — richest, with the live
registry + run history), a chat ReAct tool (`agents`, optional `{"name": …}`), and
the CLI (`iris agents [name]`). `GET /agents/{name}`'s settings block reuses
`agent_settings`. React only renders.

### 3. WebUI (slice 3)

An `/agents` index (cards from `GET /agents`) and `/agents/{name}` dashboard
routes — thin renderers, matching the Health/Action-Center pattern. The finance
card deep-links its pending actions to the Action Center.

### 4. Per-agent settings — reflect now, edit curated next (design review 2026-06-22)

The hard constraint: most agent settings are **start-time** today — `llm_tiers.yaml`
is read once at startup (only skills + escalation priors hot-reload), and behaviour
toggles are env flags. Some flags are re-read at use-time (the finance tick reads
`IRIS_FINANCE_AUTO_INGEST`/`IRIS_FINANCE_DUES_FROM_EMAIL` each run → a change applies
next run); others are wired at startup (`IRIS_EMAIL_SEMANTIC_SEARCH` → restart). A
settings UI that claimed instant apply for everything would be lying.

**Decision: reflect existing settings read-only now; make a curated subset editable
next.** This honours existing config (it reflects `llm_tiers.yaml` + env flags +
`heartbeats.yaml`, inventing nothing) and is truthful about apply-timing.

- `GET /agents/{name}` gains a `settings` block: `llm` (tier+model per the agent's
  intents, via `tier_router.get_tier`), `toggles` (each env flag's current value,
  default, and **when a change applies** — "next run" | "restart"), and `heartbeats`
  (cadence). The catalog (`AgentMeta`) carries the agent→intents and agent→toggles
  reference data; the live tier router + env are the source of truth for values.
- The webui dashboard renders a read-only **Settings** panel.

**Curated-edit writes (SHIPPED).** Only the declared `AgentMeta.toggles` are
editable. `src/iris_harness/agent_settings_store.py` persists them to a gitignored
`config/agent_settings.local.yaml`, seeds them into `os.environ` at startup
(`apply_overrides_to_env`, called first thing in `build_runtime` so both use-time
and start-time reads see them), and `set_toggle` hot-applies live — a "next run"
flag takes effect on the next use-site read; a "restart" flag is flagged
`restart_required`. Surfaces: `PATCH /agents/{name}/settings` (write-gated, only
toggles the agent owns) and `iris agents <name> --set KEY=on|off` (CLI, no server).
Writes are **never a chat tool** — a model must not flip the user's config. No
invasive rewire of every flag read — that full hot-apply approach was rejected as
too large a blast radius. LLM-tier and schedule edits remain future work.

## Alternatives considered

- **YAML manifest of agents** — more presentation control but a second source of
  truth that drifts from the runtime; rejected per the "registry wins" decision.
- **Metrics-first dashboard** — was deferred as the MVP, now SHIPPED as an add-on
  (§5). Crucially it needed NO new instrumentation: `agent_metrics` rolls up the
  existing learning-intelligence outcome matrix (per intent x tier) over each
  agent's `intents`. `GET /agents/{name}/metrics` (+ chat/CLI detail) report
  volume, success rate, user-correction rate, avg tokens, and a per-tier split —
  no Phoenix, no opt-in flag. The static settings tier (`get_tier`) and the
  measured resolved tier can differ; the metrics show what actually ran.
- **Per-agent settings now** — settings live across YAML/env today and need a
  config surface + write story; deferred.

## Security & privacy

Read-only. `GET /agents` exposes agent names/descriptions only — no secrets, no
statement contents (the statement-inventory panel reuses the privacy-safe
ADR-0015 digest: counts + dates, never amounts or filenames).

## Testing

- `GET /agents` merges registry + catalog; unregistered catalog entries omitted;
  registered-without-metadata returns name-only.
- `GET /agents/{name}` composes the snapshot; 404 for unknown.

## Rollout

Read-only; no flags. Slices: (1) `GET /agents` + metadata catalog; (2)
`GET /agents/{name}` ops snapshot; (3) webui index + dashboard. Metrics + settings
are separate follow-ups.
