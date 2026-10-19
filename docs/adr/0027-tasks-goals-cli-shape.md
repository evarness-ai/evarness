# ADR-0027: `iris task` + `iris goal` CLI — implementation shape

Status: Accepted (2026-06-10)
Context: Personal-assistant upgrade Phase 2 Track 2A. Parent ADRs:
ADR-0005 (Task vocabulary + soft migration), ADR-0014 (Tasks subsystem
implementation shape).

## Context

Phase 0 Track 5 (ADR-0014) shipped `src/iris_harness/tasks/` with `Task`,
`Goal`, `TaskStore`, `dedup_keys`, and typed event payloads. The
store had no user-facing surface — there was no way for a human to
inspect what producer skills (email-followup in Track 2B,
finance-bills in Phase 4, calendar-prep in Phase 6) were writing.

Track 2A adds that surface: two Typer subgroups (`iris task` and
`iris goal`) covering CRUD, the manual-grouping mechanism from
ADR-0005, and goal-task linkage rendered for `show`. This ADR
records the non-obvious calls made while implementing those CLIs.

## Decision

Ten implementation decisions:

1. **Inline in `src/iris_harness/main.py`** under two new `typer.Typer`
   subapps (`task_app`, `goal_app`). Matches the existing pattern
   for `email_app`, `vault_app`, `auth_app`. Rejected: a new
   `src/iris_harness/cli/task_cmds.py` module — would be the first CLI
   subgroup not living inline; consistency wins until main.py
   itself needs to be split.

2. **UUID prefix resolution at the CLI boundary** — accept any
   prefix ≥ 4 chars, resolve to a unique row via SQL
   `LIKE 'prefix%' LIMIT 5`. Exit 2 on `<4` chars, exit 1 on
   no match or ambiguity. Rejected: requiring full 36-char UUIDs
   (operationally hostile after copy-paste from `task list`); short
   sequential IDs (would force a separate human-id column on the
   store and break the existing event payload contract).

3. **Default `--status` filter on `iris task list` is `open`**.
   The brief's "Open tasks" slot pulls the same default. Users
   pass `--status all` (or a specific status) to widen.
   Rejected: defaulting to `all` — would render terminal noise on
   every casual list invocation.

4. **`iris task update` rejects terminal status transitions
   (`done`, `dropped`)** with exit 2 and a message routing the
   user to `complete` / `drop`. Reason: terminal transitions on
   TaskStore have side effects (`completed_at` set + dedicated
   typed events fired) and the CLI must not duplicate that logic.
   Forcing `complete`/`drop` keeps the side-effect path canonical.

5. **`iris task update --clear-due` flag instead of empty
   `--due ""`**. Typer treats an empty option as "not provided",
   so there's no string-sentinel way to express "clear the
   existing value". An explicit boolean flag is unambiguous and
   self-documenting.

6. **`iris task group <a> <b> ... --under "title"` creates a new
   parent task** then re-parents every listed child via
   `TaskStore.update(child, parent_task_id=parent.id)`. Tree-only
   per ADR-0005 — children that already had a parent are
   re-parented to the new one (no DAG). Rejected: rejecting
   already-parented children (would force the user to ungroup
   first); making `--under` accept an existing task id (saves a
   call but conflates two distinct operations — `move-into`
   versus `create-and-collect`).

7. **`iris goal achieve` only — no separate `iris goal pause`
   or `iris goal drop`**. Goals already support `paused`,
   `dropped`, and `achieved` via `TaskStore.update_goal`, but the
   only terminal one we surface from the CLI is `achieved`. Pause
   and drop are reachable from the store API (and from a future
   harness tool); the CLI surface stays small until evidence
   shows the other two are user-asked.

8. **List markers in `iris goal show` use `(x)` / `( )`, not
   `[x]` / `[ ]`**. Rich treats `[x]` as a markup tag and silently
   strips it. Parentheses render identically across console widths
   and don't collide with Rich's tag parser.

9. **Rich tables for all multi-row output**; single-row results
   render as plain `console.print` lines. Matches the email-CLI
   convention and keeps `--print --json` portable (Rich tables
   strip cleanly when piped).

10. **Added `TaskStore.get_by_dedup_key`** as a public wrapper
    over the existing private `_get_by_dedup_key`. The Track 2B
    auto-resolver needs to look up the followup task by
    `followup_key(provider, thread_id)` without depending on
    raw SQL or private method access. Same row-visibility rule
    (dropped tasks don't surface) as the private helper — ADR-0014
    decision #3 remains intact.

## Alternatives considered

1. **CLI under `src/iris_harness/cli/task_cmds.py`.** Cleaner separation
   from `main.py`. Rejected: every other CLI subgroup is inline;
   doing this one differently fragments the surface.

2. **`iris tasks` (plural) command name.** Rejected: Typer
   subgroups conventionally use the singular noun (`vault`,
   `auth`, `email`). The plural would be cosmetic divergence.

3. **`iris t` short alias.** Rejected: `t` is too easily confused
   with `tab`, terminal aliases, etc. Users can alias locally.

4. **Live `iris task watch` mode** that streams `task.*` events.
   Rejected for MVP: useful, but the brief covers the
   common "what's the state right now?" question and a watch mode
   needs a stable event-replay story that Phase 2 doesn't have.

5. **JSON output via `--json`.** Rejected for MVP: the harness
   reads via the SkillRegistry tool surface (Track 2C), not the
   CLI. Operators piping `iris task list` through `jq` is a real
   future case; revisit when somebody asks.

## Consequences

**Positive:**

- The Phase 0 substrate now has a usable surface — every task /
  goal mutation a producer skill performs is inspectable, and a
  human can `add` / `complete` / `drop` / `group` to manage their
  own work alongside producer output.
- Track 2B (email-followup) and Track 2C (brief integration)
  both inherit the prefix-resolution convention via the new
  `_short_id` rendering, keeping ID display consistent across
  the CLI and the brief slots.
- 15 CLI tests in `tests/unit/test_cli/test_main_task.py` cover
  CRUD, prefix resolution edge cases, `--clear-due`, group
  reparenting, and goal-task linkage rendered by `goal show`.

**Negative:**

- Inline CLI commands push `src/iris_harness/main.py` past 3000 lines.
  Continues the trend (the file is now the load-bearing CLI
  registry); a future split is a larger refactor than was
  warranted for this track.
- Prefix resolution opens a small window for "ambiguous prefix"
  errors as the task list grows. The 4-char minimum keeps the
  collision probability low (~1 in 65k per task) but operators
  with thousands of tasks may need to type longer prefixes.

**Reversibility:** high. Each command is its own function; the
shape of any one can change without touching the others. Adding
`--json` or `iris t` aliases is a one-commit addition.

## Related ADRs

- ADR-0005 (parent) — Task vocabulary + soft migration.
- ADR-0014 (sibling) — TaskStore + dedup_keys + event payloads
  this CLI sits on.
- ADR-0028 — email-followup skill that produces followup tasks
  this CLI now exposes.
- ADR-0029 — brief integration that consumes the same TaskStore
  via the `iris-tasks` builtin skill.
