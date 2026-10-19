"""``foundation.file_lock``: one holder at a time across processes, on POSIX and Windows."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from evarness.foundation.file_lock import exclusive_lock

_HOLDER = textwrap.dedent("""
    import os, sys, time
    from evarness.foundation.file_lock import exclusive_lock

    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
    with exclusive_lock(fd):
        print("held", flush=True)
        time.sleep(float(sys.argv[2]))
    os.close(fd)
""")


def _open(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def test_a_second_process_waits_for_the_holder(tmp_path: Path) -> None:
    lock = tmp_path / "audit.lock"
    holder = subprocess.Popen(  # noqa: S603 - this interpreter, fixed code
        [sys.executable, "-c", _HOLDER, str(lock), "1.5"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        fd = _open(lock)
        started = time.monotonic()
        try:
            with exclusive_lock(fd):
                waited = time.monotonic() - started
        finally:
            os.close(fd)
    finally:
        holder.wait(timeout=30)
    # The holder slept 1.5 s with the lock: the wait covers most of it, not zero.
    assert waited > 0.75, f"took the lock after {waited:.2f}s while another process held it"


def test_the_lock_is_free_again_after_the_block(tmp_path: Path) -> None:
    lock = tmp_path / "audit.lock"
    for _ in range(3):  # each block releases, so the next one gets it at once
        fd = _open(lock)
        try:
            with exclusive_lock(fd):
                pass
        finally:
            os.close(fd)
    holder = subprocess.run(  # noqa: S603 - this interpreter, fixed code
        [sys.executable, "-c", _HOLDER, str(lock), "0"], capture_output=True, text=True, timeout=30
    )
    assert holder.stdout.strip() == "held"
