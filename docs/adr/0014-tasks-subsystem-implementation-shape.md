# ADR-0014: Tasks subsystem — implementation shape

Status: Accepted (2026-05-25)

**Status pass (2026-10-18):** the decision holds; the code now lives in `evarness/services/tasks/`. all ten choices hold; TaskStatus has since gained `expired`

Context: Personal-assistant upgrade Phase 0 track 5. Companion: ADR-0005 (parent vocabulary + soft-migration design); ADR-0013 (event-bus convention).

## Context

ADR-0005 locked the vocabulary (Goal / Task / Followup-as-Task-variant /
Reminder-as-delivery / Bill), the three grouping mechanisms
(``dedup_key`` + ``parent_task_id`` + wikilink overlap), and the soft-
migration stance (don't touch ``src/iris_harness/reminders.py``; new module
under ``src/iris_harness/tasks/``). The canonical doc §3.4 sketched the
schema and the API surface in broad strokes.

Phase 0 track 5 implemented the subsystem across two commits:
``37974ca`` (data layer — models, store, dedup_keys, 44 tests) and
``37f0415`` (event payloads + store rewiring, 10 more tests).

This ADR records the implementation decisions resolved during that
work, in the same pattern as ADR-0010 (entity extractor), ADR-0011
(credentials), ADR-0012 (manifest schema), and ADR-0013 (event bus
promotion).

## Decision

Ten implementation decisions:

1. **String-based ``Literal`` aliases at module level**
   (``TaskStatus``, ``GoalStatus``, ``SourceKind``, ``WaitForKind``)
   rather than ``StrEnum`` classes. Reason: Pydantic ``Literal``
   support is mature and emits clean ValidationErrors; ``StrEnum``
   adds an import + class-noun ceremony for no behavioral gain at
   this size.

2. **``status='dropped'`` does NOT set ``completed_at``.** Drops are
   terminal but a distinct concept from completion — a dropped task
   never finished, it was abandoned. Using ``completed_at`` for both
   would conflate the two and pollute "what did I get done?"
   queries. Enforced by ``Task._validate_completion_consistency``
   model_validator.

3. **``UNIQUE INDEX`` on ``tasks(dedup_key)`` is conditional:
   ``WHERE dedup_key IS NOT NULL AND status != 'dropped'``.** Drops
   re-enable upsert with the same key (legitimate re-creation of a
   work item the user previously abandoned). NULL dedup_keys don't
   collide (tasks without a dedup contract are allowed to duplicate
   freely). Both behaviors are intentional and tested.

4. **Upsert: first-write wins; no ``source_evidence`` accumulation
   in MVP.** Collision returns the existing Task unchanged.
   Cross-producer evidence (e.g., ``source_evidence: [email/abc,
   statement/xyz]``) is left for Phase 1+ when real producers
   compete and we have data on what evidence is actually useful.
   Single ``source_kind``/``source_id`` columns suffice today.

5. **JSON-shaped columns for ``wait_for`` and ``related_wikilinks``;
   raw SQLite TEXT.** Both are structured-but-variable fields where
   adding columns would either limit the shape (one ``wait_for_kind``
   column + many type-specific FKs) or normalize prematurely (a
   ``related_wikilinks`` join table for what is almost always 0–3
   slugs). The store handles ``json.dumps``/``json.loads`` at the
   row boundary; callers see ``WaitFor`` Pydantic models and
   ``tuple[str, ...]`` slugs.

6. **``bus`` is an optional ``TaskStore`` constructor argument; the
   store works silently when ``None``.** Tests, scripts, and one-off
   maintenance code shouldn't have to construct a bus to use the
   store. When wired, every mutation emits a typed event payload
   from ``iris_harness.tasks.events``.

7. **``goal.updated`` and ``goal.achieved`` are mutually exclusive
   on a single transition.** When a goal moves to ``status='achieved'``,
   the store emits ``goal.achieved`` ONLY (with the terminal
   ``GoalAchievedPayload``); ``goal.updated`` does NOT fire on the
   same call. Rationale: subscribers to ``goal.achieved`` shouldn't
   have to filter out generic-update events that happen to coincide
   with achievement. The terminal event carries everything an
   achievement subscriber needs.

8. **``task.completed`` and ``task.dropped`` fire IN ADDITION to
   ``task.updated``** (the opposite of #7). Reason: ``complete()``
   and ``drop()`` call ``update()`` internally to write the status
   change, and ``update()`` is what emits ``task.updated``. We could
   suppress the inner emission but the symmetry isn't worth the
   complexity: most subscribers will listen to either the general
   ``task.updated`` stream OR the specific terminal events, not
   both. The duplicate emission is documented and tested.

9. **``update()`` and ``update_goal()`` reject mutation of identity
   fields** (``id``, ``created_at``, plus ``dedup_key`` for tasks).
   Identity changes via re-create + drop, not via update. Enforces
   the invariant that a ``task.created`` event ID + ``created_at``
   is stable forever and dedup keys can't be silently re-assigned.

10. **``resolve_wait()`` sets ``wait_for_resolved_at`` but does NOT
    auto-complete the task.** When the awaited event arrives, the
    followup is *unblocked* but not yet *done* — the user (or a
    routine) decides whether to mark it complete, change its title,
    add a follow-up, etc. Auto-completing on wait-resolve would
    silently close work the user may still need to do.

## Alternatives considered

1. **``StrEnum`` for status/source-kind types.** More OOP-shaped,
   slightly nicer in IDEs. Rejected: ``Literal`` covers the same
   ground at zero ceremony cost.

2. **``status='dropped'`` sets ``completed_at`` to a "dropped_at"
   timestamp.** Simpler schema. Rejected: pollutes "completed work"
   queries; the schema-level distinction is cheap.

3. **Unconditional ``UNIQUE INDEX`` on ``dedup_key``.** Means
   dropped tasks block re-upsert. Rejected: legitimate use cases
   (user drops a bill task, then a new bill with the same key
   arrives) shouldn't fail.

4. **``source_evidence`` JSON array from day one.** Cleaner long-
   term API. Rejected for MVP: speculative; design happens better
   with real producer data.

5. **Normalize ``related_wikilinks`` into a join table.** Cleaner
   relational shape. Rejected: typical task has 0-3 slugs; the JSON
   column avoids a join on the hot read path.

6. **Required-bus ``TaskStore`` constructor.** Cleaner contract
   (bus is the integration point). Rejected: makes test fixtures
   verbose and forces scripts to construct a real bus they don't
   use.

7. **Symmetric: ``goal.updated`` AND ``goal.achieved`` always both
   fire.** Maximally informative. Rejected: forces every achieved
   subscriber to filter — clearer to make ``goal.achieved`` the
   sole-and-terminal signal.

8. **Suppress ``task.updated`` inside ``complete()``/``drop()``.**
   Cleaner symmetry with #7. Rejected: requires an internal flag to
   distinguish "called from complete()" vs "called directly", adding
   complexity. The duplicate emission is harmless and documented.

9. **Allow ``dedup_key`` mutation via ``update()``.** More flexible
   in edge cases. Rejected: silently re-assigning dedup keys
   undermines the producer-cooperation contract.

10. **Auto-complete on ``resolve_wait()``.** Convenient default.
    Rejected: silently closes work that the user may want to
    explicitly take action on after the wait is resolved (e.g., the
    reply they were waiting for may itself need a response).

## Consequences

**Positive:**
- Phase 1+ producers (email-followup, finance-bills, calendar-prep)
  get a simple ``TaskStore.upsert(dedup_key=...)`` to cooperate via
  identity without coordinating with each other.
- Typed event payloads from ``iris_harness.tasks.events`` give subscribers
  static-checkable interfaces; runtime topic strings are documented
  and importable.
- The 54-test suite covers schema idempotency, identity guards,
  upsert collisions across status transitions, JSON column
  roundtrips, and every event topic's payload shape.
- Soft migration intact: legacy ``src/iris_harness/reminders.py`` (974 LOC)
  unchanged; new code path established without breaking existing
  callers.

**Negative:**
- ``task.updated`` fires both as a standalone event AND inside
  ``complete()``/``drop()`` — subscribers must understand this.
  Documented; tested.
- The JSON-column approach for ``wait_for``/``related_wikilinks``
  means queries can't filter inside those fields without
  ``json_extract()``. For Phase 0 we never need to; if a future
  query needs "find all tasks waiting on email replies from X," we
  add a SQLite generated column or restructure.
- ``source_evidence`` deferral means a real cross-producer collision
  in Phase 1 will need a small schema migration. Reversibility is
  high (JSON column added; existing rows default to empty list).

**Reversibility:** high. Each decision is isolated to its method or
its schema column. Flipping any of them (e.g., adding
``source_evidence``, allowing ``task.updated`` suppression) is a
small focused edit.

## Related ADRs

- ADR-0005 (parent) — Task vocabulary + soft migration.
- ADR-0013 (event bus promotion) — convention that subsystem topics
  live with their producer; this module follows that.
- ADR-0010, ADR-0011, ADR-0012 — sibling implementation-shape ADRs
  from Phase 0.
- A future ADR may introduce ``source_evidence`` accumulation when
  Phase 1+ producers actually compete.
- A future ADR may add a wildcard event subscription pattern when
  cross-cutting subscribers (audit log, observability) land.
