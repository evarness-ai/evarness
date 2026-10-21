"""The heartbeat tick handlers the runtime registers by default.

Gate-1 extraction (OSS plan M5.7), third slice. Three ticks — the
delivery-only reminder store, host pressure, and routines — plus the routine execution
they share. Each is a closure over the runtime, which is why they were written beside it
and why they are the natural next block out: none of them is composition, they are the
scheduled work the composition root happens to own.

Measured before cutting: every name referenced from outside the block was a module-level
import of bootstrap's, except ``EvarnessRuntime`` itself, which is needed only as an
annotation. That is a TYPE_CHECKING import here — the same shape
``handlers/skill_brief.py`` already uses, and the reason this package exists.

Public here means "something outside calls it": ``execute_routine`` because
``EvarnessRuntime`` calls it directly as well as through ``build_routine_tick_handler``, and
``resolve_brief_skill_id`` / ``routine_render_params`` because
``handlers/skill_brief.py`` resolves the same skill id and params for its preview path.
The legacy ``reminder_tick`` left for the calendar plugin at M5.7 track A slice 4.

Those last two were nearly missed. The measurement that sized this block read
*bootstrap's* AST, so a sibling module importing a bootstrap private was invisible to
it — mypy caught it, not the measurement. A block's consumers are not only its own
file's.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from evarness.runtime.routine_authoring import _BRIEF_TEMPLATE_ALIASES
from evarness.services.heartbeat import HeartbeatDefinition, HeartbeatRun, HeartbeatStatus
from evarness.services.heartbeat.scheduler import HeartbeatHandler
from evarness.services.routines import (
    RoutineExecutionRecord,
    RoutineExecutionStatus,
    RoutineSpec,
    RoutineTickResult,
)
from evarness.services.routines.seeded import (
    MORNING_DIGEST_ROUTINE_ID,
    sync_morning_digest,
)

if TYPE_CHECKING:
    from evarness.runtime.bootstrap import EvarnessRuntime

logger = logging.getLogger(__name__)

# The routines with a real run in progress in this process, by (routine store, id): one
# run of a routine at a time, whether the schedule, Run now or the API started it. The
# scheduler's own guard does not cover this — a brief renders through an ad-hoc
# ``skill_brief`` definition that every brief routine shares.
_routines_running: set[tuple[str, str]] = set()
_routines_running_lock = threading.Lock()


def _routine_key(runtime: EvarnessRuntime, routine: RoutineSpec) -> tuple[str, str]:
    store = runtime.routine_store
    return (str(getattr(store, "db_path", id(store))), routine.id)


def build_notification_reminder_tick_handler(
    runtime: EvarnessRuntime,
) -> HeartbeatHandler:
    """Return the heartbeat handler that delivers due reminders (loop-proof D14).

    Accept-then-fire: each due ``ReminderStore`` row is sent to the reminder channels
    (``config/notifications.yaml`` ``reminder_channels``, default Telegram + web push)
    and is marked ``sent`` only when one of them accepted — then ``reminder.fired`` is
    emitted. A send no channel accepted is retried 5 minutes later, three times, then
    the row is ``failed``. The run reports FAILED whenever a send was not accepted,
    so the heartbeat history never shows a silent miss as a success.
    """
    from evarness.foundation.eventbus import get_default_bus
    from evarness.services.digest.settings import evarness_timezone
    from evarness.services.notifications.channels import (
        deliver_due,
        load_reminder_channels,
        resolve_channels,
    )
    from evarness.services.notifications.store import ReminderStore

    def handler(definition: HeartbeatDefinition) -> HeartbeatRun:
        tz = evarness_timezone()
        store = ReminderStore(
            db_path=runtime.data_dir / "tasks.db",
            bus=get_default_bus(),
            tz=tz,
        )
        store.ensure_schema()
        channels = resolve_channels(
            runtime.channels,
            load_reminder_channels(runtime.config_dir),
            default_channel=runtime.default_channel,
        )
        report = deliver_due(store, runtime.channels, channels=channels, tz=tz)
        if report.ok:
            return HeartbeatRun(
                name=definition.name,
                status=HeartbeatStatus.SUCCESS,
                output=report.summary(),
            )
        return HeartbeatRun(
            name=definition.name,
            status=HeartbeatStatus.FAILED,
            output=report.summary(),
            error=report.summary(),
        )

    return handler


def build_pressure_tick_handler(runtime: EvarnessRuntime) -> HeartbeatHandler:
    """Return a heartbeat handler that polls the resource governor."""

    def handler(definition: HeartbeatDefinition) -> HeartbeatRun:
        governor = getattr(runtime.tier_router, "governor", None)
        if governor is None:
            return HeartbeatRun(
                name=definition.name,
                status=HeartbeatStatus.SKIPPED,
                output="governor not configured",
            )
        try:
            snapshot = governor.poll()
        except Exception as exc:  # noqa: BLE001
            return HeartbeatRun(
                name=definition.name,
                status=HeartbeatStatus.FAILED,
                error=str(exc),
            )
        return HeartbeatRun(
            name=definition.name,
            status=HeartbeatStatus.SUCCESS,
            output=(
                f"mode={governor.mode()} "
                f"ram_free={snapshot.ram_free_gb:.1f}GB "
                f"cpu={snapshot.cpu_percent:.0f}% "
                f"speed_limit={snapshot.cpu_speed_limit}"
            ),
        )

    return handler


def resolve_brief_skill_id(runtime: EvarnessRuntime, template: str) -> str | None:
    """Return the brief-skill manifest name a routine template renders, if any.

    Two ways a template resolves to a brief:
      1. A legacy underscore alias (``morning_briefing`` → ``morning-briefing``)
         — registry-independent, preserves the historical behavior.
      2. The canonical manifest name itself (``morning-briefing``), confirmed by
         a loadable brief package in the registry. This is what lets
         canonically-named brief routines — the shape *new* routines persist —
         actually fire (previously they fell through to "handler not registered"
         and failed every tick).

    Returns ``None`` for anything else (including the explicit ``skill_brief``
    template, whose ``skill_id`` comes from metadata) so the caller falls back to
    the generic heartbeat-handler path. Registry access is guarded so callers
    without a ``skill_registry`` (unit fakes) degrade cleanly.
    """
    aliased = _BRIEF_TEMPLATE_ALIASES.get(template)
    if aliased is not None:
        return aliased
    registry = getattr(runtime, "skill_registry", None)
    if registry is None:
        return None
    try:
        for package in registry.list_packages(only_loadable=True):
            if package.manifest.kind == "brief" and package.manifest.name == template:
                return template
    except Exception:  # noqa: BLE001 — never let resolution crash a tick
        return None
    return None


def routine_render_params(routine: RoutineSpec) -> dict[str, Any]:
    """Per-routine customization forwarded into the ``skill_brief`` render path.

    Carries the user's section selection (``source_preferences``) plus optional
    formatting knobs from ``metadata`` (line caps, ordering, header/footer). The
    generic non-brief handler path simply ignores these extra params.
    """
    meta = routine.metadata
    params: dict[str, Any] = {"source_preferences": list(routine.source_preferences)}
    cap = meta.get("content_lines_per_item")
    if cap is not None:
        params["content_lines_per_item"] = cap
    # ``digest``: the seeded morning digest renders grouped (skill_brief.DIGEST_PARAM).
    for key in ("section_order", "section_line_caps", "header", "footer", "digest"):
        value = meta.get(key)
        if value is not None:
            params[key] = value
    return params


def sync_digest_routine(runtime: EvarnessRuntime) -> tuple[bool, RoutineSpec | None]:
    """Re-read Settings → Digest and sync the seeded ``morning-digest`` row from it.

    Called at every routine tick (before the due check) and before a manual run, so a
    changed digest ``time``/``channel``/``sections`` takes effect at the next tick with
    no restart. Returns ``(enabled, row)``: ``enabled`` is the settings switch (a
    disabled digest is not dispatched), ``row`` the synced routine or ``None`` when
    there is none. Never raises — a settings error must not stop the other routines;
    it logs and leaves the row as it was (``enabled`` stays True: a partial digest
    beats a silent morning).
    """
    data_dir = getattr(runtime, "data_dir", None)
    if data_dir is None:  # unit fakes without a data dir have no digest
        return True, None
    try:
        from evarness.services.digest.settings import (
            evarness_timezone,
            load_digest_settings,
        )

        settings = load_digest_settings(data_dir, getattr(runtime, "config_dir", None))
        row = sync_morning_digest(runtime.routine_store, settings, evarness_timezone())
    except Exception:  # never let the digest sync break a tick
        logger.exception("morning-digest: settings sync failed; keeping the stored row")
        return True, None
    return settings.enabled, row


class _RoutineActivity:
    """One ``ActivityStore`` row per routine run (loop-proof D13).

    Opened (``running``, real start time) before the capability runs and closed with
    its outcome after, so the Activity tab shows start, end and status, survives a
    restart, and a run that dies mid-way stays visible as ``running``. The store is
    built WITHOUT a bus on purpose: a bus would fire the activity-completed notifier,
    which posts a second channel message for a run that already delivered its own.
    Best-effort — a ledger failure never fails the run.
    """

    def __init__(self, runtime: EvarnessRuntime, routine: RoutineSpec, *, trigger: str) -> None:
        self._store: Any = None
        self._activity_id: str | None = None
        data_dir = getattr(runtime, "data_dir", None)
        if data_dir is None:  # unit fakes without a data dir keep no ledger
            return
        try:
            from evarness.services.activities import ActivityStore

            store = ActivityStore(db_path=data_dir / "activities.db")
            store.ensure_schema()
            activity = store.create(
                kind=f"routine.{routine.id}",
                title=routine.title,
                origin="routine",
                metadata={
                    "routine_id": routine.id,
                    "template": routine.template,
                    "channel": routine.delivery_channel,
                    "trigger": trigger,
                },
            )
            store.mark_running(activity.id)
        except Exception:  # the activity ledger is best-effort
            logger.exception("routine %s: activity record failed", routine.id)
            return
        self._store = store
        self._activity_id = activity.id

    def finish(self, outcome: RoutineExecutionRecord) -> None:
        if self._store is None or self._activity_id is None:
            return
        try:
            if outcome.status == RoutineExecutionStatus.SUCCESS:
                self._store.mark_completed(
                    self._activity_id,
                    result_summary=outcome.detail,
                    metadata={"heartbeat_status": outcome.heartbeat_status},
                )
            else:
                self._store.mark_failed(self._activity_id, outcome.detail or "routine run failed")
        except Exception:  # the activity ledger is best-effort
            logger.exception("routine %s: activity record failed", outcome.routine_id)


def expire_before_digest(runtime: EvarnessRuntime) -> int:
    """Close what aged out (digest.yaml ``expiry``) just before the digest renders, so
    the stored state matches what it shows: the tasks past their grace, and the missed
    reminders already carried into ``reminder_missed_digests`` digests — the rest of
    the missed ones are counted as shown in this digest — and, the same way, the
    delivered reminders from before today the owner never answered (D18, PR 3b). Only a real run calls
    this, so a manual re-run neither counts nor expires. Returns how many expired;
    never raises (the digest's slot tools filter expired items anyway)."""
    data_dir = getattr(runtime, "data_dir", None)
    if data_dir is None:  # unit fakes without a data dir have no task store
        return 0
    expired = 0
    try:
        from evarness.services.digest.expiry import sweep_expired_tasks
        from evarness.services.tasks.store import TaskStore

        store = TaskStore(db_path=data_dir / "tasks.db")
        store.ensure_schema()
        expired += len(sweep_expired_tasks(store))
    except Exception:  # expiry must never stop the digest
        logger.exception("morning-digest: expiry sweep failed; rendering anyway")
    try:
        from evarness.services.digest.expiry import (
            sweep_missed_reminders,
            sweep_unacknowledged_reminders,
        )
        from evarness.services.notifications.store import ReminderStore

        reminders = ReminderStore(db_path=data_dir / "tasks.db")
        reminders.ensure_schema()
        expired += len(sweep_missed_reminders(reminders)[0])
        expired += len(sweep_unacknowledged_reminders(reminders))
    except Exception:  # expiry must never stop the digest
        logger.exception("morning-digest: missed-reminder sweep failed; rendering anyway")
    return expired


def run_before_digest(runtime: EvarnessRuntime) -> list[Any]:
    """Run the jobs ``digest.yaml`` ``run_first`` names, in order, before the digest
    renders — each through the scheduler's Run-now path, so each is a real, kept run.

    A job whose ``if_setting`` is off, that is not registered, turned off or already
    running (waited for instead) is handled in ``services/digest/run_first.py``. Every
    real digest run calls this — the scheduled one and a manual ``POST
    /routines/morning-digest/run`` alike; a preview does not. Never raises: whatever the
    jobs did, the digest renders."""
    try:
        from evarness.runtime.settings_catalog import (
            bool_setting,
            registry_catalog,
        )
        from evarness.services.digest.run_first import (
            load_run_first,
            run_first,
        )

        plan = load_run_first(getattr(runtime, "config_dir", None))
        if not plan.jobs:
            return []
        catalog = registry_catalog(getattr(runtime, "plugin_registry", None))
        return run_first(
            runtime.heartbeats,
            plan,
            setting_on=lambda name: bool_setting(name, catalog),
            trigger="digest",
        )
    except Exception:  # the jobs before the digest never stop it
        logger.exception("morning-digest: run_first failed; rendering anyway")
        return []


def execute_routine(
    runtime: EvarnessRuntime,
    routine: RoutineSpec,
    *,
    checked_at: datetime,
    record: bool = True,
    trigger: str = "manual",
) -> RoutineExecutionRecord:
    """Execute one routine's bound capability and return its outcome record.

    Shared by the scheduled tick loop (``record=True``, ``trigger="schedule"``) and
    the on-demand ``/routines run`` path. When ``record`` is ``False`` the run
    counters, ``last_run_at`` and the Activity row are left untouched (used by dry
    surfaces). Never raises: failures are captured in the returned record.

    A real run (``record=True``) of a routine already running here is not started: it
    comes back ``skipped``, and the run in progress is that routine's run.
    """
    if not record:
        return _execute_routine_run(runtime, routine, checked_at=checked_at, record=False)
    key = _routine_key(runtime, routine)
    with _routines_running_lock:
        if key in _routines_running:
            logger.info("routine %s is already running; not starting a second run", routine.id)
            return RoutineExecutionRecord(
                routine_id=routine.id,
                title=routine.title,
                template=routine.template,
                status=RoutineExecutionStatus.SKIPPED,
                detail="already running; not starting a second run",
            )
        _routines_running.add(key)
    try:
        return _execute_routine_run(
            runtime, routine, checked_at=checked_at, record=True, trigger=trigger
        )
    finally:
        with _routines_running_lock:
            _routines_running.discard(key)


def _execute_routine_run(
    runtime: EvarnessRuntime,
    routine: RoutineSpec,
    *,
    checked_at: datetime,
    record: bool,
    trigger: str = "manual",
) -> RoutineExecutionRecord:
    if routine.id == MORNING_DIGEST_ROUTINE_ID:
        # The digest's channel + sections are Settings → Digest's, read now — a manual
        # run between ticks must not render a stale section list.
        _, synced = sync_digest_routine(runtime)
        routine = synced or routine
        if record:
            # A real run (scheduled or manual, never a preview): the jobs digest.yaml
            # ``run_first`` names run first, then expiry, then the render.
            run_before_digest(runtime)
            expire_before_digest(runtime)
    activity = _RoutineActivity(runtime, routine, trigger=trigger) if record else None
    outcome = _execute_routine(runtime, routine, checked_at=checked_at, record=record)
    if activity is not None:
        activity.finish(outcome)
    return outcome


def _execute_routine(
    runtime: EvarnessRuntime,
    routine: RoutineSpec,
    *,
    checked_at: datetime,
    record: bool,
) -> RoutineExecutionRecord:

    def _fail(detail: str) -> RoutineExecutionRecord:
        if record:
            runtime.routine_store.record_run(routine.id, success=False, finished_at=checked_at)
        return RoutineExecutionRecord(
            routine_id=routine.id,
            title=routine.title,
            template=routine.template,
            status=RoutineExecutionStatus.FAILED,
            detail=detail,
        )

    if routine.template == "routine_tick":
        return _fail("routine_tick cannot execute itself recursively")

    # Resolve how this routine executes. A brief routine renders via the generic
    # ``skill_brief`` handler — accept BOTH the canonical manifest name
    # (``morning-briefing``, what new routines persist) and the legacy underscore
    # aliases (``morning_briefing``). Only when the template is not a loadable
    # brief do we fall back to a same-named heartbeat handler.
    brief_skill_id = resolve_brief_skill_id(runtime, routine.template)
    if brief_skill_id is not None:
        template_definition = HeartbeatDefinition(
            name="skill_brief", handler="skill_brief", schedule="manual"
        )
        skill_id = brief_skill_id
    else:
        found = next(
            (
                item
                for item in runtime.heartbeats.list_definitions()
                if item.name == routine.template
            ),
            None,
        )
        if found is None:
            if not runtime.heartbeats.has_handler(routine.template):
                return _fail(f"routine template heartbeat not registered: {routine.template}")
            found = HeartbeatDefinition(
                name=routine.template, handler=routine.template, schedule="manual"
            )
        template_definition = found
        skill_id = str(routine.metadata.get("skill_id", ""))

    run = runtime.heartbeats.trigger_now(
        HeartbeatDefinition(
            name=template_definition.name,
            handler=template_definition.handler,
            schedule=template_definition.schedule,
            enabled=template_definition.enabled,
            description=template_definition.description,
            params={
                **template_definition.params,
                "channel": routine.delivery_channel,
                "routine_id": routine.id,
                "skill_id": skill_id,
                **routine_render_params(routine),
            },
        )
    )
    success = run.status is HeartbeatStatus.SUCCESS
    if record:
        runtime.routine_store.record_run(routine.id, success=success, finished_at=checked_at)
    return RoutineExecutionRecord(
        routine_id=routine.id,
        title=routine.title,
        template=routine.template,
        status=(RoutineExecutionStatus.SUCCESS if success else RoutineExecutionStatus.FAILED),
        detail=run.output or run.error or "",
        heartbeat_status=run.status.value,
    )


def build_routine_tick_handler(runtime: EvarnessRuntime) -> HeartbeatHandler:
    """Return a heartbeat handler that executes due approved routines."""

    def handler(definition: HeartbeatDefinition) -> HeartbeatRun:
        checked_at = datetime.now(UTC).replace(microsecond=0)
        # Settings → Digest drives the seeded digest's schedule: sync it BEFORE the due
        # check so a time change takes effect at this tick, not after a restart.
        digest_enabled, _ = sync_digest_routine(runtime)
        # Claim each due slot BEFORE running it: a run outlives its tick (the digest's
        # run-first jobs take minutes), and a slot read as due by two callers in that
        # window fired twice. A slot another caller claimed first is not ours to run.
        due_routines = [
            routine
            for routine in runtime.routine_store.list_due(now=checked_at)
            if (digest_enabled or routine.id != MORNING_DIGEST_ROUTINE_ID)
            and runtime.routine_store.claim(routine, at=checked_at)
        ]
        executions: list[RoutineExecutionRecord] = [
            execute_routine(
                runtime, routine, checked_at=checked_at, record=True, trigger="schedule"
            )
            for routine in due_routines
        ]

        # Per-routine outcome ledger (routines learning loop): one signal per
        # execution so the reflection tick can mine per-routine failure rates.
        for record in executions:
            ok = record.status == RoutineExecutionStatus.SUCCESS
            try:
                runtime.signal_collector.record_metric(
                    metric_name="routine_run",
                    value=1.0 if ok else 0.0,
                    success=ok,
                    metadata={
                        "routine_id": record.routine_id,
                        "title": record.title,
                        "template": record.template,
                    },
                )
            except Exception:  # noqa: BLE001, S110 — ledger is best-effort
                pass

        result = RoutineTickResult(
            checked_at=checked_at,
            due_count=len(due_routines),
            executed_count=len(executions),
            success_count=sum(1 for item in executions if item.status == "success"),
            failure_count=sum(1 for item in executions if item.status == "failed"),
            skipped_count=sum(1 for item in executions if item.status == "skipped"),
            executions=tuple(executions),
        )
        status = HeartbeatStatus.FAILED if result.failure_count else HeartbeatStatus.SUCCESS
        output = (
            "routine_tick "
            f"due={result.due_count} "
            f"executed={result.executed_count} "
            f"success={result.success_count} "
            f"failed={result.failure_count} "
            f"skipped={result.skipped_count}"
        )
        failures = [item.detail for item in executions if item.status == "failed" and item.detail]
        return HeartbeatRun(
            name=definition.name,
            status=status,
            output=output,
            error="; ".join(failures),
        )

    return handler
