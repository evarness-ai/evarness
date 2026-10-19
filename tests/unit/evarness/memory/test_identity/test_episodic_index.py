"""Tests for section-aware episodic memory indexing."""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.memory.identity import loader


@pytest.fixture()
def evarness_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / ".evarness"
    monkeypatch.setattr(loader, "EVARNESS_HOME", home)
    monkeypatch.setattr(loader, "MEMORY_DIR", home / "memory")
    monkeypatch.setattr(loader, "EPISODIC_MD_PATH", home / "memory" / "episodic.md")
    return home


def test_list_episodic_patterns_indexes_sections_without_hints(evarness_home: Path) -> None:
    episodic = evarness_home / "memory" / "episodic.md"
    episodic.parent.mkdir(parents=True, exist_ok=True)
    episodic.write_text(
        "# Episodic Memory\n\n"
        "## Long-Term Patterns\n\n"
        "- 2026-05-08 - User prefers framework-first implementation.\n\n"
        "## Routines\n\n"
        "| Routine | Wiki Page | Status | Last Seen |\n"
        "|---|---|---|---|\n"
        "| Daily repo brief | [[daily-repo-brief]] | draft | 2026-05-08 |\n\n"
        "## Retrieval Hints\n\n"
        "- For durable design decisions, search the wiki first.\n",
        encoding="utf-8",
    )

    texts = [item.text for item in loader.list_episodic_patterns()]

    assert "2026-05-08 - User prefers framework-first implementation." in texts
    assert "Routines: Daily repo brief | [[daily-repo-brief]] | draft | 2026-05-08" in texts
    assert not any("durable design decisions" in text for text in texts)
    assert not any("Routine | Wiki Page" in text for text in texts)


def test_append_episodic_pattern_inserts_under_long_term_patterns(evarness_home: Path) -> None:
    episodic = evarness_home / "memory" / "episodic.md"
    episodic.parent.mkdir(parents=True, exist_ok=True)
    episodic.write_text(
        "# Episodic Memory\n\n"
        "## Long-Term Patterns\n\n"
        "<!-- patterns here -->\n\n"
        "## Routines\n\n"
        "| Routine | Wiki Page | Status | Last Seen |\n"
        "|---|---|---|---|\n",
        encoding="utf-8",
    )

    item = loader.append_episodic_pattern("Evarness should keep episodic memory compact.")
    text = episodic.read_text(encoding="utf-8")
    pattern_index = text.index(item.text)
    routines_index = text.index("## Routines")

    assert pattern_index < routines_index
    assert "Evarness should keep episodic memory compact." in item.text
