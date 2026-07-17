"""The one config-overlay mechanism.

Every tunable in Evarness lives in a packaged YAML file, overridable per user
and per process, resolved the same way everywhere:

    packaged default  ←  ~/.evarness/<name>.yaml  ←  $EVARNESS_<NAME>

Modules declare *what* their sections are; this module owns *how* the merge
works. A broken user file is logged and ignored — user config must never brick
the engine — but it is logged, not silently skipped.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger("evarness")

USER_DIR = Path.home() / ".evarness"


def user_path(filename: str, env_var: str | None = None) -> Path:
    """``~/.evarness/<filename>``, overridable via ``env_var``."""
    if env_var:
        env = os.environ.get(env_var)
        if env:
            return Path(env)
    return USER_DIR / filename


def load_overlaid_yaml(
    packaged: Path, env_var: str, user_default: Path, sections: tuple[str, ...]
) -> dict:
    """Packaged YAML with the user file (if any) merged over it, per section.
    Dict-valued sections merge key-wise (user wins); other values replace."""
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
