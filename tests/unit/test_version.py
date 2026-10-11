"""The version has one source: ``iris_harness.__version__``, which release.yml requires to
equal ``pyproject.toml``'s. Everything that reports a version reads it; a literal copy went
stale and shipped 0.1.1 printing "IRIS 0.1.0"."""

from __future__ import annotations

from pathlib import Path

import tomllib
from typer.testing import CliRunner

import iris_harness
from iris_harness.cli import render
from iris_harness.main import app
from iris_harness.tools.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[2]


def test_the_package_version_is_the_one_pyproject_declares() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert iris_harness.__version__ == declared["tool"]["poetry"]["version"]


def test_iris_version_prints_the_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert iris_harness.__version__ in result.output


def test_the_other_places_that_report_a_version_read_the_package_version() -> None:
    assert render.IRIS_VERSION == iris_harness.__version__
    assert mcp_server.SERVER_VERSION == iris_harness.__version__
