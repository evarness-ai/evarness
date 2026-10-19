"""The names Evarness had before 0.2.0, kept working for one deprecation cycle.

Until 0.2.0 the project was IRIS: the package was ``iris_harness`` (the domains
``iris_personal``), the environment variables ``IRIS_*`` and the home ``~/.iris``. Each
old name still works and warns once; all three are removed in 0.3.0.

* :func:`alias_legacy_environment` copies every ``IRIS_*`` variable to its ``EVARNESS_*``
  name when that name is unset. It runs when ``evarness`` is imported.
* :func:`install_import_alias` makes ``import iris_harness.sdk.tools`` resolve to
  ``evarness.sdk.tools`` (the same module object, so ``isinstance`` and identity hold).
  The ``iris_harness`` and ``iris_personal`` shim packages call it.
* :func:`legacy_fallback_dir` answers "use the old directory?" for a home or data
  location: yes when the new one does not exist and the old one does.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import sys
import warnings
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

LEGACY_ENV_PREFIX = "IRIS_"
ENV_PREFIX = "EVARNESS_"
REMOVED_IN = "0.3.0"

_env_warned = False
_dir_warned: set[str] = set()


def alias_legacy_environment() -> list[str]:
    """Copy each ``IRIS_*`` variable to ``EVARNESS_*`` where the new name is unset.

    Returns the new names that were set. Warns once per process, naming them. A
    variable set under both names is left alone: the new name wins and nothing is said.
    """
    global _env_warned
    copied: list[str] = []
    for name, value in list(os.environ.items()):
        if not name.startswith(LEGACY_ENV_PREFIX):
            continue
        new = ENV_PREFIX + name[len(LEGACY_ENV_PREFIX) :]
        if new in os.environ:
            continue
        os.environ[new] = value
        copied.append(new)
    if copied and not _env_warned:
        _env_warned = True
        warnings.warn(
            f"IRIS_* environment variables are now EVARNESS_*; read as {sorted(copied)}. "
            f"Rename them: the IRIS_ names are removed in {REMOVED_IN}.",
            DeprecationWarning,
            stacklevel=2,
        )
    return copied


class _AliasLoader(importlib.abc.Loader):
    """Hands the import system the target module itself, then puts its own spec back.

    ``module_from_spec`` overwrites ``__spec__`` on whatever ``create_module`` returns,
    so without the restore the target would carry the alias name in its spec.
    """

    def __init__(self, target: ModuleType) -> None:
        self._target = target
        self._spec = getattr(target, "__spec__", None)

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        return self._target

    def exec_module(self, module: ModuleType) -> None:
        if self._spec is not None:
            module.__spec__ = self._spec


class _AliasFinder(importlib.abc.MetaPathFinder):
    """``<old>.x.y`` imports resolve to the already-imported ``<new>.x.y`` module.

    The target is imported here but **not** registered under the old name here: if it
    were, ``_find_spec`` would take it for a module the parent import already loaded,
    use the target's real spec, and execute the file a second time.
    """

    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if fullname != self.old and not fullname.startswith(self.old + "."):
            return None
        module = importlib.import_module(self.new + fullname[len(self.old) :])
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(module), is_package=hasattr(module, "__path__")
        )


def install_import_alias(old: str, new: str) -> None:
    """Make ``old`` and its submodules import as ``new``; warn once per old name."""
    warnings.warn(
        f"`{old}` is now `{new}`; change the import. The `{old}` name is removed in {REMOVED_IN}.",
        DeprecationWarning,
        stacklevel=3,
    )
    if not any(isinstance(f, _AliasFinder) and f.old == old for f in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder(old, new))
    target = importlib.import_module(new)
    shim = sys.modules.get(old)
    if shim is not None and shim is not target:
        # The shim package's own module object is what `import old` returned; make its
        # attributes those of the target so `old.sdk` works without a submodule import.
        shim.__dict__.update({k: v for k, v in target.__dict__.items() if not k.startswith("__")})
        # The shim keeps its own (empty) __path__ on purpose: with the target's path the
        # regular PathFinder would load `old.x` a second time from the target's files.


def legacy_fallback_dir(new: Path, old: Path, *, what: str) -> Path:
    """``old`` when it exists and ``new`` does not, with one warning per location; else ``new``."""
    if new.exists() or not old.exists():
        return new
    key = str(old)
    if key not in _dir_warned:
        _dir_warned.add(key)
        warnings.warn(
            f"{what} found at {old} (the 0.1.x location); Evarness reads it from there until "
            f"{new} exists. Move it: `mv {old} {new}`. The fallback is removed in {REMOVED_IN}.",
            DeprecationWarning,
            stacklevel=3,
        )
    return old


__all__ = [
    "ENV_PREFIX",
    "LEGACY_ENV_PREFIX",
    "REMOVED_IN",
    "alias_legacy_environment",
    "install_import_alias",
    "legacy_fallback_dir",
]
