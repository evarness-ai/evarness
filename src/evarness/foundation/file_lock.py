"""An exclusive lock on an open file, held across processes, on POSIX and Windows.

POSIX takes ``flock``. Windows has no ``flock``; it locks a byte range with
``msvcrt.locking``, whose blocking mode gives up after about ten seconds, so the wait here
retries until the lock is free, as ``flock`` waits. The lock covers the file's first byte,
which works on an empty file: Windows locks a range past the end.
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError as exc:
                if exc.errno != errno.EDEADLOCK:  # not contention: a real failure
                    raise
                # Still held after LK_LOCK's own retries: keep waiting, as flock does.

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(fd: int) -> Iterator[None]:
    """Hold an exclusive lock on the open file ``fd`` for the block, waiting until it is free.

    The caller owns ``fd`` and closes it after the block.
    """
    _lock(fd)
    try:
        yield
    finally:
        _unlock(fd)


__all__ = ["exclusive_lock"]
