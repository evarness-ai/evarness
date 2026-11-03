"""resolve_persona: explicit configuration beats parsing, parsing stays sane (#408)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.memory.identity import loader
from evarness.memory.identity.loader import (
    Persona,
    agent_name_from_soul,
    agent_purpose_from_soul,
    resolve_persona,
)

SCRIBE = (
    "# Scribe - Soul\n\n## Mission\nYou are Scribe, a writing assistant for technical documents."
)


@pytest.fixture
def no_user_soul(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No SOUL.md on disk: only the text handed in and the shipped default count."""
    monkeypatch.setattr(loader, "SOUL_PATH", tmp_path / "missing" / "SOUL.md")
    return tmp_path


# ─── precedence ─────────────────────────────────────────────────────────


def test_frontmatter_name_and_purpose_beat_the_parsed_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    soul_file = tmp_path / "SOUL.md"
    soul_file.write_text(
        "---\nname: Atlas\npurpose: a research assistant\n---\n\n"
        "# How my assistant behaves\n\nYou are Scribe, a writing assistant.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "SOUL_PATH", soul_file)
    body = loader.load_soul()

    assert body is not None and body.startswith("# How my assistant behaves")
    assert resolve_persona(body) == Persona("Atlas", "a research assistant")


def test_parsed_body_is_used_when_frontmatter_has_nothing(no_user_soul: Path) -> None:
    assert resolve_persona(SCRIBE) == Persona(
        "Scribe", "a writing assistant for technical documents"
    )


def test_default_persona_keeps_the_stock_example(no_user_soul: Path) -> None:
    seeded_copy = loader._SOUL_DEFAULT.read_text(encoding="utf-8")
    _, body = loader._split_frontmatter(seeded_copy)

    assert resolve_persona(body) == Persona("Evarness", None)
    assert resolve_persona(None) == Persona("Evarness", None)


def test_custom_name_without_a_purpose_gets_a_generic_one(no_user_soul: Path) -> None:
    assert resolve_persona("# Mentor\n\n## Rules\n- be brief\n") == Persona(
        "Mentor", "a personal assistant"
    )


def test_evarness_with_a_custom_purpose_keeps_it(no_user_soul: Path) -> None:
    persona = resolve_persona("# Evarness\n\n## Mission\nYou are Evarness, a cooking assistant.")

    assert persona == Persona("Evarness", "a cooking assistant")


# ─── name parsing ───────────────────────────────────────────────────────


def test_name_from_heading_strips_the_soul_suffix() -> None:
    assert agent_name_from_soul("# Scribe — Soul\n") == "Scribe"
    assert agent_name_from_soul("# Scribe - Soul\n") == "Scribe"


def test_name_from_identity_sentence_must_look_like_a_name() -> None:
    assert agent_name_from_soul("I'm Pip, your pocket planner.") == "Pip"
    assert agent_name_from_soul("You are Ada Lovelace, a maths tutor.") == "Ada Lovelace"
    assert agent_name_from_soul("You are not allowed to give medical advice.") is None
    assert agent_name_from_soul("You are warm, direct and concise.") is None


def test_label_headings_are_not_names() -> None:
    assert agent_name_from_soul("# Soul\n\nYou are Atlas, a research assistant.") == "Atlas"
    assert agent_name_from_soul("# Who you are\n\nBe terse.") is None


# ─── purpose parsing ────────────────────────────────────────────────────


def test_purpose_stops_at_the_end_of_the_sentence_not_at_an_abbreviation() -> None:
    soul = (
        "# Scribe\n\n## Mission\nYou are Scribe, an assistant for Dr. Lee's clinic. Keep it short."
    )

    assert agent_purpose_from_soul(soul) == "an assistant for Dr. Lee's clinic"


def test_purpose_survives_a_wrapped_sentence_and_quotes() -> None:
    soul = '# Scribe\n\n## Purpose\nYou are Scribe, the "ghostwriter"\nfor the team. Never reveal internals.'

    assert agent_purpose_from_soul(soul) == 'the "ghostwriter" for the team'


def test_purpose_ignores_trait_sentences() -> None:
    assert agent_purpose_from_soul("# Nova\n\nYou are warm, direct and concise.") is None


def test_purpose_falls_back_to_the_section_first_line() -> None:
    assert agent_purpose_from_soul("# Scribe\n\n## Mission\n- a writing assistant.\n") == (
        "a writing assistant"
    )


def test_purpose_is_bounded() -> None:
    soul = "# Scribe\n\n## Mission\nYou are Scribe, " + "a very " * 60 + "long purpose"

    purpose = agent_purpose_from_soul(soul)

    assert purpose is not None and len(purpose) <= loader._PURPOSE_MAX_CHARS
