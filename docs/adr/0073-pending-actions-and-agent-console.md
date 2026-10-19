# ADR-0073: Pending actions — background jobs surface actionable blockers as tasks, with an Action Center and (later) a per-agent console

Status: Accepted (2026-06-22) — slices 1-3 landing on the `feat/pending-actions-*` stack

**Status pass (2026-10-18):** the decision holds; the code now lives in `services/tasks/pending_actions.py`, `runtime/action_center.py`, `harness_api/action_center_routes.py`. the finance provider is in the private tree

Update (2026-06-22, design review): the producer is generalized into a **provider
seam** (any agent implements `PendingActionProvider`; a generic reconciler raises/
resolves its tasks), and the Action Center is a **unified** surface that folds in
Health's actionable checks. See §2a/§2b.

Context: IRIS's background autonomy can already *do* the work but cannot *ask for
help*. The daily `finance_ingest_tick` heartbeat (07:00) runs the full
ingest → extract path across connected accounts, but when it hits a wall the
failure is recorded only as a status column in `finance.db` plus a log line:

- **Missing/wrong password** → `Statement.password_status=FAILED`,
  `extraction_status=FAILED` (e.g. an ICICI credit-card statement whose vault
  secret was never set). The chat answer to "what's my card balance?" now
  *explains* this (issue 0016), but only reactively — the user has to ask.
- **Unknown institution / unrecognised document** → stored with
  `institution_id=None`, `document_type=OTHER`, or skipped entirely
  (`src/iris_harness/finance/ingest.py`).
- **Heartbeat run results** are kept **in-memory only** (`HeartbeatScheduler._runs`)
  and never persisted or surfaced.

Net effect: the only way the user learns a statement is blocked is to stumble on
it in chat. The ask (from the design review): when a routine/heartbeat discovers
an actionable blocker — a missing password, a new document type, a failed
extraction — it should become a **pending action** the user can see and resolve
from the UI immediately, instead of being discovered through conversation.

Three forks were decided up front (design review, 2026-06-22):

- **Backbone = the Tasks subsystem**, not a health-style projection or a new
  bespoke store. Background jobs write actionable items into the existing task
  store (`src/iris_harness/tasks/`), which already has lifecycle, dedup, and `wait_for`
  auto-resolution. Chosen for one inbox + history + dismiss/snooze.
- **Action Center first.** Phase 1 closes the gap (ingestion → tasks + a webui
  Action Center). Per-agent console (Phase 2) and settings + UI-triggered
  remediation (Phase 3) follow.
- **Surface + safe actions.** The UI may *trigger* non-secret remediations
  (re-extract, register institution) behind the write gate; **secret entry
  (passwords) stays local CLI** — this is a privacy-first local assistant and
  secrets must not cross the web boundary.

## Decision

### 1. Pending actions are Tasks carrying an `action`, raised by background jobs

We reuse `iris_harness.tasks` rather than invent a parallel concept. A **pending action**
is simply a `Task` that (a) was raised by the system (a non-`manual`
`source_kind`) and (b) carries a new `action` field — a remediation CTA. The
Action Center is the projection "open tasks that carry an `action`"; the user's
own todos (no action, `source_kind="manual"`) are unaffected and can be shown or
hidden separately. This is the refinement that keeps system blockers from
polluting the personal todo list while still sharing one store and one history.

New on `Task` (`src/iris_harness/tasks/models.py`):

```python
ActionKind = Literal[
    "copy_command",   # show a command for the user to run locally (e.g. set-password)
    "re_extract",     # safe: re-run extraction for a statement
    "register",       # safe: open the institution/account registration flow
    "review",         # safe: open the offending item in the UI for inspection
]

class TaskAction(BaseModel):
    kind: ActionKind
    label: str                       # button / link text
    command: str | None = None       # for copy_command — the exact CLI to run
    target_id: str | None = None     # statement id / institution id the action operates on
    safe: bool = False               # True => UI may execute behind the write gate

class Task(BaseModel):
    ...
    action: TaskAction | None = None  # set on system-raised pending actions
```

A task is a "pending action" iff `action is not None`. `safe=False` actions
(e.g. `copy_command` for `set-password`) are **display-only** in the UI: the user
runs the command locally. `safe=True` actions (`re_extract`, `register`,
`review`) may be invoked from the UI behind `IRIS_WEBUI_ALLOW_WRITES`.

### 2a. A generic provider seam — any agent can raise pending actions

The mechanism is domain-agnostic and lives in the tasks backbone
(`src/iris_harness/tasks/pending_actions.py`); the policy lives in each domain's provider.

```python
@runtime_checkable
class PendingActionProvider(Protocol):
    source_kind: SourceKind                       # the slice of the task store it owns
    def desired_actions(self) -> list[DesiredAction]: ...   # what current state calls for
    def invoke(self, task: Task) -> str: ...               # run a safe action server-side

def reconcile(provider, task_store) -> PendingActionsSummary:
    # raise the desired set (dedup upsert, first-write-wins) and complete any active
    # action of THIS provider's source_kind whose dedup_key is no longer desired.
```

`reconcile` only ever touches tasks of the provider's own `source_kind`, so
providers never step on each other. Adding an agent's actions (email re-auth,
coding "PR needs review", calendar "ambiguous event") is **a new provider, not new
plumbing**, and `POST /actions/{id}/invoke` dispatches to the owning provider by
`source_kind`. Finance is the first provider (`FinancePendingActionProvider`);
`sync_finance_pending_actions` is the thin `reconcile(provider, store)` wrapper the
ingest tick calls.

### 2b. The Action Center is unified — Health folds in at the read layer

Two "needs attention" surfaces exist: persisted action-tasks (this ADR) and
**Health** alerts (ADR-0069 — services/credentials/hardware, a recompute-on-read
projection that auto-clears). The Action Center unifies them, but **does not copy
Health's projection into the task store** — that would reintroduce the staleness a
projection avoids. Instead the two are merged at the **read/presentation** layer:

- `GET /actions` returns persisted action-tasks (mapped) **∪** pending actions
  *synthesized on read* from the live Health snapshot's red+actionable checks
  (`src/iris_harness/health/pending_actions.py`, slice 4). Health items are ephemeral
  (synthetic ids, no persistence) and disappear when the snapshot recovers.
- Health-derived actions are typically `copy_command` (e.g. `iris auth gmail
  login`) — display-only, so no `invoke` lifecycle is needed for them.
- The Health **screen** stays as-is (the diagnostic view); the Action Center is the
  unified "things you should act on" inbox.

This bridges the persisted (Tasks) and projection (Health) models cleanly: each
keeps its native lifecycle, and the union happens only where it's rendered.

### 2. The finance pipeline emits pending-action tasks (the first provider)

`src/iris_harness/finance/pending_actions.py` translates blocked statements
into tasks. It runs **at the end of `finance_ingest_tick`** (so it reflects the
just-completed ingest/extract pass) and is also callable standalone. For each
statement it inspects status and emits a dedup-keyed task:

| Condition | source_kind | dedup_key | action |
|---|---|---|---|
| `password_status=FAILED` | `finance-statements` | `stmt:{id}:password` | `copy_command`, `safe=False`, `command="iris finance secret set-password {institution_id}"` |
| `extraction_status=FAILED` (not password) | `finance-statements` | `stmt:{id}:extract` | `re_extract`, `safe=True`, `target_id={id}` |
| `institution_id is None` / `document_type=OTHER` (unresolved) | `finance-statements` | `stmt:{id}:unknown` | `register`, `safe=True`, `target_id={id}` |

Dedup is first-write-wins on `(dedup_key, status != 'dropped')` — the existing
unique index (`src/iris_harness/tasks/store.py`) — so re-running the tick never
duplicates an action.

### 3. Auto-reconciliation: actions clear when the underlying state resolves

The risk of a persisted store is staleness; we close it deterministically. The
same pass that emits actions also **resolves** them: for every open
`finance-statements` pending-action task, if the underlying statement no longer
meets its raise-condition (password now set and extracted, institution now
resolved), the task is marked `done`. This makes the tick **idempotent and
self-healing** — the task list is reconciled to the store on every run, the same
way the net-worth and health surfaces recompute from source of truth. We do *not*
rely on the user to close these manually (though they may `dismiss` → `dropped`).

### 4. API: read tasks + invoke safe actions

New endpoints in `services/iris_api/main.py` (read endpoints open; mutations
behind the existing write-gate middleware):

- `GET /actions` → persisted action-tasks (`TaskStore.list(has_action=True)`, mapped)
  unioned with Health-synthesized items (§2b), newest first.
- `GET /tasks` → all tasks (so the per-agent console and a future todo view can
  reuse it); query params for `status`, `source_kind`.
- `POST /actions/{id}/invoke` → execute a `safe=True` action (re-extract /
  register / review). 403 when writes are disabled; `copy_command` actions reject
  invocation (display-only). Returns the updated task.

### 4b. Non-web surfaces — the Action Center is not web-only

The Web UI is a thin renderer, never a dependency: a harness capability must be
reachable without the browser. The Action Center composition therefore lives in the
harness (`src/iris_harness/action_center.py`: `collect_pending_actions` unions persisted
action-tasks with read-time Health items; `render_pending_actions` for text), and is
consumed by **three** surfaces over the same code: the API (`GET /actions`), a chat
ReAct tool (`pending_actions`, alongside `system_health`), and the CLI
(`iris actions`, composing locally — no running server needed). React only renders.

### 5. WebUI: Action Center (Phase 1)

A new `/actions` screen + a header badge (count of open actions), both thin
renderers over `GET /actions` — no business logic in React, matching the Health
screen pattern. Each row shows the title, the offending item, age, and either a
**[Copy command]** affordance (display-only actions) or a **safe-action button**
(re-extract / register / review) shown only when `writes_enabled`. Resolving the
underlying issue makes the row disappear on the next poll (reconciliation).

### 6. Per-agent console (Phase 2) and settings + UI remediation (Phase 3)

Deferred, but the shape is fixed so Phase 1 doesn't paint us in:

- **Phase 2** adds `GET /agents` (enumerate `AgentExecutor.registered_agents()`
  with metadata) and per-agent dashboard routes. Each agent's dashboard embeds
  *its* pending actions (filter by `source_kind`), recent runs, and accounts.
  The finance dashboard is the reference: accounts, last ingest run, statement
  inventory (reusing the issue-0015 `build_statements_digest`), pending actions.
- **Phase 3** adds per-agent settings pages and, if desired later, a
  localhost-only gated secret-entry flow (explicitly out of scope now per the
  "secrets stay CLI" decision).

## Alternatives considered

- **Health-style projection (recompute, never persisted).** Lowest code, no
  stale state, auto-clears — but no dismiss/snooze, no history, and advisory
  items (new doc type) recompute forever until acted on. Rejected in favour of
  Tasks for the unified inbox + history; we recover the "no stale state" benefit
  via §3 reconciliation.
- **A bespoke `pending_actions` table.** Duplicates the lifecycle/dedup the task
  store already provides; rejected.
- **Mixing actions into the user's todo list undifferentiated.** Rejected — the
  `action`-presence discriminator (§1) keeps the two cleanly separable in one
  store.

## Security & privacy

- Secrets never cross the web UI: `set-password` is a `copy_command`
  (`safe=False`), display-only, run by the user in their terminal.
- Safe actions execute only behind `IRIS_WEBUI_ALLOW_WRITES`, through the
  existing write-gate middleware, and are audited like other mutations.
- Pending-action titles/descriptions reference institutions and statement ids,
  never statement contents or account numbers beyond the existing last-four.

## Testing

- `tests/unit/test_finance/test_pending_actions.py`: each condition → the right
  dedup-keyed task + action; idempotent re-run (no duplicates); reconciliation
  resolves a task when the statement is fixed.
- `tests/unit/test_tasks/`: `TaskAction` validation; `action`-presence filter.
- API tests: `GET /actions` shape; `POST /actions/{id}/invoke` gated by writes;
  `copy_command` rejects invoke.

## Rollout

- Phase 1 producer is gated by the existing `IRIS_FINANCE_AUTO_INGEST` switch
  (the tick that drives it) — no new always-on behaviour. The Action Center is
  read-only unless `IRIS_WEBUI_ALLOW_WRITES=1`.
- Slices: (1) `TaskAction` model + store filters; (2) finance producer +
  reconciliation in the tick; (3) generic provider seam (Protocol + `reconcile`),
  finance refactored to a provider; (4) Health adapter + `GET /actions` (union) +
  `GET /tasks` + `POST /actions/{id}/invoke`; (5) webui Action Center + badge.
  The per-agent console + settings (§6) are separate follow-ups.
