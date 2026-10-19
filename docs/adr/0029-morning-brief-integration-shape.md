# ADR-0029: Morning brief Phase 2 integration — implementation shape

Status: Accepted (2026-06-10)
Context: Personal-assistant upgrade Phase 2 Track 2C. Parent: ADR-0021
(skill-routing + brief auto-wrap convention in the project-iris-prd).
Companions: ADR-0027 (Tasks/Goals CLI surfacing the same data),
ADR-0028 (email-followup producing the followup tasks).

## Context

The canonical doc §3.4 marks "real brief with open tasks,
auto-resolving followups" as the Phase 2 user-visible deliverable.
The existing `config/skills/builtin/morning-briefing/` skill was
already YAML-driven (`kind: brief`, `brief.slots`, tool-driven
sections) but only consumed legacy slot tools (calendar-reminders,
iris-core for active items + routines, web-fetch for repos/news).

Track 2C plugs the Phase 0+1+2 substrate into the existing slot
mechanism. The choice is whether to extend the manifest in place,
introduce a sibling brief, or fork. This ADR records the call and
the supporting shape decisions (new builtin skill, where the
inbox-summary tool lives, manifest version bump).

## Decision

Seven implementation decisions:

1. **Extend `config/skills/builtin/morning-briefing/manifest.yaml`
   in place, version bump 0.1.0 → 0.2.0.** The user's morning
   brief is one document; a sibling skill (`personal-brief`,
   `phase-2-brief`) would force the operator to choose between
   them. Five new slots — `due_today`, `open_tasks`,
   `open_followups`, `resolved_followups`, `email_summary` —
   inserted at the top of the layout so the user's own work shows
   above generic news/repos. Legacy slots unchanged.

2. **New builtin skill `config/skills/builtin/iris-tasks/`** owns
   the three task-domain brief tools: `list_open_tasks`,
   `list_due_today`, `list_resolved_followups`. Rejected:
   bundling these into `iris-core` (which currently owns
   `list_active_items` and `list_approved_routines`) — would
   conflate the legacy active.md / approved-routines surface
   with the new TaskStore surface, making "what reads what" less
   obvious; the legacy surface stays minimal and the new domain
   gets its own skill.

3. **`list_open_followups` lives in the `email-followup` skill,
   not `iris-tasks`.** Followups are TaskStore rows, but their
   producer is email-followup; the skill that creates them owns
   the brief surface for them. Symmetric: `list_resolved_followups`
   lives in `iris-tasks` because the *brief-time* question
   ("what replies arrived that I haven't acknowledged?") is
   independent of which producer set the wait. Boundary chosen
   to match the user's mental model, not strict producer-owns-tool.

4. **`email_inbox_summary` added to the existing `email-triage`
   skill, not a new `email-brief` skill.** The summary aggregates
   classified / pending_review / unclassified counts that are
   the very state email-triage manages. Bundling the read-only
   summary alongside the read-write classify tools keeps related
   functions co-located. Rejected: a sibling
   `email-brief-summary` skill — splits the email surface for no
   clear benefit.

5. **Slot tools return `list[dict[str, str]]`** matching the
   `BriefToolSlot` `item_template` substitution contract.
   `email_inbox_summary` always returns a one-element list (or
   empty); the brief's `format: bullets` renders the single row
   as one bullet. Rejected: scalar return for the inbox summary
   — would force a new format kind in the brief resolver.

6. **`iris-tasks` manifest declares empty `config_files`.** The
   tools call `TaskStore.ensure_schema()` on first invocation,
   auto-creating `data/tasks.db`. Listing the DB as a prereq
   would gate tool loading behind file existence and break the
   brief render on fresh installs — the same failure mode that
   broke the email skills via the loader bug (ADR omitted; fix
   in commit `198b964`). Companion fix to
   `iris_harness.skills.loader._resolve_config_path` lets future
   manifests safely declare `data/...` prereqs without the
   loader silently misresolving them.

7. **Slot test convention: load `tools.py` via `importlib`
   with `sys.modules` registration before `exec_module`.**
   Pydantic BaseTool subclasses require their referenced
   `BaseModel` to be importable from the module's namespace at
   class-construction time; without `sys.modules` registration
   the loader raises `PydanticUserError: not fully defined`.
   Matches the pattern already used by
   `test_iris_core_tools.py`. Rejected: instantiating BaseTool
   subclasses via SkillRegistry in tests (would couple test
   isolation to registry behavior and slow the suite).

## Alternatives considered

1. **Sibling `personal-brief` skill.** Cleanest namespace
   separation. Rejected: forces the operator to disable one of
   the two briefs to avoid duplicate delivery; the existing
   morning-briefing is already the "your day" brief.

2. **Bundle all Phase 2 brief tools into one `personal-brief-data`
   builtin.** Less skill-package proliferation. Rejected:
   couples task domain to email domain at the manifest level;
   per-skill prereqs and per-skill version bumps become messy.

3. **Compute brief content in the brief skill's own
   tools.py** (i.e. `morning-briefing` reads tasks.db and
   email.db directly). Rejected: violates the skill-tool
   separation that ADR-0021's skill-routing pattern depends on;
   the brief is a YAML composer, not a domain reader.

4. **Generate slot tools' SQL with `json_extract` to filter
   `wait_for` server-side** (e.g. "tasks WHERE wait_for IS NOT
   NULL"). Cleaner queries but `TaskStore.list` has no
   wait_for filter — adding one means an API change.
   Rejected for MVP: a Python-side filter over `limit*3` rows
   is fine at Phase 2 volumes; ADR-0014 §"Negative" already
   flagged the JSON-column query limitation as a known
   trade-off. Revisit if a future producer creates so many
   tasks that the over-fetch matters.

5. **Skip `list_resolved_followups` and surface replies via a
   one-shot "what's new" event log.** Lighter brief footprint.
   Rejected: the resolved-but-not-completed state is exactly
   the "should I close this followup or re-engage?" moment the
   user needs the brief to flag.

## Consequences

**Positive:**

- The brief becomes the operator's daily attention surface for
  everything the Phase 0+1+2 producers wrote — tasks, goals,
  followups, inbox state — without bypassing the YAML-first
  skill-routing convention.
- The five new slots compose with the existing slot mechanism
  identically to the legacy ones; the brief resolver got zero
  changes.
- 21 tests across `test_iris_tasks_tools.py` (7),
  `test_email_triage_brief_tool.py` (4), and
  `test_morning_briefing_integration.py` (10) cover slot output
  shapes, manifest-uses consistency, and that every tool slot
  references a declared tool.

**Negative:**

- One brief manifest is now load-bearing across three skill
  domains (calendar-reminders, iris-core, iris-tasks,
  email-followup, email-triage, web-fetch). A change in any of
  the consumed skills' tool surfaces silently regresses the
  brief — the integration test
  (`test_every_tool_slot_references_a_declared_tool`) catches
  this in CI.
- Operators who genuinely want a different daily brief shape
  must fork the manifest. Acceptable: the manifest is YAML,
  forking is one file copy.
- The brief render aborts if any slot tool raises (no
  per-slot soft-fail in the resolver). Phase 2 saw this trip
  twice — once on the loader bug, once on a stooq.com 404 in
  the legacy stocks slot. Surfacing per-slot errors as
  empty content is a future brief-resolver improvement.

**Reversibility:** high for the slot additions (remove from
the manifest); medium for the iris-tasks skill (deleting it
is one-line in the manifest's `uses` list + a directory
delete). Reverting the legacy email-triage tools.py to remove
`email_inbox_summary` is a small edit.

## Related ADRs

- ADR-0014 — TaskStore that the brief slot tools read.
- ADR-0021 — skill-routing + brief auto-wrap convention from
  the project-iris-prd that this brief extension uses.
- ADR-0027 (sibling) — `iris task` CLI surfacing the same
  TaskStore data.
- ADR-0028 (sibling) — `email-followup` skill producing the
  followup tasks this brief surfaces in two of its slots.
- ADR-0030 (sibling) — delivery-only Reminder primitive whose
  fired rows could be surfaced by a future brief slot once
  channel routing is observed in production.
