# ADR-0013: Event bus promotion — implementation shape

Status: Accepted (2026-05-25)

**Status pass (2026-10-18):** the decision holds; the code now lives in `foundation/eventbus/`. exact-match topics, no wildcard, no audit handler, singleton kept; the coding-side helpers are in the private `iris_code` tree

Context: Personal-assistant upgrade Phase 0 track 4. Companion: canonical doc §3.4 (parent design); ADR-Q9.

## Context

The canonical doc §3.4 named promotion of the existing
``src/iris_harness/coding/event_bus.py`` (a coding-agent–internal event bus, 365
LOC) to a runtime-wide package ``src/iris_harness/eventbus/`` as a Phase 0 track.
The doc specified **what** to do (move generic infrastructure out, keep
coding-specific topics with their producer) but left the **shape**
decisions to the implementation phase.

Phase 0 track 4 executed the promotion (commit ``f23dcb0``). Six
implementation decisions resolved during that work aren't in the parent
design and would be invisible to future readers without this record —
same gap that ADRs 0010, 0011, and 0012 closed for their respective
tracks.

## Decision

Six implementation decisions:

1. **Coding-specific convenience methods removed from ``EventBus``;
   replaced with module-level helper functions in
   ``iris_harness.coding.events``.** The old class had four methods
   (``tool_call_context``, ``stage_context``, ``persona_context``,
   ``pipeline_context``) that each instantiated coding-specific
   context-manager classes with coding-specific payloads. The generic
   bus must not know about specific topics or payloads, so those
   methods became standalone helpers that take ``bus`` as the first
   argument: ``tool_call_context(bus, tool_name, args, ...)`` instead
   of ``bus.tool_call_context(tool_name, args, ...)``. Eight call
   sites updated.

2. **Old ``event_bus.py`` is deleted (no compatibility shim).** A
   ``from iris_harness.coding.event_bus import *`` shim would have eased
   migration, but: (a) all 5 caller files were known and updated in
   the same commit, (b) the shim adds an indirection layer that
   discourages converging on the new import paths, (c) pre-commit +
   tests catch any missed import. Clean delete.

3. **``src/iris_harness/eventbus/topics.py`` ships empty (docstring only).**
   The file is a deliberate scaffolding placeholder: Phase 1+
   producers (email-triage, finance-statements, calendar-sync,
   reminders, tasks) will populate it with topic name constants and
   frozen-dataclass payloads as those subsystems land. Pre-populating
   with speculative constants before any consumer exists would be
   guessing.

4. **No wildcard ``"*"`` subscription support added in this track.**
   The canonical doc §3.4 mentions a wildcard-subscriber pattern for
   the audit handler. Adding it requires changing the handler
   signature (wildcard subscribers need to know which event fired) —
   a meaningful API extension. Deferred until a real cross-cutting
   subscriber (e.g., audit log) needs it. The dispatch loop in
   ``EventBus.emit``/``emit_sync`` does exact-match only today.

5. **No ``audit_handler.py`` added in this track.** The canonical doc
   §3.4 lists it as "optional: subscribes to ``*`` and appends to
   ``data/audit.db``." Same reasoning as (4): defer until there's a
   real consumer. Adding it now would be speculation.

6. **Module-level singleton (``get_default_bus`` /
   ``reset_default_bus``) is preserved as-is; not converted to DI.**
   The canonical doc §3.4 says "Singleton lifecycle.
   IrisRuntime.startup() instantiates the bus. Skills/routines/heartbeats
   receive the bus via dependency injection, NOT via a global." The
   coding agent's existing code is module-singleton-based. Converting
   to DI would touch every coding-agent call site simultaneously —
   well beyond the promotion scope. The singleton stays for the
   coding-agent paths; Phase 1 (when skills start needing the bus
   injected into their contexts) is where the DI conversion belongs.
   Flagged as future ADR.

## Alternatives considered

1. **Keep convenience methods on ``EventBus``.** Smaller move; less
   call-site churn. Rejected: generic bus would carry coding-specific
   knowledge forever; Phase 1+ producers would be tempted to add their
   own ``bus.email_classified()`` etc. methods; god-class incrementally
   accreted.

2. **Subclass: ``CodingEventBus(EventBus)`` adds the four methods.**
   No call-site changes for coding-agent code. Rejected:
   ``get_default_bus()`` returns plain ``EventBus`` (the singleton
   pattern), so coding paths using it would lose the methods unless
   we add a ``get_coding_bus()`` (more API). Adds a class that exists
   only to host four methods.

3. **Compatibility shim at old path.** Re-export from
   ``src/iris_harness/coding/event_bus.py`` for one phase. Rejected: clutters
   the import graph; discourages converging on the new paths;
   indirection makes editor "go to definition" worse.

4. **Pre-populate ``topics.py`` with canonical-doc §3.4 topics from
   day one.** Adds ``email.new_arrived``, ``email.classified``,
   ``finance.statement_ingested``, ``task.due_soon``, etc. as
   constants before any producer exists. Rejected: speculative;
   producers should land with their topic constants in the same
   commit. Empty-stub file is honest about MVP scope.

5. **Convert singleton → DI in the same commit.** Right architectural
   end state. Rejected for this track: touches every coding-agent
   call site that uses ``get_default_bus()``, which is well beyond
   the move's scope. Belongs in Phase 1 alongside the bus-injection
   into skill contexts.

6. **Add wildcard support in this commit.** Cleaner future state.
   Rejected: requires an API change (handler signature for wildcard
   subscribers); no current consumer needs it; defer to the
   first real cross-cutting subscriber.

## Consequences

**Positive:**
- Generic ``EventBus`` is now genuinely topic-agnostic; Phase 1+
  producers can publish their topics + helpers in their own packages
  without inflating the generic class.
- 4 directory-level boundaries make the runtime layering visible:
  ``iris_harness.eventbus.bus`` (generic), ``iris_harness.eventbus.topics`` (runtime
  payloads), ``iris_harness.coding.events`` (coding-pipeline payloads).
- 8 call-site updates were mechanical and bounded; pre-commit + tests
  caught the one ruff/black interaction (``# noqa: S603`` had to
  move down a line after black split a multi-line ``subprocess.run``
  call).
- 524 tests across coding + identity + knowledge + skills pass
  unchanged; behavior preserved.

**Negative:**
- Singleton-vs-DI question is unresolved. The canonical doc names DI
  as the right end state; this ADR explicitly defers it. Future
  Phase 1 work will pick this up alongside skill-context injection,
  and a future ADR will record that decision.
- ``topics.py`` is an empty file that exists only to document the
  intent. Looks weird until Phase 1 producers populate it.
- Eight call-site changes from method-style to function-style
  (``bus.X(...)`` → ``X(bus, ...)``) are mildly less ergonomic for
  callers, even though architecturally correct.

**Reversibility:** moderate. Re-introducing the convenience methods
on ``EventBus`` is easy (re-add four methods that delegate to the
helpers). Re-introducing the old file path requires moving code back
and updating callers. The singleton-vs-DI deferral is a future
choice, not a lock-in.

## Related ADRs

- ADR-Q9 / canonical doc §3.4 — parent design naming this track.
- ADR-0010 (entity extractor implementation shape), ADR-0011
  (Keychain credentials shape), ADR-0012 (manifest schema extension
  shape) — sibling implementation-shape ADRs from Phase 0.
- A future ADR will record the singleton → DI conversion when
  Phase 1 skill-context injection lands.
- A future ADR may extend ``EventBus`` with wildcard ``"*"``
  subscription support when the first cross-cutting subscriber
  (likely an audit handler) lands.
