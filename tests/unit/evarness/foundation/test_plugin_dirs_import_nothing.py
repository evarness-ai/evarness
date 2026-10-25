"""Locating a plugin's directory must not run the plugin's code (ADR-0136).

An entry point names a dotted module (``overlay.plugins.demo.plugin:setup``). Resolving it
with ``importlib.util.find_spec`` imports every parent package, which executes their
``__init__.py`` -- code of a plugin the owner may have switched off. Finding the directory
is a question about files, so it is answered from them.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from evarness.foundation.plugin_dirs import installed_plugin_dirs
from evarness.runtime.plugin_host.profile import PluginRef
from evarness.runtime.plugin_host.skill_sources import profile_skill_sources
from evarness.tools.skills.registry import SkillRegistry

_PACKAGE = "overlay_demo"


@pytest.fixture
def entry_point_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An installed plugin ``demo`` in a dotted package whose ``__init__`` files leave a marker."""
    site = tmp_path / "site"
    marker = tmp_path / "imported.marker"
    package = site / _PACKAGE
    plugin = package / "plugins" / "demo"
    pack = plugin / "skills" / "demo-pack"
    pack.mkdir(parents=True)
    for init in (
        package / "__init__.py",
        package / "plugins" / "__init__.py",
        plugin / "__init__.py",
    ):
        init.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n")
    (plugin / "manifest.yaml").write_text("name: demo\n", encoding="utf-8")
    (plugin / "plugin.py").write_text("def setup(api):\n    pass\n", encoding="utf-8")
    (pack / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo-pack",
                "version": "1.0.0",
                "description": "d",
                "author": "t",
                "license": "Apache-2.0",
                "kind": "brief",
                "brief": {
                    "subject": "T",
                    "recipient": "user",
                    "uses": [],
                    "layout": "hi {{g}}",
                    "slots": {"g": {"kind": "literal", "value": "w"}},
                },
            }
        ),
        encoding="utf-8",
    )
    dist = site / "overlay_demo-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: overlay-demo\nVersion: 1.0\n")
    (dist / "entry_points.txt").write_text(
        f"[evarness.plugins]\ndemo = {_PACKAGE}.plugins.demo.plugin:setup\n"
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path / "home"))
    for var in ("EVARNESS_PLUGINS_DISABLE", "EVARNESS_PLUGINS_ENABLE", "EVARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "profile.yaml").write_text("plugins:\n  - name: demo\n", encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    yield marker
    for name in [m for m in sys.modules if m == _PACKAGE or m.startswith(f"{_PACKAGE}.")]:
        del sys.modules[name]


def test_finding_an_entry_point_plugin_imports_none_of_its_package(
    entry_point_plugin: Path,
) -> None:
    dirs = dict(installed_plugin_dirs())
    assert dirs["demo"].name == "demo" and (dirs["demo"] / "manifest.yaml").is_file()
    assert not entry_point_plugin.exists()
    assert _PACKAGE not in sys.modules


def test_the_other_readers_of_installed_plugins_import_nothing_either(
    entry_point_plugin: Path,
) -> None:
    """Web nav, the ontology and the expiry reader all share ``installed_plugin_dirs``."""
    from evarness.memory.ontology import vocabulary_fragments
    from evarness.runtime.plugin_host.nav import installed_manifests

    assert "demo" in [name for name, _manifest in installed_manifests()]
    vocabulary_fragments()
    assert not entry_point_plugin.exists()
    assert _PACKAGE not in sys.modules


def test_a_plugin_without_a_manifest_contributes_no_pack_and_says_why(
    entry_point_plugin: Path, tmp_path: Path
) -> None:
    from evarness.runtime.plugin_host.skill_sources import _profile_verdict

    bare = tmp_path / "bare"
    bare.mkdir()
    reason = _profile_verdict(PluginRef(name="bare"), bare)
    assert reason == "no manifest.yaml: plugins without one ship no skill packs"


def test_the_profile_view_of_a_disabled_plugin_imports_none_of_its_package(
    entry_point_plugin: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVARNESS_PLUGINS_DISABLE", "demo")
    config = tmp_path / "config"
    config.mkdir()
    registry = SkillRegistry(tmp_path / "repo", plugin_sources=profile_skill_sources(config))
    assert registry.discover() == ()
    assert [p.name for p in registry.unmounted] == ["demo-pack"]
    assert not entry_point_plugin.exists()
    assert _PACKAGE not in sys.modules


def test_a_booted_runtime_with_the_plugin_disabled_imports_none_of_its_package(
    entry_point_plugin: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from evarness.runtime import build_runtime
    from evarness.services.health.service import clear_check_providers

    monkeypatch.setenv("EVARNESS_PLUGINS_DISABLE", "demo")
    monkeypatch.setenv("EVARNESS_PROFILE", "default")
    monkeypatch.setenv("EVARNESS_DISABLE_ARBITER", "1")
    monkeypatch.setenv("EVARNESS_DISABLE_WARMUP", "1")
    clear_check_providers()
    try:
        rt = build_runtime(
            config_dir=Path(__file__).resolve().parents[4] / "config",
            data_dir=tmp_path / "data",
            use_background_scheduler=False,
        )
        assert "demo-pack" not in [p.manifest.name for p in rt.skill_registry.discover()]
        assert [p.name for p in rt.skill_registry.unmounted] == ["demo-pack"]
        assert not entry_point_plugin.exists()
        assert _PACKAGE not in sys.modules
    finally:
        clear_check_providers()
