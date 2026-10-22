"""The named ``.env`` files (``foundation/env_files.py``): which are read, in what order,
and where ``evarness setup`` writes."""

from __future__ import annotations

import os
from pathlib import Path

import dotenv
import pytest

from evarness.foundation import env_files


def _real_load(path: Path, override: bool = False) -> bool:
    """The session conftest turns ``dotenv.load_dotenv`` into a no-op (the developer's
    repo ``.env`` must never reach a test); these tests read their own temp files."""
    values = dotenv.dotenv_values(path)
    for key, value in values.items():
        if value is None:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
    return bool(values)


@pytest.fixture(autouse=True)
def _own_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(env_files.dotenv, "load_dotenv", _real_load)
    monkeypatch.setattr(env_files, "_checkout_root", lambda: None)
    monkeypatch.delenv("EVARNESS_HOME", raising=False)
    monkeypatch.delenv("EVARNESS_T_SETTING", raising=False)
    # No EVARNESS_HOME: the default home is the fixture's, never the developer's.
    monkeypatch.setattr(
        "evarness.foundation.legacy.legacy_fallback_dir",
        lambda primary, legacy, what: tmp_path / "default-home",
    )


def test_an_installed_tool_reads_the_default_homes_file(tmp_path: Path) -> None:
    home = tmp_path / "default-home"
    home.mkdir()
    (home / ".env").write_text("EVARNESS_T_SETTING=from-home\n", encoding="utf-8")
    assert env_files.load_env_files() == [home / ".env"]
    assert os.environ["EVARNESS_T_SETTING"] == "from-home"
    assert env_files.settings_env_path() == home / ".env"


def test_the_default_homes_file_can_point_at_a_chosen_home(tmp_path: Path) -> None:
    default = tmp_path / "default-home"
    chosen = tmp_path / "chosen"
    default.mkdir()
    chosen.mkdir()
    (default / ".env").write_text(f"EVARNESS_HOME={chosen}\n", encoding="utf-8")
    (chosen / ".env").write_text("EVARNESS_T_SETTING=from-chosen\n", encoding="utf-8")
    assert env_files.load_env_files() == [default / ".env", chosen / ".env"]
    assert os.environ["EVARNESS_HOME"] == str(chosen)
    assert os.environ["EVARNESS_T_SETTING"] == "from-chosen"
    # Once the home is known, the settings file is the chosen home's.
    assert env_files.settings_env_path() == chosen / ".env"


def test_the_shell_wins_over_every_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "shell-home"
    home.mkdir()
    (home / ".env").write_text("EVARNESS_T_SETTING=from-file\n", encoding="utf-8")
    monkeypatch.setenv("EVARNESS_HOME", str(home))
    monkeypatch.setenv("EVARNESS_T_SETTING", "from-shell")
    assert env_files.home_is_from_shell()
    assert env_files.load_env_files() == [home / ".env"]
    assert os.environ["EVARNESS_T_SETTING"] == "from-shell"


def test_a_checkout_reads_its_own_file_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "default-home"
    home.mkdir()
    (repo / ".env").write_text("EVARNESS_T_SETTING=from-repo\n", encoding="utf-8")
    (home / ".env").write_text("EVARNESS_T_SETTING=from-home\n", encoding="utf-8")
    monkeypatch.setattr(env_files, "_checkout_root", lambda: repo)
    assert env_files.load_env_files() == [repo / ".env", home / ".env"]
    assert os.environ["EVARNESS_T_SETTING"] == "from-repo"
    assert env_files.settings_env_path() == repo / ".env"


def test_missing_files_are_simply_not_read(tmp_path: Path) -> None:
    assert env_files.load_env_files() == []
    assert not env_files.home_is_from_shell()
