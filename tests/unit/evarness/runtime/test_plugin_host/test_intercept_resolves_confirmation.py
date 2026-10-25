"""A plugin declares its own ``resolves_confirmation`` (E4, ADR-0106)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evarness.memory.state.continuations import ContinuationStore
from evarness.runtime.continuations import ContinuationRegistry
from evarness.runtime.intercept_dispatch import InterceptDispatch
from evarness.runtime.intercepts import InterceptSpec
from evarness.runtime.plugin_host.api import PluginAPI
from evarness.runtime.plugin_host.registry import PluginRegistry


def _api(registry: PluginRegistry) -> PluginAPI:
    api = PluginAPI.__new__(PluginAPI)
    api.plugin, api._registry = "acme", registry  # type: ignore[assignment]
    return api


def _dispatch(
    tmp_path: Path, declared: list[InterceptSpec], register: dict[str, bool]
) -> tuple[Any, ContinuationRegistry, list[str]]:
    """The real dispatch over a real plugin registry; ``register`` maps name -> the flag."""
    plugins, calls = PluginRegistry(), []
    api = _api(plugins)
    for name, flag in register.items():

        def handler(_m: str, *, session_id: str, span: Any = None, _name: str = name) -> Any:
            calls.append(_name)
            return object()

        api.register_intercept(name, handler, resolves_confirmation=flag)
    continuations = ContinuationRegistry(store=ContinuationStore(db_path=tmp_path / "c.db"))
    host = SimpleNamespace(
        intercept_chain=declared,
        plugin_registry=plugins,
        continuations=continuations,
        profile=None,
    )
    dispatch = InterceptDispatch(host)  # type: ignore[arg-type]
    dispatch._log_chat_result = lambda *_a, **_k: None  # type: ignore[method-assign]
    return dispatch, continuations, calls


def _say_yes(dispatch: InterceptDispatch) -> Any:
    return dispatch.dispatch("yes", session_id="s1", channel="console", span=None)


def test_the_flag_reaches_the_registered_spec(tmp_path: Path) -> None:
    plugins = PluginRegistry()
    api = _api(plugins)
    api.register_intercept("claims", lambda m, *, session_id, span=None: None)
    api.register_intercept(
        "approves", lambda m, *, session_id, span=None: None, resolves_confirmation=True
    )
    flags = {r.spec.name: r.spec.resolves_confirmation for r in plugins.intercepts()}
    assert flags == {"claims": False, "approves": True}


@pytest.mark.parametrize("declared_in_yaml", [False, True], ids=["undeclared", "declared"])
def test_a_plugin_intercept_that_declares_it_is_shielded_from_another_owners_yes(
    tmp_path: Path, declared_in_yaml: bool
) -> None:
    # A YAML row that does NOT say it (the plugin's own flag is what shields it), or no row.
    rows = [InterceptSpec("approves", "plugin:acme")] if declared_in_yaml else []
    dispatch, continuations, calls = _dispatch(tmp_path, rows, {"approves": True})
    continuations.ask("s1", "planner", question="proceed with this plan?", intent="planner")

    assert _say_yes(dispatch) is None
    assert calls == []  # never consulted


def test_without_the_flag_the_same_intercept_would_have_claimed_the_yes(tmp_path: Path) -> None:
    dispatch, continuations, calls = _dispatch(tmp_path, [], {"approves": False})
    continuations.ask("s1", "planner", question="proceed with this plan?", intent="planner")

    assert _say_yes(dispatch) is not None
    assert calls == ["approves"]


def test_it_still_answers_the_question_it_owns(tmp_path: Path) -> None:
    dispatch, continuations, calls = _dispatch(tmp_path, [], {"approves": True})
    continuations.ask("s1", "approves", question="approve the plan?")

    assert _say_yes(dispatch) is not None
    assert calls == ["approves"]
