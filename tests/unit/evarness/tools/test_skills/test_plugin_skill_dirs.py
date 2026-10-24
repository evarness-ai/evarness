"""Skill packs an installed plugin ships are discovered with no config (ADR-0136)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from evarness.cli.commands import _list_skill_entries
from evarness.tools.skills.loader import discover_plugin_skill_dirs, discover_skill_dirs
from evarness.tools.skills.registry import SkillRegistry


def _write_brief_pack(directory: Path, name: str, *, marker: str = "") -> Path:
    """A brief pack: a manifest only, so a pack's tools module is never imported."""
    directory.mkdir(parents=True)
    payload = {
        "name": name,
        "version": "1.0.0",
        "description": f"Test pack {marker}",
        "author": "evarness-tests",
        "license": "Apache-2.0",
        "kind": "brief",
        "brief": {
            "subject": "Test",
            "recipient": "user",
            "uses": [],
            "layout": "hello {{greeting}}",
            "slots": {"greeting": {"kind": "literal", "value": "world"}},
        },
    }
    (directory / "manifest.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    return directory


@pytest.fixture
def plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One installed plugin, ``acme``, whose directory the skill loader is told about."""
    directory = tmp_path / "site" / "acme"
    directory.mkdir(parents=True)
    monkeypatch.setattr(
        "evarness.foundation.plugin_dirs.installed_plugin_dirs",
        lambda home=None: [("acme", directory)],
    )
    return directory


def test_a_pack_inside_an_installed_plugin_is_discovered(tmp_path: Path, plugin_dir: Path) -> None:
    pack = _write_brief_pack(plugin_dir / "skills" / "nested" / "acme-brief", "acme-brief")
    (plugin_dir / "manifest.yaml").write_text("name: acme\n", encoding="utf-8")  # not a pack

    assert discover_plugin_skill_dirs() == (("acme", pack.resolve()),)
    assert discover_skill_dirs(tmp_path / "repo") == (pack.resolve(),)

    names = [p.manifest.name for p in SkillRegistry(tmp_path / "repo").discover()]
    assert names == ["acme-brief"]


def test_a_plugin_without_a_skills_directory_adds_nothing(tmp_path: Path, plugin_dir: Path) -> None:
    assert discover_plugin_skill_dirs() == ()
    assert SkillRegistry(tmp_path / "repo").discover() == ()


def test_config_skills_win_a_name_collision_and_it_is_logged_once(
    tmp_path: Path, plugin_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = tmp_path / "repo"
    owner = _write_brief_pack(repo / "config" / "skills" / "acme-brief", "acme-brief", marker="own")
    _write_brief_pack(plugin_dir / "skills" / "acme-brief", "acme-brief", marker="plugin")
    _write_brief_pack(plugin_dir / "skills" / "acme-extra", "acme-extra")

    registry = SkillRegistry(repo)
    with caplog.at_level(logging.INFO, logger="evarness.tools.skills.registry"):
        first = registry.discover()
        registry.discover()  # per chat turn and on hot reload: said once

    by_name = {p.manifest.name: p for p in first}
    assert set(by_name) == {"acme-brief", "acme-extra"}
    assert by_name["acme-brief"].skill_dir == owner.resolve()
    shadowed = [r for r in caplog.records if "shadowed" in r.getMessage()]
    assert len(shadowed) == 1


def test_a_broken_plugin_pack_is_a_load_failure_not_an_abort(
    tmp_path: Path, plugin_dir: Path
) -> None:
    _write_brief_pack(plugin_dir / "skills" / "good", "good")
    bad = plugin_dir / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "manifest.yaml").write_text("- not a mapping\n", encoding="utf-8")

    registry = SkillRegistry(tmp_path / "repo")
    assert [p.manifest.name for p in registry.discover()] == ["good"]
    assert list(registry.load_failures) == [bad.resolve()]


def test_skills_list_names_the_plugin_that_ships_a_pack(tmp_path: Path, plugin_dir: Path) -> None:
    repo = tmp_path / "repo"
    _write_brief_pack(repo / "config" / "skills" / "own", "own")
    pack = _write_brief_pack(plugin_dir / "skills" / "acme-brief", "acme-brief")

    rows = {entry.name: entry for entry in _list_skill_entries(repo)}
    assert rows["own"].source == "config/skills"
    assert rows["acme-brief"].source == "plugin:acme"
    assert rows["acme-brief"].location == str(pack.resolve())  # outside the repo: absolute
