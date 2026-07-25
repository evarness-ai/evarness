"""Prompt vocabulary loader: templates, default system prompts, loop
protocols, and generator prompts live in prompts.yaml — data, not code. A user
file at ~/.evarness/prompts.yaml (env EVARNESS_PROMPTS) merges over the
packaged one per section, so presets can be added or defaults overridden
without touching the package.
"""

from __future__ import annotations

import logging

from evarness.core.config import load_overlaid_yaml
from pathlib import Path

log = logging.getLogger("evarness")


_PACKAGED = Path(__file__).parent / "prompts.yaml"
_SECTIONS = ("templates", "defaults", "protocols", "nudges", "generator")


def load_prompts() -> dict[str, dict[str, str]]:
    return load_overlaid_yaml(
        _PACKAGED, "EVARNESS_PROMPTS", Path.home() / ".evarness" / "prompts.yaml", _SECTIONS
    )


_VOCAB = load_prompts()
PROMPT_TEMPLATES: dict[str, str] = _VOCAB["templates"]
DEFAULTS: dict[str, str] = _VOCAB["defaults"]
PROTOCOLS: dict[str, str] = _VOCAB["protocols"]
NUDGES: dict[str, str] = _VOCAB["nudges"]
GENERATOR: dict[str, str] = _VOCAB["generator"]
