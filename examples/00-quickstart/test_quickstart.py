"""The quickstart, as a new user runs it: ``evarness doctor``, ``evarness email demo``, a first turn.

The two commands run as child processes, the way a user types them, in a throwaway
home: ``HOME`` and ``EVARNESS_HOME`` point into a temporary directory, the OS keyring is
out of reach, and the doctor's model-server probe goes to a closed port on this
machine, so nothing leaves it. ``evarness email demo`` needs no model server and refuses
the network itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from first_turn import ANSWER, first_turn

from evarness.sdk.audit import AuditLog


def evarness(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    """``evarness <args>`` in a child process. The ``evarness`` command is
    ``evarness.main:main``; running the module is the same program without depending
    on where the console script was installed."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("EVARNESS_") and name not in {"HOME", "OLLAMA_BASE_URL"}
    }
    env |= {
        "HOME": str(home),
        "EVARNESS_HOME": str(home / ".evarness"),
        # A closed port on this machine: the probe fails fast, nothing leaves the box.
        "OLLAMA_BASE_URL": "http://127.0.0.1:9",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
    }
    return subprocess.run(  # noqa: S603 -- a fixed argv, our own interpreter
        [sys.executable, "-m", "evarness.main", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )


def test_evarness_doctor_gives_a_verdict(tmp_path: Path) -> None:
    done = evarness("doctor", "--json", home=tmp_path)

    report = json.loads(done.stdout)
    # With no model server running, the verdict is "demo only" or "not ready" on this
    # machine; with Ollama and the starter models, "ready". The exit code says which.
    assert report["verdict"] in {"ready", "demo_only", "not_ready"}
    assert done.returncode == report["exit_code"]
    names = {check["name"] for check in report["checks"]}
    assert {"python", "ollama"} <= {name.lower() for name in names}


def test_evarness_email_demo_runs_offline_and_audits_what_it_did(tmp_path: Path) -> None:
    home = tmp_path / "demo"
    done = evarness("email", "demo", "--home", str(home), home=tmp_path)

    assert done.returncode == 0, done.stderr[-2000:]
    assert "What Evarness just did" in done.stdout
    assert "Network connections attempted: 0" in done.stdout
    assert "Labels (setup step 6): labelled" in done.stdout
    # Its governance ledger is in the demo home: the model calls, and the approval of
    # mailbox writes for the demo's own synthetic account.
    rows = AuditLog(db_path=home / "governance" / "audit.db").query()
    assert {row.hook_point for row in rows} >= {"pre_llm_call", "mailbox_writes"}


def test_a_first_governed_turn() -> None:
    result, rows, gaps = first_turn()

    assert result.text == ANSWER
    assert {row.hook_point for row in rows} >= {"pre_turn", "pre_llm_call", "pre_response"}
    assert gaps == []
