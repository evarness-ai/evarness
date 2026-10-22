"""The ``.env`` files an Evarness process reads, and the one ``evarness setup`` writes.

An installed ``evarness`` (uv tool, pipx) used to call ``load_dotenv()`` with no path,
which searches upward from the *calling module's* directory: inside a site-packages tree
that finds nothing, so a setting the wizard wrote to a ``.env`` in the current directory
was read only when the next command happened to run from that directory. A checkout
found the repository's ``.env`` the same way, by accident of where ``src/`` sits.

Now the files are named, in the order they are read; a variable the shell already
exports always wins (``override=False``), and an earlier file wins over a later one:

1. The checkout's ``.env``, when running from a checkout (the developer's file).
2. The default home's ``.env`` (``$EVARNESS_HOME/.env`` when the shell sets the home,
   else ``~/.evarness/.env``): the installed tool's settings file, what ``evarness
   setup`` writes. It may itself set ``EVARNESS_HOME``, which is how a home the owner
   chose at setup is found again without a shell profile edit.
3. That chosen home's ``.env``, when it is a different directory.

``settings_env_path()`` is where the wizard writes: the checkout's ``.env`` in a
checkout, else the resolved home's ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv

from evarness.foundation.paths import _checkout_root, evarness_home

HOME_ENV = "EVARNESS_HOME"


def settings_env_path() -> Path:
    """The ``.env`` every ``evarness`` command reads and ``evarness setup`` writes to."""
    root = _checkout_root()
    if root is not None:
        return root / ".env"
    return evarness_home() / ".env"


def env_files_in_read_order() -> list[Path]:
    """The files :func:`load_env_files` reads, in order, whether or not they exist.

    Evaluated against the environment as it stands: after a load, the chosen home's
    file (3) appears once the default home's file (2) has set ``EVARNESS_HOME``.
    """
    files: list[Path] = []
    root = _checkout_root()
    if root is not None:
        files.append(root / ".env")
    files.append(evarness_home() / ".env")
    return files


def load_env_files() -> list[Path]:
    """Read the files above into ``os.environ`` (never over a variable the shell set)
    and return the ones that existed."""
    loaded: list[Path] = []
    seen: set[Path] = set()
    # Two passes: the default home's file may set EVARNESS_HOME, after which the chosen
    # home's file is named too. A file is read at most once.
    for _ in range(2):
        for path in env_files_in_read_order():
            if path in seen:
                continue
            seen.add(path)
            if path.is_file() and dotenv.load_dotenv(path, override=False):
                loaded.append(path)
    return loaded


def home_is_from_shell() -> bool:
    """Whether the shell (not a ``.env`` file) set ``EVARNESS_HOME``. Read before
    :func:`load_env_files` to tell the two apart; after it, both look the same."""
    return bool(os.environ.get(HOME_ENV))


__all__ = [
    "HOME_ENV",
    "env_files_in_read_order",
    "home_is_from_shell",
    "load_env_files",
    "settings_env_path",
]
