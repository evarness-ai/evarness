"""scripts/ci_scope.sh decides whether a change can affect the webui CI job, and fails open."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.posix("the CI scripts are bash; CI runs them on Linux")

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
    ("changed", "webui"),
    [
        ("README.md\ndocs/guides/a.md\nCHANGELOG.md", "false"),
        ("webui/src/App.tsx", "true"),
        ("src/evarness/main.py", "false"),
        ("docs/a.md\nsrc/evarness/main.py", "false"),
        (".github/workflows/ci.yml", "true"),
        (".github/ISSUE_TEMPLATE/bug.md", "false"),
        ("scripts/bundle_webui.py", "true"),
        ("pyproject.toml", "false"),
        ("mkdocs.yml", "false"),
        ("scripts/ci_quickstart.sh", "false"),
    ],
)
def test_a_change_runs_the_webui_job_only_when_it_can_affect_it(changed: str, webui: str) -> None:
    assert _scope(changed) == {"webui": webui}


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
    assert _scope(None, **env) == {"webui": "true"}


def test_an_empty_diff_runs_everything() -> None:
    assert _scope("") == {"webui": "true"}
