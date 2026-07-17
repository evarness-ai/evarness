"""Prompt vocabulary loader: templates, default system prompts, loop
protocols, and generator prompts live in prompts.yaml — data, not code. A user
file at ~/.evarness/prompts.yaml (env EVARNESS_PROMPTS) merges over the
packaged one per section, so presets can be added or defaults overridden
without touching the package.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger("evarness")


def load_overlaid_yaml(
    packaged: Path, env_var: str, user_default: Path, sections: tuple[str, ...]
) -> dict:
    """Packaged YAML with the user file (if any) merged over it, per section.
    Shared by prompts.yaml and ui.yaml."""
    data = yaml.safe_load(packaged.read_text(encoding="utf-8")) or {}
    user_file = Path(os.environ.get(env_var, str(user_default)))
    if user_file.exists():
        try:
            user = yaml.safe_load(user_file.read_text(encoding="utf-8")) or {}
            for section in sections:
                if isinstance(user.get(section), dict):
                    data[section] = {**data.get(section, {}), **user[section]}
                elif section in user:  # lists (e.g. example prompts) replace
                    data[section] = user[section]
        except yaml.YAMLError as exc:  # a broken user file must not kill the engine
            log.warning("ignoring unparseable %s: %s", user_file, exc)
    return data


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
