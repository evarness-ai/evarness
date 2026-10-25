"""Where installed plugins live on disk — found without importing or loading them.

The plugin host (``runtime/plugin_host/loader.py``) loads the plugins a profile names.
Some things a plugin ships are needed without a runtime and whether or not the plugin is
mounted: its memory vocabulary (ADR-0115 — what was stored with a plugin's terms must
stay readable when the plugin is switched off). This module answers only "which plugin
directories exist", from the same three places the loader looks, in the same order:

1. **builtin** — each package under ``evarness.plugins_builtin`` with a manifest;
2. **entry point** — each ``evarness.plugins`` entry point, its package directory;
3. **home** — ``$EVARNESS_HOME/plugins/<name>/`` with a manifest.

A name found earlier wins, as it does when loading. Nothing here imports a plugin's
module: packages are located with ``importlib.util.find_spec`` on the package, which
runs only its (and its parents') ``__init__``.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "evarness.plugins"
# The 0.1.x group name. A plugin published against it still mounts, with one warning
# naming it; the group is dropped in 0.3.0 (src/evarness/foundation/legacy.py has the rule).
LEGACY_ENTRY_POINT_GROUP = "iris_harness.plugins"
_legacy_group_warned: set[str] = set()


def plugin_entry_points() -> list[importlib.metadata.EntryPoint]:
    """Every plugin entry point: the current group, then the 0.1.x group (warned once per plugin)."""
    import warnings

    eps = list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    seen = {ep.name for ep in eps}
    for ep in importlib.metadata.entry_points(group=LEGACY_ENTRY_POINT_GROUP):
        if ep.name in seen:
            continue
        if ep.name not in _legacy_group_warned:
            _legacy_group_warned.add(ep.name)
            warnings.warn(
                f"plugin {ep.name!r} registers under the {LEGACY_ENTRY_POINT_GROUP!r} entry-point "
                f"group; the group is now {ENTRY_POINT_GROUP!r} and the old one is dropped in 0.3.0.",
                DeprecationWarning,
                stacklevel=2,
            )
        eps.append(ep)
    return eps


BUILTIN_PACKAGE = "evarness.plugins_builtin"
MANIFEST_FILENAME = "manifest.yaml"


def evarness_home() -> Path:
    """``$EVARNESS_HOME`` (tests relocate it) else ``~/.evarness``."""
    home = os.environ.get("EVARNESS_HOME")
    if home:
        return Path(home).expanduser()
    from evarness.foundation.legacy import legacy_fallback_dir

    return legacy_fallback_dir(
        Path.home() / ".evarness", Path.home() / ".iris", what="The Evarness home"
    )


def _package_dir(package: str) -> Path | None:
    """Where the dotted ``package`` lives on disk, found without importing any of it.

    ``importlib.util.find_spec("a.b.c")`` imports ``a`` and ``a.b`` to learn ``a.b``'s
    search path, which runs their ``__init__.py``. A plugin the owner switched off must
    not run, and locating a directory is a question about files, so only the top-level
    name goes to ``find_spec`` (a lookup that imports nothing) and the rest is walked as
    directories under each of its locations. A namespace package is fine (``find_spec`` of
    the top-level name returns every portion). A package that extends its own ``__path__``
    when it is imported (``pkgutil.extend_path``) is not followed: that needs the import.
    """
    top, _, rest = package.partition(".")
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    locations = [Path(location) for location in spec.submodule_search_locations or []]
    if not locations:
        # A single-module top-level name: its directory, when nothing is below it.
        return Path(spec.origin).parent if spec.origin and not rest else None
    for location in locations:
        candidate = location.joinpath(*rest.split(".")) if rest else location
        if candidate.is_dir():
            return candidate
    return None


def _builtin_dirs() -> list[tuple[str, Path]]:
    root = _package_dir(BUILTIN_PACKAGE)
    if root is None:
        return []
    return [
        (child.name, child)
        for child in sorted(root.iterdir())
        if child.is_dir() and (child / MANIFEST_FILENAME).is_file()
    ]


def _entry_point_dirs() -> list[tuple[str, Path]]:
    try:
        eps = plugin_entry_points()
    except Exception:  # noqa: BLE001 — metadata scan is best-effort
        return []
    found: list[tuple[str, Path]] = []
    for ep in sorted(eps, key=lambda e: e.name):
        module = ep.value.split(":", 1)[0].strip()
        package = module.rpartition(".")[0] or module
        directory = _package_dir(package)
        if directory is not None:
            found.append((ep.name, directory))
    return found


def _home_dirs(home: Path) -> list[tuple[str, Path]]:
    plugins = home / "plugins"
    if not plugins.is_dir():
        return []
    return [
        (child.name, child)
        for child in sorted(plugins.iterdir())
        if child.is_dir() and (child / MANIFEST_FILENAME).is_file()
    ]


def installed_plugin_dirs(home: Path | None = None) -> list[tuple[str, Path]]:
    """``(name, directory)`` of every installed plugin, first place found wins."""
    seen: dict[str, Path] = {}
    for name, directory in (
        *_builtin_dirs(),
        *_entry_point_dirs(),
        *_home_dirs(home or evarness_home()),
    ):
        seen.setdefault(name, directory)
    return list(seen.items())


__all__ = [
    "BUILTIN_PACKAGE",
    "ENTRY_POINT_GROUP",
    "MANIFEST_FILENAME",
    "installed_plugin_dirs",
    "evarness_home",
]
