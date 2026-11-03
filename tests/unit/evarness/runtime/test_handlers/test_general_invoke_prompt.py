"""The conversational system prompt carries the configured persona (#408)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.kernel.governance.disclosure import DISCLOSURE_STYLE_GUARDRAIL
from evarness.memory.identity import loader
from evarness.runtime.handlers.general_invoke import _general_system_prompt

SCRIBE = (
    "# Scribe - Soul\n\n## Mission\nYou are Scribe, a writing assistant for technical documents."
)


@pytest.fixture(autouse=True)
def _no_soul_on_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only the soul text handed in counts.

    SOUL.md frontmatter on disk outranks the text by design, and another test on the same
    worker may have seeded the default SOUL.md (``name: Evarness``) into the shared test home.
    """
    monkeypatch.setattr(loader, "SOUL_PATH", tmp_path / "missing" / "SOUL.md")


def test_conversational_prompt_uses_soul_persona_in_disclosure_example() -> None:
    prompt = _general_system_prompt(SCRIBE)

    assert "Agent name: Scribe" in prompt
    assert '"I\'m Scribe, a writing assistant for technical documents."' in prompt
    assert "\"I'm Evarness" not in prompt


def test_conversational_prompt_keeps_stock_example_for_default_persona() -> None:
    prompt = _general_system_prompt(
        "# Evarness — Soul\n\n## Mission\nYou are Evarness, a helpful personal AI assistant. "
        "Answer directly."
    )

    assert "Agent name: Evarness" in prompt
    assert prompt.endswith(DISCLOSURE_STYLE_GUARDRAIL)
