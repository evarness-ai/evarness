"""scripts/ci_scope.sh decides which slow CI jobs a change can affect, and fails open."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci_scope.sh"


def _scope(changed: str | None, **env: str) -> dict[str, str]:
    full_env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", **env}
    if changed is not None:
        full_env["CHANGED_OVERRIDE"] = changed
    out = subprocess.run(
        ["bash", str(SCRIPT)], cwd=ROOT, env=full_env, capture_output=True, text=True, check=True
    ).stdout
    return dict(line.split("=") for line in out.split())


@pytest.mark.parametrize(
    ("changed", "webui", "engine"),
    [
        ("README.md\ndocs/guides/a.md\nCHANGELOG.md", "false", "false"),
        ("webui/src/App.tsx", "true", "true"),
        ("src/iris_harness/main.py", "false", "true"),
        ("docs/a.md\nsrc/iris_harness/main.py", "false", "true"),
        (".github/workflows/ci.yml", "true", "true"),
        (".github/ISSUE_TEMPLATE/bug.md", "false", "false"),
        ("scripts/bundle_webui.py", "true", "true"),
        # pyproject, mkdocs.yml and the scripts are code, not documentation.
        ("pyproject.toml", "false", "true"),
        ("mkdocs.yml", "false", "true"),
        ("scripts/ci_quickstart.sh", "false", "true"),
    ],
)
def test_a_change_runs_the_jobs_it_can_affect(changed: str, webui: str, engine: str) -> None:
    assert _scope(changed) == {"webui": webui, "engine": engine}


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"EVENT_NAME": "push"},
        {"EVENT_NAME": "workflow_dispatch"},
        # A pull request whose base commit cannot be found.
        {"EVENT_NAME": "pull_request", "PR_BASE_SHA": "0" * 40},
        {"EVENT_NAME": "pull_request", "PR_BASE_SHA": ""},
    ],
)
def test_it_fails_open(env: dict[str, str]) -> None:
    assert _scope(None, **env) == {"webui": "true", "engine": "true"}


def test_an_empty_diff_runs_everything() -> None:
    assert _scope("") == {"webui": "true", "engine": "true"}
