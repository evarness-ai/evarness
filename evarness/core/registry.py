"""Generic registries and domain hooks — how anything extends the kernel.

The kernel never imports a domain. Instead it defines named tables and hook
points here; a domain (or a pip-installed plugin) registers into them at
import time. Three mechanisms, in resolution order:

1. **In-tree domains** — ``evarness/__init__`` imports the packaged domains,
   which register their node types, providers, and inspectors.
2. **Entry points** — any installed distribution can declare
   ``[project.entry-points."evarness.plugins"] name = "pkg.module:setup"``;
   ``load_entry_point_plugins()`` calls each ``setup()`` once.
3. **Local plugins** — domain-specific loaders (e.g. ``~/.evarness/plugins/``)
   may layer on top; they belong to the domain, not to core.

Unknown names are :class:`~evarness.core.errors.RegistryError` with the known
names listed — misconfiguration is loud, never a silent fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import metadata
from typing import Any, Callable, Iterator

from evarness.core.errors import RegistryError


class Registry(Mapping):
    """A named table with decorator registration and loud lookups.

    A real :class:`Mapping`: ``in``, ``[]``, ``.get``, iteration, and ``len``
    all behave like a dict, so registry-consuming code reads naturally and
    accepts a plain dict in tests."""

    def __init__(self, kind: str):
        self.kind = kind
        self._table: dict[str, Any] = {}

    def register(self, name: str, obj: Any = None):
        """``register("x", obj)`` or ``@register("x")`` as a decorator."""
        if obj is None:

            def deco(o):
                self._table[name] = o
                return o

            return deco
        self._table[name] = obj
        return obj

    def get(self, name: str, default: Any = None) -> Any:
        """Dict semantics: returns ``default`` when absent. Use :meth:`require`
        (or ``registry[name]``) when absence is an error."""
        return self._table.get(name, default)

    def require(self, name: str) -> Any:
        try:
            return self._table[name]
        except KeyError:
            raise RegistryError(
                f"unknown {self.kind} '{name}' — registered: {', '.join(sorted(self._table))}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._table)

    def __contains__(self, name: object) -> bool:
        return name in self._table

    def __getitem__(self, name: str) -> Any:
        return self.require(name)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._table))

    def items(self):
        return self._table.items()

    def values(self):
        return self._table.values()

    def __len__(self) -> int:
        return len(self._table)


# ------------------------------------------------------------------ node types

#: type name -> NodeSpec subclass. Domains fill this; the executor and lint
#: read it. Dict-compatible so ``lint(graph, NODE_TYPES)`` reads naturally.
NODE_TYPES = Registry("node type")


def register_node(cls):
    """Class decorator used by domain node modules: registers by ``type_name``."""
    NODE_TYPES.register(cls.type_name, cls)
    return cls


# ------------------------------------------------------------ domain hooks

#: graph -> True when something in it breaks the determinism contract
#: (e.g. a real-mode tool). The executor ANDs "provider is deterministic"
#: with "no inspector objects". Domains append here.
DETERMINISM_INSPECTORS: list[Callable[[Any], bool]] = []


def register_determinism_inspector(fn: Callable[[Any], bool]) -> Callable[[Any], bool]:
    DETERMINISM_INSPECTORS.append(fn)
    return fn


_provider_factory: Callable[..., Any] | None = None


def set_provider_factory(fn: Callable[..., Any]) -> None:
    """The domain supplies how ``graph.params.provider`` specs become provider
    objects. Core refuses to guess."""
    global _provider_factory
    _provider_factory = fn


def make_provider(spec: str, environment: Any = None) -> Any:
    if _provider_factory is None:
        raise RegistryError(
            "no provider factory registered — import a domain (e.g. "
            "evarness.domains.agents) before executing graphs"
        )
    return _provider_factory(spec, environment)


_environment_loader: Callable[..., Any] | None = None


def set_environment_loader(fn: Callable[..., Any]) -> None:
    """The domain supplies how scenario documents become Environment objects
    (the agents domain: YAML fixture -> Fixture)."""
    global _environment_loader
    _environment_loader = fn


def load_environment(source: Any) -> Any:
    if _environment_loader is None:
        raise RegistryError(
            "no environment loader registered — import a domain (e.g. "
            "evarness.domains.agents) before proving"
        )
    return _environment_loader(source)


#: graph -> {bundle_subject_key: value} — extra artifacts a domain pins into a
#: proof's subject (the agents domain pins tool-manifest hashes). Merged in
#: registration order; a domain must not reuse another's keys.
SUBJECT_PINNERS: list[Callable[[Any], dict]] = []


def register_subject_pinner(fn: Callable[[Any], dict]) -> Callable[[Any], dict]:
    SUBJECT_PINNERS.append(fn)
    return fn


# ----------------------------------------------------------- contract sources

#: packaged contract-library YAML paths, in resolution order after any
#: pattern-local and user-overlay definitions. Domains append theirs.
CONTRACT_SOURCES: list[Any] = []


def register_contract_source(path: Any) -> None:
    CONTRACT_SOURCES.append(path)


# ------------------------------------------------------------- entry points

_plugins_loaded = False


def load_entry_point_plugins(group: str = "evarness.plugins") -> list[str]:
    """Call every installed plugin's ``setup()`` once. Returns the names
    loaded; a plugin that raises is skipped loudly via a RegistryError."""
    global _plugins_loaded
    if _plugins_loaded:
        return []
    _plugins_loaded = True
    loaded = []
    for ep in metadata.entry_points(group=group):
        try:
            setup = ep.load()
            setup()
            loaded.append(ep.name)
        except Exception as exc:  # a broken plugin must not brick the host
            raise RegistryError(f"plugin '{ep.name}' failed to load: {exc}") from exc
    return loaded
