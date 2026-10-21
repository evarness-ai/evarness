"""One run of a routine per slot, whoever asks (the doubled 15:40 digest, 2026-10).

On the cloud trial the digest's run took ~3 minutes (its run-first jobs waited on the
Mac). routine_tick is a 60 s job, so the health watch read it as overdue, re-ran it
beside the run in progress, and the routine — not yet recorded as run — fired again.
These tests replay that on every path that can start a routine.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from evarness.runtime.handlers.ticks import build_routine_tick_handler, execute_routine
from evarness.services.health.models import CheckKind, HealthCheck, HealthState
from evarness.services.health.repair import heartbeat_retry_repairer
from evarness.services.heartbeat import (
    HeartbeatDefinition,
    HeartbeatRun,
    HeartbeatScheduler,
    HeartbeatStatus,
)
from evarness.services.routines import (
    RoutineApprovalStatus,
    RoutineExecutionStatus,
    RoutineStore,
    create_routine_spec,
)

_TICK = HeartbeatDefinition(name="routine_tick", handler="routine_tick", schedule="interval:60")


class _SlowRender:
    """A ``skill_brief`` that blocks until released, counting its renders."""

    def __init__(self) -> None:
        self.renders = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, definition: HeartbeatDefinition) -> HeartbeatRun:
        self.renders += 1
        self.started.set()
        self.release.wait(5)
        return HeartbeatRun(name=definition.name, status=HeartbeatStatus.SUCCESS, output="sent")


def _setup(tmp_path: Path) -> tuple[RoutineStore, HeartbeatScheduler, _SlowRender, str]:
    store = RoutineStore(tmp_path / "routines.db")
    spec = create_routine_spec(
        title="Digest",
        goal="Send the digest",
        schedule="interval:86400",
        template="skill_brief",
        approval_status=RoutineApprovalStatus.SCHEDULED,
        metadata={"skill_id": "morning-briefing"},
    )
    store.save(spec)
    render = _SlowRender()
    scheduler = HeartbeatScheduler()
    scheduler.register_handler("skill_brief", render)
    runtime = SimpleNamespace(routine_store=store, heartbeats=scheduler)
    scheduler.register_handler("routine_tick", build_routine_tick_handler(runtime))
    scheduler.register(_TICK)
    return store, scheduler, render, spec.id


def _start_tick(scheduler: HeartbeatScheduler) -> threading.Thread:
    worker = threading.Thread(target=scheduler.trigger_by_name, args=("routine_tick",))
    worker.start()
    return worker


def test_the_health_watch_re_run_during_a_long_digest_does_not_send_it_twice(
    tmp_path: Path,
) -> None:
    store, scheduler, render, routine_id = _setup(tmp_path)
    worker = _start_tick(scheduler)
    assert render.started.wait(5)

    # Minutes later: the watch's diagnostics must not call the busy tick overdue...
    later = datetime.now(UTC) + timedelta(minutes=3)
    assert [d.name for d in scheduler.diagnose(now=later)] == []
    # ...and its repair, if it ran anyway, must not start a second tick beside it.
    check = HealthCheck("heartbeat:routine_tick", CheckKind.SERVICE, HealthState.RED, "overdue")
    outcome = heartbeat_retry_repairer(scheduler)(check)
    assert outcome is not None and outcome.ok is False and not outcome.final

    render.release.set()
    worker.join(5)
    assert render.renders == 1
    assert store.load(routine_id).run_count == 1  # type: ignore[union-attr]


def test_a_second_tick_after_the_slot_is_claimed_finds_nothing_due(tmp_path: Path) -> None:
    # Another process on the same routines.db (or any caller past the scheduler's
    # guard): the slot was claimed before the render began, so it is not due again.
    store, scheduler, render, _ = _setup(tmp_path)
    worker = _start_tick(scheduler)
    assert render.started.wait(5)

    other = build_routine_tick_handler(SimpleNamespace(routine_store=store, heartbeats=scheduler))
    second = other(_TICK)

    render.release.set()
    worker.join(5)
    assert "due=0" in second.output
    assert render.renders == 1


def test_run_now_while_the_scheduled_run_is_in_progress_is_skipped(tmp_path: Path) -> None:
    store, scheduler, render, routine_id = _setup(tmp_path)
    worker = _start_tick(scheduler)
    assert render.started.wait(5)

    runtime = SimpleNamespace(routine_store=store, heartbeats=scheduler)
    manual = execute_routine(
        runtime, store.load(routine_id), checked_at=datetime.now(UTC)  # type: ignore[arg-type]
    )

    render.release.set()
    worker.join(5)
    assert manual.status == RoutineExecutionStatus.SKIPPED
    assert "already running" in manual.detail
    assert render.renders == 1


def test_run_now_after_the_run_finished_runs_again(tmp_path: Path) -> None:
    store, scheduler, render, routine_id = _setup(tmp_path)
    render.release.set()
    scheduler.trigger_by_name("routine_tick")

    runtime = SimpleNamespace(routine_store=store, heartbeats=scheduler)
    manual = execute_routine(
        runtime, store.load(routine_id), checked_at=datetime.now(UTC)  # type: ignore[arg-type]
    )

    assert manual.status == RoutineExecutionStatus.SUCCESS
    assert render.renders == 2
