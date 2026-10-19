"""The config resolver: ``EVARNESS_CONFIG_DIR`` > the checkout's ``config/`` > the packaged defaults.

A plain ``pip install`` has no checkout, so every config reader has to land on the copy
the wheel ships (``evarness/_data/config``). These tests fake "no checkout" by
pointing ``repo_root`` somewhere that is not one, and "the wheel" by pointing
``packaged_config_dir`` at a temp tree.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import evarness
from evarness.foundation import paths

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVARNESS_CONFIG_DIR", raising=False)


@pytest.fixture
def wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No checkout above the package, and a packaged config tree at a temp path."""
    site = tmp_path / "site-packages"
    site.mkdir()
    packaged = site / "evarness" / "_data" / "config"
    packaged.mkdir(parents=True)
    monkeypatch.setattr(paths, "repo_root", lambda: site)
    monkeypatch.setattr(paths, "packaged_config_dir", lambda: packaged)
    return packaged


# -- the order ---------------------------------------------------------------------------


def test_env_override_wins_over_the_checkout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVARNESS_CONFIG_DIR", str(tmp_path))
    assert paths.config_dir() == tmp_path
    assert paths.config_path("memory", "retention.yaml") == tmp_path / "memory" / "retention.yaml"


def test_env_override_expands_the_home_dir(monkeypatch) -> None:
    monkeypatch.setenv("EVARNESS_CONFIG_DIR", "~/evarness-config")
    assert paths.config_dir() == Path.home() / "evarness-config"


def test_env_override_wins_over_the_package(wheel: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVARNESS_CONFIG_DIR", str(tmp_path / "mine"))
    assert paths.config_dir() == tmp_path / "mine"


def test_a_checkout_reads_its_own_config_dir() -> None:
    # The test suite runs from a checkout: the same directory the old cwd-relative
    # Path("config") meant when run from the repo root, and the Docker image's /app/config.
    assert paths.config_dir() == REPO / "config"
    assert paths.config_root() == REPO
    assert (paths.config_dir() / "governor" / "policy.yaml").is_file()


def test_the_checkout_answer_does_not_depend_on_the_current_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)  # the old Path("config") found nothing from here
    assert paths.config_dir() == REPO / "config"


def test_no_checkout_falls_back_to_the_packaged_defaults(wheel: Path) -> None:
    assert paths.config_dir() == wheel
    assert paths.default_config_dir() == wheel
    assert paths.config_root() == wheel.parent  # evarness/_data: <root>/config is it


def test_another_projects_pyproject_is_not_a_checkout(tmp_path: Path, monkeypatch) -> None:
    """A wheel installed into some project's .venv walks up to THAT project's
    pyproject.toml; its config/ is not Evarness's."""
    other = tmp_path / "their-project"
    (other / "config").mkdir(parents=True)
    (other / "pyproject.toml").write_text("[project]\nname='theirs'\n")
    monkeypatch.setattr(paths, "repo_root", lambda: other)
    assert paths.config_dir() == paths.packaged_config_dir()


def test_a_checkout_without_config_falls_back_to_the_package(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    monkeypatch.setattr(paths, "__file__", str(root / "src/evarness/foundation/paths.py"))
    assert paths.config_dir() == paths.packaged_config_dir()
    (root / "config").mkdir()
    assert paths.config_dir() == root / "config"


def test_default_config_dir_ignores_the_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVARNESS_CONFIG_DIR", str(tmp_path))
    assert paths.default_config_dir() == REPO / "config"


def test_the_packaged_dir_sits_inside_the_package() -> None:
    expected = Path(evarness.__file__).parent / "_data" / "config"
    assert paths.packaged_config_dir() == expected


# -- readers land on the packaged defaults with no checkout ----------------------------


def test_governor_policy_is_read_from_the_package(wheel: Path, tmp_path: Path, monkeypatch) -> None:
    """The governor used to fail at import from a wheel: site-packages/config/... ."""
    monkeypatch.delenv("EVARNESS_GOVERNOR_POLICY_PATH", raising=False)
    monkeypatch.setenv("EVARNESS_GOVERNOR_AUDIT_DB", str(tmp_path / "audit.db"))
    (wheel / "governor").mkdir()
    shutil.copy(REPO / "config" / "governor" / "policy.yaml", wheel / "governor")
    from evarness.server.governor import main as governor_main

    service = governor_main._build_governor_service()
    assert service.policy_engine.config.routes  # the packaged policy, parsed


def test_playground_suites_come_from_the_package(wheel: Path, monkeypatch) -> None:
    monkeypatch.delenv("EVARNESS_PLAYGROUND_DIR", raising=False)
    from evarness.playground.loader import default_scenario_dir, discover_suites

    (wheel / "playground").mkdir()
    shutil.copy(REPO / "config" / "playground" / "core-deterministic.yaml", wheel / "playground")
    assert default_scenario_dir() == wheel / "playground"
    assert [p.name for p in discover_suites()] == ["core-deterministic.yaml"]


def test_memory_ontology_dir_comes_from_the_package(wheel: Path) -> None:
    from evarness.memory.ontology import memory_config_dir

    shutil.copytree(REPO / "config" / "memory", wheel / "memory")
    assert memory_config_dir() == wheel / "memory"


def test_status_counts_the_packaged_skills_and_heartbeats(wheel: Path, tmp_path: Path) -> None:
    from evarness.services.system.status import evarness_status

    shutil.copytree(REPO / "config" / "skills" / "builtin", wheel / "skills" / "builtin")
    shutil.copy(REPO / "config" / "heartbeats.yaml", wheel)

    class _NoAccounts:
        def ensure_schema(self) -> None: ...

        def list(self, active_only: bool = False) -> list[object]:
            return []

    status = evarness_status(accounts_store=_NoAccounts(), data_dir=tmp_path / "data")
    assert status.skill_count == len(list((wheel / "skills").rglob("manifest.yaml"))) > 0


# -- the data dir (never the current directory) ---------------------------------------


@pytest.fixture
def _no_data_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVARNESS_DATA_DIR", raising=False)
    monkeypatch.delenv("EVARNESS_HOME", raising=False)


def test_data_dir_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVARNESS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path / "h"))
    assert paths.data_dir() == tmp_path / "d"


@pytest.mark.usefixtures("_no_data_env")
def test_a_relocated_home_keeps_its_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path / "h"))
    assert paths.data_dir() == tmp_path / "h" / "data"


@pytest.mark.usefixtures("_no_data_env")
def test_a_checkout_keeps_its_data_beside_its_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # the answer does not depend on where `evarness` runs
    assert paths.data_dir() == REPO / "data"


@pytest.mark.usefixtures("_no_data_env")
def test_an_installed_wheel_keeps_its_data_in_the_home(
    wheel: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "user"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir() == fake_home / ".evarness" / "data"


def test_the_workspace_follows_the_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    assert paths.workspace_dir() == tmp_path / "workspace"
