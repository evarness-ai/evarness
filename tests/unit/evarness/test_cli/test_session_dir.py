"""CLI sessions live under ``$EVARNESS_HOME``, never a hard-coded ``~/.evarness``.

``evarness.main`` makes a ``SessionManager`` at import, so every ``evarness`` command
creates the sessions directory: with ``Path.home()`` it landed in the real home even
when EVARNESS_HOME relocated everything else (a plugin's harness test, the demo).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.cli.session import SessionManager, default_sessions_dir


def test_the_sessions_dir_follows_evarness_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "evarness-home"
    monkeypatch.setenv("EVARNESS_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    assert default_sessions_dir() == home / "sessions"

    session = SessionManager().create(cwd=str(tmp_path))
    assert (home / "sessions" / f"{session.id}.json").exists()
    assert not (tmp_path / "user").exists()
