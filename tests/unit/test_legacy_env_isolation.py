"""No legacy ``IRIS_*`` variable reaches a test (tests/conftest.py strips them, issue #386).

``import evarness`` aliases every ``IRIS_*`` variable to ``EVARNESS_*`` where the new name is
unset (foundation/legacy.py), until 0.3.0. conftest used to strip only ``EVARNESS_*``, BEFORE
that import ran, so a developer's own legacy flags came straight back under the new names: a
live ``IRIS_SPIKE_GMAIL_PASSWORD`` became visible as ``EVARNESS_SPIKE_GMAIL_PASSWORD`` and
failed ``test_secret_isolation``, and about forty dev flags switched features on across the
suite. The strip has to cover the legacy prefix too, and it has to happen before the alias.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
SECRET = "live-looking-secret-value"


def _child(snippet: str, **legacy: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IRIS_", "EVARNESS_"))}
    env.update(legacy)
    code = f"import os, runpy\nrunpy.run_path({str(CONFTEST)!r})\n{snippet}\n"
    return subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )


def test_no_legacy_variable_reaches_a_test() -> None:
    assert [k for k in os.environ if k.startswith("IRIS_")] == []


def test_a_legacy_secret_is_not_visible_after_the_alias_runs() -> None:
    """Load the conftest in a child whose environment holds a legacy secret, then import the
    package, which is what runs the aliasing. The secret must be neither under its old name
    nor under the new one."""
    result = _child(
        "import evarness\n"
        "print(repr(os.environ.get('EVARNESS_SPIKE_GMAIL_PASSWORD')),"
        " repr(os.environ.get('IRIS_SPIKE_GMAIL_PASSWORD')))",
        IRIS_SPIKE_GMAIL_PASSWORD=SECRET,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "None None"
    assert SECRET not in result.stdout + result.stderr


def test_a_legacy_feature_flag_does_not_switch_a_feature_on() -> None:
    result = _child(
        "import evarness\nprint(sorted(k for k in os.environ if k == 'EVARNESS_LEARNING_ANALYST'))",
        IRIS_LEARNING_ANALYST="1",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"


def test_the_suite_s_own_controls_are_still_kept() -> None:
    """Only the legacy prefix is new: the EVARNESS_* names the suite sets stay."""
    result = _child("print(os.environ.get('EVARNESS_HOME', '') != '')", EVARNESS_AUTH_SECRET="x")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True"
