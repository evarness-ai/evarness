"""The version has one source: ``evarness.__version__``, which release.yml requires to
equal ``pyproject.toml``'s. Everything that reports a version reads it; a literal copy went
stale and shipped 0.1.1 printing "Evarness 0.1.0"."""

from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

import evarness
from evarness.cli import render
from evarness.main import app
from evarness.tools.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[2]


def test_the_package_version_is_the_one_pyproject_declares() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert evarness.__version__ == declared["project"]["version"]


def test_evarness_version_prints_the_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert evarness.__version__ in result.output


def test_the_other_places_that_report_a_version_read_the_package_version() -> None:
    assert render.EVARNESS_VERSION == evarness.__version__
    assert mcp_server.SERVER_VERSION == evarness.__version__
