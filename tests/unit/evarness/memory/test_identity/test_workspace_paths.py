"""Tests for the workspace path migration (2026-05-19).

Identity moved from ``~/.evarness/identity/soul.md`` + ``~/.evarness/memory/user.md``
to a consolidated ``~/.evarness/workspace/`` location. The loader must:

- Prefer the workspace copy when present.
- Fall back to the legacy path so pre-migration installs keep working.
- Auto-migrate legacy -> workspace on first ``bootstrap_identity_files``.
- Strip frontmatter from both SOUL.md and USER.md before injecting body
  text into prompts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.memory.identity import loader


@pytest.fixture()
def evarness_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / ".evarness"
    monkeypatch.setattr(loader, "EVARNESS_HOME", home)
    monkeypatch.setattr(loader, "WORKSPACE_DIR", home / "workspace")
    monkeypatch.setattr(loader, "IDENTITY_DIR", home / "identity")
    monkeypatch.setattr(loader, "MEMORY_DIR", home / "memory")
    monkeypatch.setattr(loader, "BEHAVIORS_DIR", home / "behaviors")
    monkeypatch.setattr(loader, "SOUL_PATH", home / "workspace" / "SOUL.md")
    monkeypatch.setattr(loader, "USER_MD_PATH", home / "workspace" / "USER.md")
    monkeypatch.setattr(loader, "_LEGACY_SOUL_PATH", home / "identity" / "soul.md")
    monkeypatch.setattr(loader, "_LEGACY_USER_MD_PATH", home / "memory" / "user.md")
    monkeypatch.setattr(loader, "ACTIVE_MD_PATH", home / "memory" / "active.md")
    monkeypatch.setattr(loader, "EPISODIC_MD_PATH", home / "memory" / "episodic.md")
    return home


def test_load_soul_prefers_workspace_copy(evarness_home: Path) -> None:
    workspace = evarness_home / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text(
        "---\nname: Evarness\nclassification: internal\n---\n\nworkspace body\n",
        encoding="utf-8",
    )

    assert loader.load_soul() == "workspace body"


def test_load_soul_falls_back_to_legacy_path(evarness_home: Path) -> None:
    legacy = evarness_home / "identity"
    legacy.mkdir(parents=True)
    (legacy / "soul.md").write_text(
        "---\nname: Evarness\n---\n\nlegacy body\n",
        encoding="utf-8",
    )
    # Workspace dir doesn't exist yet — loader must still find legacy.
    assert loader.load_soul() == "legacy body"


def test_load_soul_returns_none_when_neither_exists(evarness_home: Path) -> None:
    assert loader.load_soul() is None


def test_load_user_md_prefers_workspace_and_strips_frontmatter(
    evarness_home: Path,
) -> None:
    workspace = evarness_home / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "USER.md").write_text(
        "---\nclassification: personal\negress: local-only\n---\n\n"
        "# User Profile\n\nname: Robin\n",
        encoding="utf-8",
    )

    body = loader.load_user_md()
    assert body is not None
    assert body.startswith("# User Profile")
    assert "classification:" not in body


def test_load_user_md_falls_back_to_legacy(evarness_home: Path) -> None:
    legacy = evarness_home / "memory"
    legacy.mkdir(parents=True)
    (legacy / "user.md").write_text("# Legacy profile\n", encoding="utf-8")

    assert loader.load_user_md() == "# Legacy profile"


def test_bootstrap_migrates_legacy_soul_into_workspace(evarness_home: Path) -> None:
    legacy = evarness_home / "identity"
    legacy.mkdir(parents=True)
    (legacy / "soul.md").write_text(
        "---\nname: Evarness\n---\n\nlegacy soul body\n", encoding="utf-8"
    )

    loader.bootstrap_identity_files()

    workspace_soul = evarness_home / "workspace" / "SOUL.md"
    assert workspace_soul.exists()
    assert "legacy soul body" in workspace_soul.read_text(encoding="utf-8")


def test_bootstrap_migrates_legacy_user_md_into_workspace(evarness_home: Path) -> None:
    legacy = evarness_home / "memory"
    legacy.mkdir(parents=True)
    (legacy / "user.md").write_text(
        "# Legacy user\n\n## Auto-detected\n- **name**: Robin  <!-- auto: confidence=0.95 -->\n",
        encoding="utf-8",
    )

    loader.bootstrap_identity_files()

    workspace_user = evarness_home / "workspace" / "USER.md"
    assert workspace_user.exists()
    text = workspace_user.read_text(encoding="utf-8")
    assert "# Legacy user" in text
    assert "name**: Robin" in text


def test_bootstrap_does_not_clobber_existing_workspace_files(evarness_home: Path) -> None:
    workspace = evarness_home / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("workspace authored\n", encoding="utf-8")

    legacy = evarness_home / "identity"
    legacy.mkdir(parents=True)
    (legacy / "soul.md").write_text("legacy authored\n", encoding="utf-8")

    loader.bootstrap_identity_files()

    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "workspace authored\n"


def test_append_user_fact_to_md_writes_workspace_path_with_frontmatter(
    evarness_home: Path,
) -> None:
    """First-time fact append on a clean install must seed the workspace
    file with the classification frontmatter, not the legacy plain header."""

    written = loader.append_user_fact_to_md("name", "Robin", 0.95)

    assert written is True
    workspace_user = evarness_home / "workspace" / "USER.md"
    assert workspace_user.exists()
    text = workspace_user.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "classification: personal" in text
    assert "name**: Robin" in text


# ---------------------------------------------------------------------------
# AGENTS.md + evarness.md loaders (on-demand reads)
# ---------------------------------------------------------------------------


def test_load_agents_md_strips_frontmatter(
    evarness_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = evarness_home / "workspace"
    workspace.mkdir(parents=True)
    agents_path = workspace / "AGENTS.md"
    agents_path.write_text(
        "---\nclassification: internal\nload: on-demand\n---\n\n"
        "# Evarness Agent Registry\n\n## Chat agent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "AGENTS_MD_PATH", agents_path)

    body = loader.load_agents_md()
    assert body is not None
    assert body.startswith("# Evarness Agent Registry")
    assert "classification:" not in body


def test_load_agents_md_returns_none_when_missing(
    evarness_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "AGENTS_MD_PATH", evarness_home / "workspace" / "AGENTS.md")
    assert loader.load_agents_md() is None


def test_load_harness_md_reads_repo_doc(
    evarness_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness_path = tmp_path / "evarness.md"
    harness_path.write_text("# Evarness Harness\n\nOperational framework body.\n", encoding="utf-8")
    monkeypatch.setattr(loader, "HARNESS_DOC_PATH", harness_path)

    body = loader.load_harness_md()
    assert body is not None
    assert "# Evarness Harness" in body
    assert "Operational framework body." in body


def test_load_harness_md_returns_none_when_missing(
    evarness_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "HARNESS_DOC_PATH", tmp_path / "missing.md")
    assert loader.load_harness_md() is None


# ---------------------------------------------------------------------------
# evarness_doc tool — exposed to the ReAct loop for on-demand doc reads
# ---------------------------------------------------------------------------


def _evarness_doc_spec():
    from evarness.runtime.react_tools import builtin_react_tools

    specs = builtin_react_tools(semantic_index=None, wiki=None, repo_root=None)
    matches = [spec for spec in specs if getattr(spec, "name", None) == "evarness_doc"]
    assert len(matches) == 1, f"expected exactly one evarness_doc tool, got {len(matches)}"
    return matches[0]


def test_evarness_doc_tool_loads_workspace_doc_by_name(
    evarness_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tool dispatch resolves the name to the right loader and returns body."""

    workspace = evarness_home / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text(
        "---\nclassification: internal\n---\n\n# Agents\n\nentry body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "AGENTS_MD_PATH", workspace / "AGENTS.md")

    spec = _evarness_doc_spec()
    result = spec.call({"name": "AGENTS"})

    assert "# Agents" in result
    assert "entry body" in result
    assert "classification:" not in result


def test_evarness_doc_tool_returns_error_on_unknown_name() -> None:
    spec = _evarness_doc_spec()
    result = spec.call({"name": "BOGUS"})
    assert result.startswith("Error: evarness_doc unknown name")
    assert "AGENTS" in result and "HARNESS" in result  # error lists valid names


def test_evarness_doc_tool_returns_error_when_name_missing() -> None:
    spec = _evarness_doc_spec()
    result = spec.call({})
    assert result.startswith("Error: evarness_doc requires a 'name'")


def test_evarness_doc_tool_handles_missing_file_gracefully(
    evarness_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "AGENTS_MD_PATH", evarness_home / "workspace" / "AGENTS.md")
    spec = _evarness_doc_spec()
    result = spec.call({"name": "AGENTS"})
    assert "file not present" in result


def test_evarness_doc_tool_normalises_case(
    evarness_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names are case-insensitive — agents, AGENTS, Agents all work."""

    workspace = evarness_home / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("body\n", encoding="utf-8")
    monkeypatch.setattr(loader, "AGENTS_MD_PATH", workspace / "AGENTS.md")

    spec = _evarness_doc_spec()
    assert "body" in spec.call({"name": "agents"})
    assert "body" in spec.call({"name": "Agents"})
