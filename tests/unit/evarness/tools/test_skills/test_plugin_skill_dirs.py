"""Skill packs a *mounted* plugin ships load with no config; an unmounted one's never run (ADR-0136)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from evarness.runtime.plugin_host.registry import PluginRecord, PluginRegistry, PluginStatus
from evarness.runtime.plugin_host.skill_sources import (
    mounted_skill_sources,
    profile_skill_sources,
)
from evarness.tools.skills.loader import (
    PluginSkillSource,
    discover_plugin_skill_dirs,
    discover_skill_dirs,
)
from evarness.tools.skills.registry import SkillRegistry


def _manifest(name: str, *, marker: str = "", config: list[str] | None = None) -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "description": f"Test pack {marker}",
        "author": "evarness-tests",
        "license": "Apache-2.0",
        "kind": "brief",
        "requires": {"config_files": config or []},
        "brief": {
            "subject": "Test",
            "recipient": "user",
            "uses": [],
            "layout": "hello {{greeting}}",
            "slots": {"greeting": {"kind": "literal", "value": "world"}},
        },
    }


def _write_brief_pack(
    directory: Path, name: str, *, marker: str = "", config: list[str] | None = None
) -> Path:
    """A brief pack: a manifest only, so a pack's tools module is never imported."""
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(_manifest(name, marker=marker, config=config)), encoding="utf-8"
    )
    return directory


def _write_tool_pack(directory: Path, name: str, imported: Path) -> Path:
    """A tool pack whose tools module leaves ``imported`` behind when it is imported."""
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "1.0.0",
                "description": "Tool pack",
                "author": "evarness-tests",
                "license": "Apache-2.0",
                "tools": [
                    {"name": f"{name}_tool", "description": "t", "governor_route": "system/read"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (directory / "tools.py").write_text(
        "from pathlib import Path\n"
        "from langchain_core.tools import BaseTool\n"
        "from pydantic import BaseModel\n\n"
        f"Path({str(imported)!r}).write_text('imported')\n\n"
        "class Args(BaseModel):\n    pass\n\n"
        "class T(BaseTool):\n"
        f"    name: str = '{name}_tool'\n"
        "    description: str = 't'\n"
        "    args_schema: type[BaseModel] = Args\n"
        "    def _run(self) -> str:\n        return 'ok'\n"
        "    async def _arun(self) -> str:\n        return 'ok'\n\n"
        "SKILL_TOOLS = [T]\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "site" / "acme"
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text("name: acme\n", encoding="utf-8")
    return directory


def _mounted(plugin_dir: Path):
    return lambda: [PluginSkillSource("acme", plugin_dir)]


def _unmounted(plugin_dir: Path):
    return lambda: [PluginSkillSource("acme", plugin_dir, False, "plugin not mounted")]


def test_a_pack_inside_a_mounted_plugin_is_discovered(tmp_path: Path, plugin_dir: Path) -> None:
    pack = _write_brief_pack(plugin_dir / "skills" / "nested" / "acme-brief", "acme-brief")
    sources = [PluginSkillSource("acme", plugin_dir)]

    assert [(p.plugin, p.skill_dir) for p in discover_plugin_skill_dirs(sources)] == [
        ("acme", pack.resolve())
    ]
    assert discover_skill_dirs(tmp_path / "repo", sources) == (pack.resolve(),)
    names = [
        p.manifest.name
        for p in SkillRegistry(tmp_path / "repo", plugin_sources=_mounted(plugin_dir)).discover()
    ]
    assert names == ["acme-brief"]


def test_the_registry_scans_no_plugin_unless_it_is_told_which(
    tmp_path: Path, plugin_dir: Path
) -> None:
    _write_brief_pack(plugin_dir / "skills" / "acme-brief", "acme-brief")
    registry = SkillRegistry(tmp_path / "repo")  # no plugin_sources
    assert registry.discover() == ()
    assert registry.unmounted == ()


def test_an_unmounted_plugins_pack_is_listed_and_its_code_never_runs(
    tmp_path: Path, plugin_dir: Path
) -> None:
    imported = tmp_path / "imported.marker"
    _write_tool_pack(plugin_dir / "skills" / "acme-tool", "acme-tool", imported)

    registry = SkillRegistry(tmp_path / "repo", plugin_sources=_unmounted(plugin_dir))
    assert registry.discover() == ()
    assert not imported.exists()
    (pack,) = registry.unmounted
    assert (pack.name, pack.plugin, pack.reason) == ("acme-tool", "acme", "plugin not mounted")
    # The same pack, once its plugin is mounted, does load and does import.
    mounted = SkillRegistry(tmp_path / "repo", plugin_sources=_mounted(plugin_dir))
    assert [p.manifest.name for p in mounted.discover()] == ["acme-tool"]
    assert imported.exists()


def test_the_runtimes_view_follows_the_plugin_hosts_records(tmp_path: Path) -> None:
    registry = PluginRegistry()
    dirs = {}
    for name, status in (
        ("up", PluginStatus.LOADED),
        ("hurt", PluginStatus.DEGRADED),
        ("off", PluginStatus.DISABLED),
        ("broken", PluginStatus.FAILED),
    ):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "manifest.yaml").write_text(f"name: {name}\n", encoding="utf-8")
        dirs[name] = directory
        registry.add_plugin(
            PluginRecord(
                name=name, source="home", status=status, directory=directory, load_error=None
            )
        )
    registry.add_plugin(PluginRecord(name="nodir", source="profile", status=PluginStatus.FAILED))

    by_name = {s.plugin: s for s in mounted_skill_sources(registry)()}
    assert set(by_name) == {"up", "hurt", "off", "broken"}  # no directory, no sources
    assert {name: s.mounted for name, s in by_name.items()} == {
        "up": True,
        "hurt": True,
        "off": False,
        "broken": False,
    }
    assert by_name["off"].reason == "disabled"


def test_a_home_plugin_outside_the_profile_is_never_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    imported = tmp_path / "imported.marker"
    plugin = home / "plugins" / "acme"
    plugin.mkdir(parents=True)
    (plugin / "manifest.yaml").write_text("name: acme\n", encoding="utf-8")
    _write_tool_pack(plugin / "skills" / "acme-tool", "acme-tool", imported)
    monkeypatch.setenv("EVARNESS_HOME", str(home))
    monkeypatch.delenv("EVARNESS_PLUGINS_DISABLE", raising=False)
    monkeypatch.delenv("EVARNESS_PLUGINS_ENABLE", raising=False)
    monkeypatch.delenv("EVARNESS_PROFILE", raising=False)
    config = tmp_path / "config"
    config.mkdir()

    # Dropped into the home but listed by no profile: installed, not mounted.
    registry = SkillRegistry(tmp_path / "repo", plugin_sources=profile_skill_sources(config))
    assert registry.discover() == ()
    assert not imported.exists()
    assert [p.name for p in registry.unmounted] == ["acme-tool"]

    # Listed in the home overlay: mounted, so its pack loads.
    (home / "profile.yaml").write_text(
        yaml.safe_dump({"plugins": [{"name": "acme"}]}), encoding="utf-8"
    )
    assert [p.manifest.name for p in registry.discover()] == ["acme-tool"]
    assert imported.exists()

    # Switched off by the environment: unmounted again, even though the profile lists it.
    imported.unlink()
    monkeypatch.setenv("EVARNESS_PLUGINS_DISABLE", "acme")
    assert registry.discover() == ()
    assert not imported.exists()
    assert [p.name for p in registry.unmounted] == ["acme-tool"]


def test_a_mounted_mcp_trust_plugins_packs_are_listed_and_never_imported(
    tmp_path: Path,
) -> None:
    """A ``trust: mcp`` plugin runs out of process; its pack code must not enter this one."""
    plugin = tmp_path / "mcpplug"
    plugin.mkdir()
    (plugin / "manifest.yaml").write_text("name: mcpplug\n", encoding="utf-8")
    imported = tmp_path / "imported.marker"
    _write_tool_pack(plugin / "skills" / "mcp-tool", "mcp-tool", imported)
    plugins = PluginRegistry()
    plugins.add_plugin(
        PluginRecord(
            name="mcpplug",
            source="home",
            status=PluginStatus.LOADED,
            trust="mcp",
            directory=plugin,
        )
    )

    registry = SkillRegistry(tmp_path / "repo", plugin_sources=mounted_skill_sources(plugins))
    assert registry.discover() == ()
    assert not imported.exists()
    (pack,) = registry.unmounted
    assert (pack.name, pack.kind, pack.reason) == (
        "mcp-tool",
        "skill",
        "trust: mcp, packs run in-process",
    )


def _home_plugin(home: Path, name: str, imported: Path, manifest: dict | None = None) -> Path:
    plugin = home / "plugins" / name
    plugin.mkdir(parents=True)
    (plugin / "manifest.yaml").write_text(
        yaml.safe_dump({"name": name, **(manifest or {})}), encoding="utf-8"
    )
    _write_tool_pack(plugin / "skills" / f"{name}-tool", f"{name}-tool", imported)
    return plugin


def test_a_profile_plugin_the_host_would_not_mount_loads_no_pack_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI and ``mcp serve`` path applies the loader's own checks, not just the profile."""
    home = tmp_path / "home"
    marks = {n: tmp_path / f"{n}.marker" for n in ("unmet", "override", "fine")}
    _home_plugin(home, "unmet", marks["unmet"], {"requires": {"packages": ["no-such-package-xyz"]}})
    _home_plugin(home, "override", marks["override"])
    _home_plugin(home, "fine", marks["fine"])
    (home / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "plugins": [
                    {"name": "unmet"},
                    {"name": "override", "trust": "mcp"},  # the profile makes it out-of-process
                    {"name": "fine"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVARNESS_HOME", str(home))
    for var in ("EVARNESS_PLUGINS_DISABLE", "EVARNESS_PLUGINS_ENABLE", "EVARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    config = tmp_path / "config"
    config.mkdir()

    registry = SkillRegistry(tmp_path / "repo", plugin_sources=profile_skill_sources(config))
    assert [p.manifest.name for p in registry.discover()] == ["fine-tool"]
    assert marks["fine"].exists()
    assert not marks["unmet"].exists() and not marks["override"].exists()
    reasons = {p.plugin: p.reason for p in registry.unmounted}
    assert reasons["unmet"].startswith("plugin would not mount: required package not installed")
    assert reasons["override"] == "trust: mcp, packs run in-process"


def test_a_plugin_without_a_skills_directory_adds_nothing(tmp_path: Path, plugin_dir: Path) -> None:
    registry = SkillRegistry(tmp_path / "repo", plugin_sources=_mounted(plugin_dir))
    assert registry.discover() == ()
    assert registry.unmounted == ()


def test_config_skills_win_a_name_collision_and_it_is_logged_once(
    tmp_path: Path, plugin_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = tmp_path / "repo"
    owner = _write_brief_pack(repo / "config" / "skills" / "acme-brief", "acme-brief", marker="own")
    _write_brief_pack(plugin_dir / "skills" / "acme-brief", "acme-brief", marker="plugin")
    _write_brief_pack(plugin_dir / "skills" / "acme-extra", "acme-extra")

    registry = SkillRegistry(repo, plugin_sources=_mounted(plugin_dir))
    with caplog.at_level(logging.INFO, logger="evarness.tools.skills.registry"):
        first = registry.discover()
        registry.discover()  # per chat turn and on hot reload: said once

    by_name = {p.manifest.name: p for p in first}
    assert set(by_name) == {"acme-brief", "acme-extra"}
    assert by_name["acme-brief"].skill_dir == owner.resolve()
    assert len([r for r in caplog.records if "shadowed" in r.getMessage()]) == 1


def test_a_config_pack_that_fails_to_load_still_keeps_its_name(
    tmp_path: Path, plugin_dir: Path
) -> None:
    """A broken owner copy must not let the plugin's pack win silently."""
    repo = tmp_path / "repo"
    broken = _write_brief_pack(
        repo / "config" / "skills" / "acme-brief", "acme-brief", config=["~/never"]
    )
    (broken / "tools.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    manifest = yaml.safe_load((broken / "manifest.yaml").read_text(encoding="utf-8"))
    manifest.pop("brief")
    manifest["kind"] = "skill"
    manifest["requires"] = {}
    manifest["tools"] = [{"name": "t", "description": "t", "governor_route": "system/read"}]
    (broken / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    _write_brief_pack(plugin_dir / "skills" / "acme-brief", "acme-brief", marker="plugin")

    registry = SkillRegistry(repo, plugin_sources=_mounted(plugin_dir))
    assert registry.discover() == ()  # the owner's failed; the plugin's did not take over
    assert list(registry.load_failures) == [broken.resolve()]


def test_two_packs_of_one_name_inside_config_skills_both_load(tmp_path: Path) -> None:
    """The collision rule is scoped to plugin packs: config/skills behaves as it always did."""
    repo = tmp_path / "repo"
    _write_brief_pack(repo / "config" / "skills" / "a" / "dup", "dup", marker="a")
    _write_brief_pack(repo / "config" / "skills" / "b" / "dup", "dup", marker="b")
    assert [p.manifest.name for p in SkillRegistry(repo).discover()] == ["dup", "dup"]


def test_a_broken_plugin_pack_is_a_load_failure_not_an_abort(
    tmp_path: Path, plugin_dir: Path
) -> None:
    _write_brief_pack(plugin_dir / "skills" / "good", "good")
    bad = plugin_dir / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "manifest.yaml").write_text("- not a mapping\n", encoding="utf-8")

    registry = SkillRegistry(tmp_path / "repo", plugin_sources=_mounted(plugin_dir))
    assert [p.manifest.name for p in registry.discover()] == ["good"]
    assert list(registry.load_failures) == [bad.resolve()]


def test_a_plugin_pack_resolves_config_files_against_its_plugin_dir(
    tmp_path: Path, plugin_dir: Path
) -> None:
    """The config a plugin's pack needs is the plugin's own package data (ADR-0136)."""
    repo = tmp_path / "repo"
    _write_brief_pack(
        plugin_dir / "skills" / "needs-file", "needs-file", config=["defaults/x.yaml"]
    )
    _write_brief_pack(
        plugin_dir / "skills" / "needs-more", "needs-more", config=["defaults/y.yaml"]
    )
    (plugin_dir / "defaults").mkdir()
    (plugin_dir / "defaults" / "x.yaml").write_text("a: 1\n", encoding="utf-8")
    # The same name under the checkout's config/ must not satisfy a plugin pack.
    (repo / "config" / "defaults").mkdir(parents=True)
    (repo / "config" / "defaults" / "y.yaml").write_text("a: 1\n", encoding="utf-8")

    by_name = {
        p.manifest.name: p
        for p in SkillRegistry(repo, plugin_sources=_mounted(plugin_dir)).discover()
    }
    assert by_name["needs-file"].missing_prerequisites == ()
    assert by_name["needs-more"].missing_prerequisites == ("config:defaults/y.yaml",)


def test_a_config_pack_keeps_the_checkout_rule(tmp_path: Path, plugin_dir: Path) -> None:
    repo = tmp_path / "repo"
    _write_brief_pack(repo / "config" / "skills" / "own", "own", config=["defaults/x.yaml"])
    (plugin_dir / "defaults").mkdir()
    (plugin_dir / "defaults" / "x.yaml").write_text("a: 1\n", encoding="utf-8")  # the plugin's

    (pack,) = SkillRegistry(repo, plugin_sources=_mounted(plugin_dir)).discover()
    assert pack.missing_prerequisites == ("config:defaults/x.yaml",)


def test_skills_list_shows_mounted_and_unmounted_packs_through_the_real_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evarness.main import app

    home = tmp_path / "home"
    for name in ("acme", "idle"):
        plugin = home / "plugins" / name
        plugin.mkdir(parents=True)
        (plugin / "manifest.yaml").write_text(f"name: {name}\n", encoding="utf-8")
        _write_brief_pack(plugin / "skills" / f"{name}-brief", f"{name}-brief")
    (home / "profile.yaml").write_text(
        yaml.safe_dump({"plugins": [{"name": "acme"}]}), encoding="utf-8"
    )
    repo = tmp_path / "repo"
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("EVARNESS_HOME", str(home))
    monkeypatch.setenv("EVARNESS_CONFIG_DIR", str(config))
    for var in ("EVARNESS_PLUGINS_DISABLE", "EVARNESS_PLUGINS_ENABLE", "EVARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("evarness.cli.skills.config_root", lambda: repo)

    result = CliRunner().invoke(app, ["skills", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = {row["name"]: row for row in json.loads(result.output)}
    assert rows["acme-brief"]["source"] == "plugin:acme"
    assert rows["acme-brief"]["loadable"] is True
    assert rows["acme-brief"]["missing_prerequisites"] == []
    assert rows["idle-brief"]["source"] == "plugin:idle"
    assert rows["idle-brief"]["loadable"] is False
    assert rows["idle-brief"]["not_loaded"].startswith("plugin not mounted")
