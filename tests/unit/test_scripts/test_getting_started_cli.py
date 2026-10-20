"""scripts/getting_started.py is CI's, not the user's CLI; it says so when mistaken for it."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "getting_started_cli_under_test", ROOT / "scripts" / "getting_started.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the script's dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("word", ["doctor", "email", "setup"])
def test_an_evarness_command_given_to_the_script_points_at_the_cli(
    word: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clone where `uv sync` installed no `evarness` command leaves this script as the
    nearest runnable thing; argparse's bare "invalid choice" sent one tester nowhere."""
    assert _script().main([word]) == 2
    err = capsys.readouterr().err
    assert repr(word) in err and "evarness" in err and "poetry install" in err
    assert "uv sync" in err and "invalid choice" not in err


def test_the_scripts_own_commands_still_parse(capsys: pytest.CaptureFixture[str]) -> None:
    module = _script()
    assert module.main(["list"]) == 0  # the real pages: `list` prints what `run` would do
    assert "install.md" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        module.main(["--help"])
    assert stop.value.code == 0
