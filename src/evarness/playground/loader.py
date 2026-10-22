"""Load scenario suites from YAML and locate the default suite directory."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from evarness.foundation.paths import config_path

from .models import ScenarioSuite


class SuiteError(ValueError):
    """A suite file that does not parse or does not fit the schema.

    ``message`` names the file and what is wrong with it, in text written here for the
    person fixing the file: a YAML problem with its line, or each field the schema
    refused. It never carries the parser's own text (which dumps the input back), so a
    surface shows it as it is.
    """

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else ""


def default_scenario_dir() -> Path:
    """Where suites live by default: ``config/playground`` under the repo/config.

    Honors ``EVARNESS_PLAYGROUND_DIR``, then the resolved config directory
    (``foundation.paths.config_dir()``: ``EVARNESS_CONFIG_DIR``, the checkout, the packaged
    defaults), so tests and adopters can point it elsewhere without editing code.
    """
    override = os.environ.get("EVARNESS_PLAYGROUND_DIR")
    if override:
        return Path(override).expanduser()
    return config_path("playground")


def discover_suites(scenario_dir: Path | None = None) -> list[Path]:
    """Return every ``*.yaml`` suite file under *scenario_dir*, sorted."""
    root = scenario_dir or default_scenario_dir()
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.yaml") if p.is_file())


def load_suite(path: Path) -> ScenarioSuite:
    """Parse one YAML file into a validated ``ScenarioSuite``.

    Raises :class:`SuiteError` (a ``ValueError``) naming the file on malformed YAML or a
    schema violation, so a bad suite fails loudly instead of silently skipping cases.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SuiteError(f"{path}: invalid YAML{_yaml_where(exc)}") from exc
    if not isinstance(raw, dict):
        raise SuiteError(f"{path}: expected a mapping at the top level")
    raw.setdefault("name", path.stem)
    try:
        return ScenarioSuite.model_validate(raw)
    except ValidationError as exc:
        raise SuiteError(f"{path}: {_schema_problems(exc)}") from exc


def _yaml_where(exc: yaml.YAMLError) -> str:
    """`` at line N`` when the parser marked where it stopped, else nothing."""
    mark = getattr(exc, "problem_mark", None)
    return f" at line {mark.line + 1}" if mark is not None else ""


def _schema_problems(exc: ValidationError) -> str:
    """Each refused field as ``where: why``, e.g. ``scenarios[0].message: Field required``."""
    problems = []
    for error in exc.errors():
        where = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error["loc"])
        problems.append(f"{where.lstrip('.') or 'suite'}: {error['msg']}")
    return "; ".join(problems) or "does not fit the suite schema"


def load_all_suites(scenario_dir: Path | None = None) -> list[ScenarioSuite]:
    """Load every discoverable suite."""
    return [load_suite(p) for p in discover_suites(scenario_dir)]
