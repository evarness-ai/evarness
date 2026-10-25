"""A plugin can own its heartbeat's schedule; heartbeats.yaml wins a name (E3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from evarness.services.heartbeat import (
    HeartbeatDefinition,
    HeartbeatRun,
    HeartbeatScheduler,
    HeartbeatStatus,
)
from evarness.services.heartbeat.models import Origin


def _ok(definition: HeartbeatDefinition) -> HeartbeatRun:
    return HeartbeatRun(
        name=definition.name, status=HeartbeatStatus.SUCCESS, finished_at=datetime.now(UTC)
    )


YAML = HeartbeatDefinition(name="job", handler="job", schedule="interval:120", plugin="acme")
PLUGIN = HeartbeatDefinition(name="job", handler="job", schedule="interval:30", plugin="acme")


def _scheduler() -> HeartbeatScheduler:
    return HeartbeatScheduler(handlers={"job": _ok})


@pytest.mark.parametrize("config_first", [True, False], ids=["config-first", "plugin-first"])
def test_the_config_row_wins_whichever_arrives_first(
    config_first: bool, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler = _scheduler()
    with caplog.at_level(logging.WARNING, logger="evarness.services.heartbeat.scheduler"):
        if config_first:
            scheduler.register(YAML)
            scheduler.register(PLUGIN, origin="plugin")
        else:
            scheduler.register(PLUGIN, origin="plugin")
            scheduler.register(YAML)

    assert scheduler.declared("job") == YAML
    drift = [r.getMessage() for r in caplog.records if "differ" in r.getMessage()]
    assert len(drift) == 1 and "schedule" in drift[0] and "config row wins" in drift[0]


@pytest.mark.parametrize("config_first", [True, False])
def test_identical_copies_are_not_drift(
    config_first: bool, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler = _scheduler()
    with caplog.at_level(logging.WARNING, logger="evarness.services.heartbeat.scheduler"):
        order = [(YAML, "config"), (YAML, "plugin")]
        for definition, origin in order if config_first else reversed(order):
            scheduler.register(definition, origin=origin)  # type: ignore[arg-type]
    assert scheduler.declared("job") == YAML
    assert not [r for r in caplog.records if "differ" in r.getMessage()]


def test_a_plugin_alone_owns_its_schedule() -> None:
    scheduler = _scheduler()
    assert scheduler.register(PLUGIN, origin="plugin") is True
    assert scheduler.declared("job") == PLUGIN


def test_a_plugin_handler_arriving_after_the_config_row_activates_it() -> None:
    """The row was registered with no handler; the plugin's registration is what binds it."""
    scheduler = HeartbeatScheduler()
    assert scheduler.register(YAML) is False  # no handler yet
    scheduler.register_handler("job", _ok)
    assert scheduler.register(PLUGIN, origin="plugin") is True
    assert scheduler.declared("job") == YAML  # and the config schedule is the one kept


def test_register_heartbeat_carries_the_definitions_other_fields() -> None:
    from evarness.runtime.plugin_host.api import PluginAPI

    seen: dict[str, Any] = {}

    class _Heartbeats:
        def register_handler(self, name: str, handler: Any) -> None:
            seen["handler"] = name

        def register(self, definition: HeartbeatDefinition, *, origin: Origin = "config") -> bool:
            seen["definition"], seen["origin"] = definition, origin
            return True

    class _Services:
        heartbeats = _Heartbeats()

    class _Registry:
        def add_heartbeat(self, plugin: str, name: str, handler: Any, definition: Any) -> Any:
            return handler

    api = PluginAPI.__new__(PluginAPI)
    api.plugin, api.services, api._registry = "acme", _Services(), _Registry()  # type: ignore[assignment]
    api.register_heartbeat(
        "job",
        _ok,
        schedule="interval:60",
        platforms=("darwin",),
        params={"k": 1},
        record_runs=False,
        retry_skipped_after_minutes=5,
    )

    definition = seen["definition"]
    assert (definition.platforms, definition.params) == (("darwin",), {"k": 1})
    assert (definition.record_runs, definition.retry_skipped_after_minutes) == (False, 5)
    assert definition.plugin == "acme" and seen["origin"] == "plugin"
