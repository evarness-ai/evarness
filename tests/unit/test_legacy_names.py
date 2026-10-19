"""The 0.1.x names (``iris_harness``, ``IRIS_*``, ``~/.iris``) keep working for one cycle."""

from __future__ import annotations

import importlib
import sys
import warnings
from pathlib import Path

import pytest

from evarness.foundation import legacy as _legacy


def test_iris_harness_import_resolves_to_evarness_and_warns() -> None:
    sys.modules.pop("iris_harness", None)
    with pytest.warns(DeprecationWarning, match="iris_harness.*evarness"):
        import iris_harness  # noqa: F401 - the shim under test

    import evarness.sdk.tools as new_tools

    old_tools = importlib.import_module("iris_harness.sdk.tools")
    assert old_tools is new_tools
    from iris_harness.sdk.tools import FailedResult as OldFailed  # type: ignore[import-not-found]

    assert OldFailed is new_tools.FailedResult


def test_iris_personal_import_resolves_to_evarness_personal() -> None:
    sys.modules.pop("iris_personal", None)
    with pytest.warns(DeprecationWarning):
        old = importlib.import_module("iris_personal.email")
    new = importlib.import_module("evarness_personal.email")
    assert old is new


def test_iris_env_is_read_as_evarness_env_and_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_LEGACY_TEST_FLAG", "1")
    monkeypatch.delenv("EVARNESS_LEGACY_TEST_FLAG", raising=False)
    monkeypatch.setattr(_legacy, "_env_warned", False)
    with pytest.warns(DeprecationWarning, match="EVARNESS_LEGACY_TEST_FLAG"):
        copied = _legacy.alias_legacy_environment()
    assert "EVARNESS_LEGACY_TEST_FLAG" in copied
    import os

    assert os.environ["EVARNESS_LEGACY_TEST_FLAG"] == "1"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _legacy.alias_legacy_environment()  # second call: nothing new, no warning


def test_new_env_name_wins_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_LEGACY_BOTH", "old")
    monkeypatch.setenv("EVARNESS_LEGACY_BOTH", "new")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _legacy.alias_legacy_environment()
    import os

    assert os.environ["EVARNESS_LEGACY_BOTH"] == "new"


def test_legacy_home_is_used_only_when_new_home_is_absent(tmp_path: Path) -> None:
    old = tmp_path / ".iris"
    new = tmp_path / ".evarness"
    _legacy._dir_warned.clear()
    assert _legacy.legacy_fallback_dir(new, old, what="home") == new  # neither exists
    old.mkdir()
    with pytest.warns(DeprecationWarning, match="0.1.x location"):
        assert _legacy.legacy_fallback_dir(new, old, what="home") == old
    new.mkdir()
    assert _legacy.legacy_fallback_dir(new, old, what="home") == new
