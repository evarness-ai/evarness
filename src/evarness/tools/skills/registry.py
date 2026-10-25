"""Registry for loading and filtering code-first skill packages."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from langchain_core.tools import BaseTool

from .loader import (
    PluginSkill,
    PluginSkillSource,
    discover_config_skill_dirs,
    discover_plugin_skill_dirs,
    load_skill_manifest,
    load_skill_package,
)
from .models import SkillPackage

logger = logging.getLogger(__name__)


CONFIG_SOURCE = "config/skills"


class UnmountedPack(NamedTuple):
    """A pack of an installed plugin that is not mounted, read from its manifest only."""

    name: str
    plugin: str
    skill_dir: Path
    reason: str
    kind: str = "unknown"


class SkillRegistry:
    """Discover and index skill packages available in the repository."""

    def __init__(
        self,
        repo_root: Path,
        *,
        environment: Mapping[str, str] | None = None,
        plugin_sources: Callable[[], Iterable[PluginSkillSource]] | None = None,
    ) -> None:
        # Where the plugins' packs come from (ADR-0136). None = no plugin packs: the
        # registry never scans what is installed, because a pack's tools.py is code, and
        # only a plugin the host has mounted may run code here.
        self._plugin_sources = plugin_sources
        self.repo_root = Path(repo_root).resolve()
        # None = the process environment, read at each discover() (a hot reload sees a
        # variable set since startup); a mapping is what the caller says the environment is.
        self.environment = None if environment is None else dict(environment)
        self._packages: tuple[SkillPackage, ...] = ()
        self._load_failures: dict[Path, str] = {}
        self._reported_failures: set[tuple[Path, str]] = set()
        self._reported_unavailable: set[tuple[str, tuple[str, ...]]] = set()
        self._reported_shadowed: set[tuple[Path, Path]] = set()
        self._sources: dict[Path, str] = {}
        self._unmounted: tuple[UnmountedPack, ...] = ()

    def bind_plugin_sources(
        self, plugin_sources: Callable[[], Iterable[PluginSkillSource]]
    ) -> None:
        """Say where the plugins' packs come from; the next :meth:`discover` uses it.

        The runtime binds this once its plugin host has mounted the profile, after the
        registry was built.
        """
        self._plugin_sources = plugin_sources

    @property
    def unmounted(self) -> tuple[UnmountedPack, ...]:
        """Packs of installed plugins that are not mounted: listed, never imported."""
        return self._unmounted

    @property
    def load_failures(self) -> dict[Path, str]:
        """Skills the last :meth:`discover` skipped, as ``skill_dir -> one-line reason``."""
        return dict(self._load_failures)

    def discover(self) -> tuple[SkillPackage, ...]:
        """Discover and load all skill packages: ``config/skills`` and installed plugins' packs.

        One skill that cannot load (invalid manifest, a tools module whose import
        fails) is skipped and recorded in :attr:`load_failures`; it never aborts the
        pass, so the skills after it still load. A skill whose *declared* prerequisites
        are missing is not a failure: it loads as a blocked package
        (``missing_prerequisites``) without its tools module being imported.
        """
        packages: list[SkillPackage] = []
        failures: dict[Path, str] = {}
        taken: dict[str, Path] = {}
        sources: dict[Path, str] = {}
        unmounted: list[UnmountedPack] = []
        plugin_packs = discover_plugin_skill_dirs(
            self._plugin_sources() if self._plugin_sources is not None else ()
        )
        # config/skills first: among plugin packs the registry keeps the first of a name,
        # and a pack in config/skills overrides a plugin's (ADR-0136). Two packs inside
        # config/skills both load, as they always did.
        ordered: list[tuple[Path, str, Path | None]] = [
            (skill_dir, CONFIG_SOURCE, None)
            for skill_dir in discover_config_skill_dirs(self.repo_root)
        ]
        for pack in plugin_packs:
            if pack.mounted:
                ordered.append((pack.skill_dir, f"plugin:{pack.plugin}", pack.plugin_dir))
            else:
                unmounted.append(self._unmounted_pack(pack))
        for skill_dir, source, plugin_dir in ordered:
            sources[skill_dir] = source
            try:
                # Decided on the manifest alone, and the name is held from the moment it
                # reads: a displaced pack's tools module is never imported, and a
                # config pack that then fails to load still keeps its name.
                name = load_skill_manifest(skill_dir).name
                first = taken.get(name)
                if plugin_dir is not None and first is not None:
                    self._note_shadowed(name, skill_dir, first)
                    continue
                taken.setdefault(name, skill_dir)
                packages.append(
                    load_skill_package(
                        self.repo_root,
                        skill_dir,
                        environment=self.environment,
                        plugin_dir=plugin_dir,
                    )
                )
            except Exception as exc:  # isolate one skill's fault from the rest
                reason = _one_line_reason(exc)
                failures[skill_dir] = reason
                # discover() also runs per chat turn and on hot reload: say it once. A
                # skill that is genuinely broken (a tools.py that raises, a bad manifest,
                # an import its manifest does not declare) is a fault, so it is logged
                # with its traceback; ``load_failures`` carries only the one-line reason.
                if (skill_dir, reason) not in self._reported_failures:
                    self._reported_failures.add((skill_dir, reason))
                    logger.exception("skill %s failed to load; skipping it", skill_dir.name)
            else:
                self._note_unavailable(packages[-1])
        self._packages = tuple(packages)
        self._load_failures = failures
        self._sources = sources
        self._unmounted = tuple(unmounted)
        return self._packages

    @staticmethod
    def _unmounted_pack(pack: PluginSkill) -> UnmountedPack:
        """Describe a pack of a plugin that is not mounted, from its manifest alone."""
        try:
            manifest = load_skill_manifest(pack.skill_dir)
            name, kind = manifest.name, manifest.kind or "skill"
        except Exception:  # noqa: BLE001 -- a bad manifest is still a row, never an abort
            name, kind = pack.skill_dir.name, "unknown"
        return UnmountedPack(
            name=name,
            plugin=pack.plugin,
            skill_dir=pack.skill_dir,
            reason=pack.reason or "plugin not mounted",
            kind=kind,
        )

    def source_of(self, skill_dir: Path) -> str:
        """Where the last :meth:`discover` found a pack: ``config/skills`` or ``plugin:<name>``."""
        return self._sources.get(skill_dir.resolve(), CONFIG_SOURCE)

    def _note_shadowed(self, name: str, skill_dir: Path, winner: Path) -> None:
        """One INFO line for a pack another pack of the same name displaces.

        ``config/skills`` is discovered before the packs plugins ship, so an owner's copy
        overrides a plugin's (ADR-0136). Said once: ``discover()`` re-runs per turn.
        """
        key = (skill_dir, winner)
        if key in self._reported_shadowed:
            return
        self._reported_shadowed.add(key)
        logger.info(
            "skill %s at %s is shadowed by %s and not loaded",
            name,
            skill_dir,
            winner,
        )

    def _note_unavailable(self, package: SkillPackage) -> None:
        """One INFO line for a skill that a missing declared prerequisite leaves blocked.

        Not a fault: the skill declared what it needs (a package, an env var, a config
        file, a credential, a Python version) and this install lacks it. Said once
        (``discover()`` re-runs per turn), naming what is missing, never a value; a missing
        package also names the extra that installs it. The same items, spelled the same,
        fill ``blocked:`` in ``evarness skills list``.
        """
        missing = package.missing_prerequisites
        key = (package.manifest.name, missing)
        if not missing or key in self._reported_unavailable:
            return
        self._reported_unavailable.add(key)
        logger.info(
            "skill %s unavailable: missing %s%s",
            package.manifest.name,
            ", ".join(_describe_missing(item) for item in missing),
            _install_hint(package),
        )

    def unavailable(self) -> list[tuple[str, str, str | None]]:
        """Skills the last discovery left blocked: ``(name, what is missing, fix or None)``.

        The same items, spelled the same, as the one INFO line and ``evarness skills list``'s
        ``blocked:``; Health reads this so a skill that cannot run is a row, not only a log.
        """
        return [
            (
                package.manifest.name,
                ", ".join(_describe_missing(item) for item in package.missing_prerequisites),
                _install_hint(package).removeprefix("; install it with: ") or None,
            )
            for package in self._packages
            if package.missing_prerequisites
        ]

    def list_packages(
        self,
        *,
        agent_name: str | None = None,
        only_loadable: bool = False,
    ) -> tuple[SkillPackage, ...]:
        """List discovered packages filtered by agent and loadability."""
        packages = self._packages
        if agent_name is None and not only_loadable:
            return packages

        normalized_agent = agent_name.strip() if isinstance(agent_name, str) else None
        filtered: list[SkillPackage] = []
        for package in packages:
            if only_loadable and not package.is_loadable:
                continue
            if (
                normalized_agent is not None
                and normalized_agent not in package.manifest.requires.agents
            ):
                continue
            filtered.append(package)
        return tuple(filtered)

    def list_tool_classes(self, *, agent_name: str | None = None) -> dict[str, type[BaseTool]]:
        """Return loadable tool classes indexed by manifest tool name."""
        indexed: dict[str, type[BaseTool]] = {}
        for package in self.list_packages(agent_name=agent_name, only_loadable=True):
            for tool_manifest, tool_class in zip(
                package.manifest.tools, package.tool_classes, strict=False
            ):
                indexed[tool_manifest.name] = tool_class
        return indexed


def _install_hint(package: SkillPackage) -> str:
    """``; install it with: pip install '...'`` when an extra supplies the missing package."""
    extra = package.manifest.requires.extra
    has_package = any(item.startswith("package:") for item in package.missing_prerequisites)
    return f"; install it with: pip install 'evarness[{extra}]'" if extra and has_package else ""


def _describe_missing(item: str) -> str:
    """``package:foo`` -> ``package foo``: the prerequisite kind, then its name."""
    kind, _, name = item.partition(":")
    return f"{kind} {name}"


def _one_line_reason(exc: BaseException) -> str:
    """``ExcType: first line of message`` -- no traceback, no multi-line payload."""
    message = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


# Backwards-compatible alias used by earlier drafts and generated code.
SkillsRegistry = SkillRegistry
