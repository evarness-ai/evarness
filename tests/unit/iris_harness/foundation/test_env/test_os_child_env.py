"""``foundation.env.os_child_env``: what the OS needs in a child started with a minimal env.

A Windows child without SYSTEMROOT cannot load Winsock, so one that opens a socket (an
asyncio event loop does) fails with WinError 10106. Both the MCP stdio bridge and the
suite's multi-interpreter tests start children with an allow-listed environment.
"""

from __future__ import annotations

from iris_harness.foundation.env import WINDOWS_CHILD_ENV, os_child_env

_HOST = {
    "SystemRoot": r"C:\Windows",
    "TEMP": r"C:\t",
    "ComSpec": "cmd.exe",
    "Path": r"C:\Windows",
    "API_TOKEN": "s",
    "IRIS_AUTH_SECRET": "x",
}


def test_on_windows_it_keeps_the_os_variables_in_their_own_spelling() -> None:
    assert os_child_env(_HOST, windows=True) == {
        "SystemRoot": r"C:\Windows",
        "TEMP": r"C:\t",
        "ComSpec": "cmd.exe",
    }


def test_on_posix_it_adds_nothing() -> None:
    assert os_child_env(_HOST, windows=False) == {}


def test_no_credential_or_iris_name_is_among_them() -> None:
    words = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "IRIS")
    assert not [name for name in WINDOWS_CHILD_ENV if any(w in name for w in words)]
