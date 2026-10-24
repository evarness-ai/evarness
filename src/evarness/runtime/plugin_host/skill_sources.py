"""Which installed plugins may contribute skill packs (ADR-0136).

A skill pack's ``tools.py`` is code that is imported into the harness process, so a plugin
contributes loadable packs only when the host would mount it AND it runs in-process. A
``trust: mcp`` plugin runs as its own server, out of process by design: its packs are
listed, never imported (importing them would cross the isolation that trust level exists
for). Two views of "mounted", for the two kinds of caller:

* :func:`mounted_skill_sources` -- the live runtime: the plugin registry's own records,
  after the profile was resolved and each plugin set up. Nothing here scans what is
  installed, so ``SkillRegistry.discover()`` (which runs per chat turn) stays cheap.
* :func:`profile_skill_sources` -- a caller that does not boot a runtime (``evarness skills
  list``, ``evarness mcp serve``): the resolved profile, which already folds in the home
  overlay and ``EVARNESS_PLUGINS_DISABLE``/``ENABLE``, then the loader's own pre-setup
  checks (the plugin is found, its ``requires`` are met) and the effective trust. A plugin
  that would fail inside ``setup`` is only known to a booted host. Installed plugins
  outside the profile are returned unmounted, so they are listed and never imported.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from evarness.foundation.plugin_dirs import (
    MANIFEST_FILENAME,
    evarness_home,
    installed_plugin_dirs,
)
from evarness.tools.skills.loader import PluginSkillSource

from .loader import _requirements_problem, discover_plugin
from .profile import PluginRef, load_profile
from .registry import MOUNTED, PluginRecord, PluginRegistry

MCP_REASON = "trust: mcp, packs run in-process"


def mounted_skill_sources(registry: PluginRegistry) -> Callable[[], list[PluginSkillSource]]:
    """A provider over the live plugin registry: mounted plugins, by their own records."""
    # A plugin the host did not set up (disabled) has no directory on its record, because
    # loading stops before discovery. Where it lives does not change while the process
    # runs, so look it up once, not on every turn's ``discover()``.
    located: dict[str, Path | None] = {}

    def directory_of(record: PluginRecord) -> Path | None:
        if record.directory is not None:
            return record.directory
        if record.name not in located:
            try:
                source = discover_plugin(record.name)
            except Exception:  # noqa: BLE001 -- a plugin that cannot be located has no packs
                source = None
            located[record.name] = source.directory if source is not None else None
        return located[record.name]

    def sources() -> list[PluginSkillSource]:
        found: list[PluginSkillSource] = []
        for record in registry.plugins():
            directory = directory_of(record)
            # A bare-module entry point resolves to its parent (site-packages): not a
            # plugin directory, so it has no packs to look in.
            if directory is None or not (directory / MANIFEST_FILENAME).is_file():
                continue
            if record.status not in MOUNTED:
                mounted, reason = False, registry.unmounted_reason(record.name)
            elif record.trust != "in-process":
                mounted, reason = False, MCP_REASON
            else:
                mounted, reason = True, None
            found.append(PluginSkillSource(record.name, directory, mounted, reason))
        return found

    return sources


def _profile_verdict(ref: PluginRef | None, name: str, home: Path) -> str | None:
    """Why the plugin ``name`` would not contribute packs from a profile, else ``None``."""
    if ref is None:
        return "plugin not mounted (not in the resolved profile)"
    try:
        source = discover_plugin(name, home_dir=home)
    except Exception as exc:  # noqa: BLE001 -- a broken manifest is "would not mount"
        return f"plugin would not mount: {exc}"
    if source is None:
        return "plugin would not mount: not found"
    problem = _requirements_problem(source.manifest)
    if problem is not None:
        return f"plugin would not mount: {problem}"
    if (ref.trust or source.manifest.trust) != "in-process":
        return MCP_REASON
    return None


def profile_skill_sources(config_dir: Path) -> Callable[[], list[PluginSkillSource]]:
    """A provider over the resolved profile, for callers that do not boot a runtime."""

    def sources() -> list[PluginSkillSource]:
        home = evarness_home()
        refs = {ref.name: ref for ref in load_profile(config_dir).enabled_plugins()}
        found: list[PluginSkillSource] = []
        for name, directory in installed_plugin_dirs():
            reason = _profile_verdict(refs.get(name), name, home)
            found.append(PluginSkillSource(name, directory, reason is None, reason))
        return found

    return sources
