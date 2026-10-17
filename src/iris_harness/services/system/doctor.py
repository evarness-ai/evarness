"""``iris doctor`` -- the install preflight (OSS plan R5, milestone L1).

Answers one question for a new install: can IRIS run here, and if not, what to fix. Each
check is pass / warn / fail / info with a one-line fix; the report ends in a verdict:

* ``ready`` -- everything the real assistant needs is in place;
* ``demo_only`` -- the synthetic demo runs (it uses a scripted model, not Ollama), but
  something real use needs is missing: local models, Ollama, or the 16 GB floor;
* ``not_ready`` -- even the demo cannot run (wrong Python, native Windows, under 8 GB,
  or no writable IRIS_HOME).

The owner's vault master key is a real-use requirement, not a demo one: without it every
governed tool call is refused (#741), but ``iris email demo`` runs in its own home with a
throwaway key (``demo/home.py``) and never reads the owner's, so a missing or invalid key
makes the verdict ``demo_only``, not ``not_ready`` (#246).

The states are System Health's (``services/health/models.HealthState``): green = pass,
yellow = warn, red = fail, grey = info. Doctor is not a second health system: health
watches a running install; doctor runs before there is one, and most of what it checks
(the Python range, total RAM, the starter models) health never looks at.

Thresholds and the model sets come from ``config/doctor.yaml``. A model set names the
host it fits (``fits_when.ram_gb``), the starter models to pull and a sparse override of
``llm_tiers.yaml``; doctor picks the largest set the host meets (``choose_model_set``),
or the one ``--model-set`` forces, and sizes every other check to it. Everything that
reaches the machine is injectable, so the report builds deterministically under test.
The only fixes offered are safe ones: pull the chosen set's missing starter models,
write the set's tiers as tier edits (ADR-0120; a set the owner did not ask for is never
written over an applied one), and create a master key when there is none (a stored key
is never overwritten -- secrets are encrypted under it).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml

from iris_harness.foundation.observability.logging_setup import log_egress
from iris_harness.foundation.paths import config_path
from iris_harness.foundation.plugin_dirs import iris_home
from iris_harness.kernel.governance.audit.digest import AuditKeyState, audit_key_status
from iris_harness.kernel.governance.vault import keys as vault_keys
from iris_harness.llm import tier_edits
from iris_harness.services.health.models import HealthState

_GIB = 1024**3
_DIST_NAME = "iris-harness"
_OLLAMA_TIMEOUT_S = 3.0
_PULL_TIMEOUT_S = 60.0 * 60

# ── config ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StarterModel:
    name: str
    size_gb: float


@dataclass(frozen=True)
class OptionalExtra:
    name: str
    module: str
    purpose: str


@dataclass(frozen=True)
class ModelSet:
    """One ``model_sets`` entry: the host it fits, what to pull, what to override."""

    name: str
    ram_gb: float
    starter_models: tuple[StarterModel, ...]
    #: ``tier -> {editable field: value}``; only what differs from ``llm_tiers.yaml``.
    tiers: dict[str, dict[str, Any]] = field(default_factory=dict)
    description: str = ""
    #: Part of the automatic choice; ``False`` = applied only when forced.
    auto: bool = True

    def describe_tiers(self) -> str:
        """``router -> llama3.2:1b, tier2 -> qwen3:4b``, or ``the shipped tiers``."""
        parts = [
            f"{tier} -> {fields['model']}" if "model" in fields else f"{tier} ({', '.join(fields)})"
            for tier, fields in self.tiers.items()
        ]
        return ", ".join(parts) if parts else "the shipped tiers"


@dataclass(frozen=True)
class DoctorConfig:
    """``config/doctor.yaml``."""

    ram_demo_gb: float
    platforms: dict[str, tuple[str, ...]]
    data_headroom_gb: float
    warn_free_gb: float
    #: Smallest to largest (``ram_gb`` ascending).
    model_sets: tuple[ModelSet, ...]
    optional_extras: tuple[OptionalExtra, ...] = ()

    @property
    def ram_floor_gb(self) -> float:
        """What real use needs at least: the smallest automatic set's RAM."""
        return min(s.ram_gb for s in self.model_sets if s.auto)

    def model_set(self, name: str) -> ModelSet | None:
        return next((s for s in self.model_sets if s.name == name), None)


class DoctorConfigError(ValueError):
    """``doctor.yaml`` is missing a value doctor cannot guess."""


def _starter_models(raw: Any, *, where: str) -> tuple[StarterModel, ...]:
    if not raw:
        raise DoctorConfigError(f"{where}: starter_models is required")
    return tuple(StarterModel(name=str(m["name"]), size_gb=float(m["size_gb"])) for m in raw)


def _model_set(name: str, raw: Any, *, where: str) -> ModelSet:
    if not isinstance(raw, dict):
        raise DoctorConfigError(f"{where}: must be a mapping")
    try:
        ram_gb = float(raw["fits_when"]["ram_gb"])
    except (KeyError, TypeError) as exc:
        raise DoctorConfigError(f"{where}: fits_when.ram_gb is required") from exc
    unknown = set(raw["fits_when"]) - {"ram_gb"}
    if unknown:
        raise DoctorConfigError(f"{where}: fits_when has unknown keys {sorted(unknown)}")
    tiers_raw = raw.get("tiers") or {}
    if not isinstance(tiers_raw, dict):
        raise DoctorConfigError(f"{where}: tiers must be a mapping")
    tiers: dict[str, dict[str, Any]] = {}
    for tier, fields in tiers_raw.items():
        try:
            tiers[str(tier)] = tier_edits.validate_fields(dict(fields or {}))
        except (tier_edits.TierEditError, TypeError) as exc:
            raise DoctorConfigError(f"{where}: tiers.{tier}: {exc}") from exc
    return ModelSet(
        name=name,
        ram_gb=ram_gb,
        starter_models=_starter_models(raw.get("starter_models"), where=where),
        tiers=tiers,
        description=str(raw.get("description", "")),
        auto=bool(raw.get("auto", True)),
    )


def load_doctor_config(path: Path | None = None) -> DoctorConfig:
    """Read ``doctor.yaml`` (the config dir's, unless ``path`` is given).

    No defaults in code: a missing key is an error naming it, so a threshold is only
    ever the one the file states. A set's ``tiers`` are validated as tier edits here,
    so a field that does not fit fails the config, not a later ``--fix``.
    """
    target = path or config_path("doctor.yaml")
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}

    def need(section: str, key: str) -> Any:
        try:
            return raw[section][key]
        except (KeyError, TypeError) as exc:
            raise DoctorConfigError(f"{target}: {section}.{key} is required") from exc

    sets_raw = raw.get("model_sets")
    if not isinstance(sets_raw, dict) or not sets_raw:
        raise DoctorConfigError(f"{target}: model_sets is required")
    sets = tuple(
        _model_set(str(name), body, where=f"{target}: model_sets.{name}")
        for name, body in sets_raw.items()
    )
    if [s.ram_gb for s in sets] != sorted(s.ram_gb for s in sets):
        raise DoctorConfigError(f"{target}: model_sets must be listed smallest to largest")
    if not any(s.auto for s in sets):
        raise DoctorConfigError(f"{target}: at least one model set must be automatic")
    platforms = raw.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise DoctorConfigError(f"{target}: platforms is required")
    return DoctorConfig(
        ram_demo_gb=float(need("ram", "demo_gb")),
        platforms={
            str(system).lower(): tuple(str(m).lower() for m in machines or ())
            for system, machines in platforms.items()
        },
        data_headroom_gb=float(need("disk", "data_headroom_gb")),
        warn_free_gb=float(need("disk", "warn_free_gb")),
        model_sets=sets,
        optional_extras=tuple(
            OptionalExtra(
                name=str(e["name"]), module=str(e["module"]), purpose=str(e.get("purpose", ""))
            )
            for e in raw.get("optional_extras") or ()
        ),
    )


# ── the model set ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelSetChoice:
    """Which set this run is sized to, and why."""

    model_set: ModelSet
    #: The host meets the set's ``fits_when``.
    fits: bool
    #: Named with ``--model-set`` rather than chosen from the host.
    forced: bool
    reason: str
    #: What ``--fix`` last applied (``tier_edits.recorded_model_set``), if anything.
    recorded: tier_edits.RecordedModelSet | None = None

    @property
    def name(self) -> str:
        return self.model_set.name

    @property
    def apply_needed(self) -> bool:
        """``--fix`` should write this set's tiers: another set is applied, or none is
        and this one changes the file's tiers."""
        if self.recorded is not None:
            return self.recorded.name != self.name
        return bool(self.model_set.tiers)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.model_set.description,
            "fits": self.fits,
            "forced": self.forced,
            "reason": self.reason,
            "recorded": None if self.recorded is None else self.recorded.name,
            "apply_needed": self.apply_needed,
            "tiers": self.model_set.tiers,
        }


def choose_model_set(
    config: DoctorConfig,
    ram_total_bytes: int,
    *,
    force: str | None = None,
    recorded: tier_edits.RecordedModelSet | None = None,
) -> ModelSetChoice:
    """The largest automatic set the host meets, or the forced one.

    Whole GiB, as :func:`check_ram`: a 16 GB Linux box reports ~15.6 GiB after the
    kernel's reservations, and it is a 16 GB machine. When nothing fits, the smallest
    automatic set stands in, so the report says what the machine is short of.
    Raises ``KeyError`` for a forced name the file does not have.
    """
    gib = round(ram_total_bytes / _GIB)
    shown = f"{ram_total_bytes / _GIB:.1f} GB RAM"
    if force is not None:
        chosen = config.model_set(force)
        if chosen is None:
            raise KeyError(force)
        fits = gib >= chosen.ram_gb
        short = "" if fits else f"; needs {chosen.ram_gb:g} GB"
        return ModelSetChoice(
            chosen, fits, True, f"forced with --model-set ({shown}{short})", recorded
        )
    fitting = [s for s in config.model_sets if s.auto and gib >= s.ram_gb]
    if fitting:
        chosen = fitting[-1]
        return ModelSetChoice(chosen, True, False, f"the largest set {shown} fits", recorded)
    smallest = next(s for s in config.model_sets if s.auto)
    return ModelSetChoice(
        smallest,
        False,
        False,
        f"no set fits {shown}; the smallest, {smallest.name}, needs {smallest.ram_gb:g} GB",
        recorded,
    )


def settings_store() -> Any:
    """The settings store tier edits live in (one place for a test to replace)."""
    from iris_harness.foundation.settings import SettingsStore

    return SettingsStore()


def recorded_model_set() -> tier_edits.RecordedModelSet | None:
    return tier_edits.recorded_model_set(settings_store())


# ── the report ──────────────────────────────────────────────────────────────────

#: What a failing check stops: ``all`` (even the demo), ``use`` (real use; the demo
#: still runs) or ``none`` (a warning or information).
Blocks = Literal["all", "use", "none"]

_STATUS = {
    HealthState.GREEN: "pass",
    HealthState.YELLOW: "warn",
    HealthState.RED: "fail",
    HealthState.GREY: "info",
}


@dataclass(frozen=True)
class DoctorCheck:
    """One preflight row: a verdict, what was seen, and the one-line fix."""

    name: str
    state: HealthState
    detail: str
    fix: str | None = None
    blocks: Blocks = "none"

    @property
    def status(self) -> str:
        return _STATUS[self.state]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "state": self.state.value,
            "detail": self.detail,
            "fix": self.fix,
            "blocks": self.blocks,
        }


class Verdict(StrEnum):
    READY = "ready"
    DEMO_ONLY = "demo_only"
    NOT_READY = "not_ready"

    @property
    def exit_code(self) -> int:
        """For scripts and CI: 0 ready, 1 demo only, 2 not ready."""
        return {"ready": 0, "demo_only": 1, "not_ready": 2}[self.value]

    @property
    def headline(self) -> str:
        return {
            "ready": "Ready.",
            "demo_only": "Ready for the demo only.",
            "not_ready": "Not ready.",
        }[self.value]


def verdict_for(checks: Iterable[DoctorCheck]) -> Verdict:
    """``not_ready`` if a failure blocks everything, ``demo_only`` if one blocks real
    use, else ``ready``. Warnings and information never change the verdict."""
    failing = [c for c in checks if c.state is HealthState.RED]
    if any(c.blocks == "all" for c in failing):
        return Verdict.NOT_READY
    if any(c.blocks == "use" for c in failing):
        return Verdict.DEMO_ONLY
    return Verdict.READY


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]
    verdict: Verdict
    #: Starter models Ollama does not have (what ``--fix`` would pull).
    missing_models: tuple[StarterModel, ...] = ()
    #: Where the vault master key stands (what ``--fix`` would do about it).
    key_status: vault_keys.MasterKeyStatus = field(
        default_factory=lambda: vault_keys.MasterKeyStatus("not_read")
    )
    ollama_url: str = ""
    #: The model set this report is sized to (what ``--fix`` pulls and applies).
    model_set: ModelSetChoice | None = None

    @property
    def fixable(self) -> bool:
        """Something ``--fix`` can do: pull a model, apply the model set, create a key."""
        return (
            bool(self.missing_models)
            or key_needs_fix(self.key_status)
            or (self.model_set is not None and self.model_set.apply_needed)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "exit_code": self.verdict.exit_code,
            "checks": [c.as_dict() for c in self.checks],
            "missing_models": [{"name": m.name, "size_gb": m.size_gb} for m in self.missing_models],
            "key": {"source": self.key_status.source, "detail": self.key_status.detail},
            "ollama_url": self.ollama_url,
            "fixable": self.fixable,
            "model_set": None if self.model_set is None else self.model_set.as_dict(),
        }


def key_needs_fix(status: vault_keys.MasterKeyStatus) -> bool:
    """Doctor can act: no key and nothing in the way (an invalid env key is the owner's;
    an unread keyring is read first by the fix itself)."""
    return status.source in ("absent", "no_keyring", "not_read")


# ── facts ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HostFacts:
    python_version: str
    system: str  # platform.system(), e.g. Darwin / Linux / Windows
    machine: str  # platform.machine(), e.g. arm64 / x86_64
    release: str  # platform.release(); "microsoft" in it = WSL
    ram_total_bytes: int


def host_facts() -> HostFacts:
    import psutil

    return HostFacts(
        python_version=platform.python_version(),
        system=platform.system(),
        machine=platform.machine(),
        release=platform.release(),
        ram_total_bytes=int(psutil.virtual_memory().total),
    )


@dataclass(frozen=True)
class OllamaFacts:
    url: str
    reachable: bool
    version: str | None = None
    models: frozenset[str] = frozenset()
    error: str | None = None


def ollama_root_url() -> str:
    """The Ollama the model calls go to (``OLLAMA_BASE_URL``, else localhost)."""
    from iris_harness.llm.tier_router import provider_root_url

    return provider_root_url("ollama")


def _get_json(client: Any, url: str, *, purpose: str) -> Any:
    log_egress(destination=urlparse(url).netloc, method="GET", kind="llm", purpose=purpose)
    resp = client.get(url, timeout=_OLLAMA_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def ollama_facts(client: Any, root: str) -> OllamaFacts:
    """Ollama's version and pulled models. Two cheap reads; loads no model."""
    try:
        version = _get_json(client, f"{root}/api/version", purpose="doctor").get("version")
        tags = _get_json(client, f"{root}/api/tags", purpose="doctor")
    except Exception as exc:  # noqa: BLE001 — any transport or payload error = unreachable
        return OllamaFacts(url=root, reachable=False, error=str(exc) or type(exc).__name__)
    names = {
        str(m.get("name") or m.get("model"))
        for m in (tags.get("models") or [])
        if isinstance(m, dict) and (m.get("name") or m.get("model"))
    }
    return OllamaFacts(
        url=root, reachable=True, version=str(version) if version else None, models=frozenset(names)
    )


def model_key(name: str) -> str:
    """Ollama's own name for a model: ``qwen2.5`` is ``qwen2.5:latest``."""
    return name if ":" in name else f"{name}:latest"


def disk_free_bytes(path: Path) -> tuple[Path, int]:
    """Free bytes on the volume ``path`` is (or would be) on; walks up to what exists."""
    probe = path.expanduser().resolve()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe, int(shutil.disk_usage(str(probe)).free)


def extra_installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def requires_python() -> str | None:
    """The supported Python range from the installed package's metadata (pyproject)."""
    try:
        return importlib.metadata.metadata(_DIST_NAME).get("Requires-Python")
    except importlib.metadata.PackageNotFoundError:
        return None


def configured_ollama_models(
    path: Path | None = None, overrides: Mapping[str, Mapping[str, Any]] | None = None
) -> tuple[str, ...]:
    """Every Ollama model ``llm_tiers.yaml`` names, in file order, deduplicated, with a
    model set's ``tiers`` (``overrides``) laid over the file's provider and model."""
    target = path or config_path("llm_tiers.yaml")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    seen: dict[str, None] = {}
    for name, cfg in (raw.get("tiers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        merged = {**cfg, **((overrides or {}).get(str(name)) or {})}
        if merged.get("provider", "ollama") == "ollama" and merged.get("model"):
            seen.setdefault(str(merged["model"]), None)
    return tuple(seen)


# ── checks (pure: facts in, a row out) ─────────────────────────────────────────


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in text.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _satisfies(version: str, spec: str) -> bool:
    """Does ``version`` meet a PEP 440 range like ``>=3.12,<3.14``? (Comparison
    operators only -- what a ``Requires-Python`` uses; no ``packaging`` dependency.)"""
    have = _version_tuple(version)
    for clause in (c.strip() for c in spec.split(",") if c.strip()):
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if clause.startswith(op):
                want = _version_tuple(clause[len(op) :])
                cut = have[: len(want)] if op in ("==", "!=") else have
                ok = {
                    ">=": cut >= want,
                    "<=": cut <= want,
                    ">": cut > want,
                    "<": cut < want,
                    "==": cut == want,
                    "!=": cut != want,
                }[op]
                if not ok:
                    return False
                break
    return True


def check_python(version: str, spec: str | None) -> DoctorCheck:
    if spec is None:
        return DoctorCheck(
            "Python",
            HealthState.YELLOW,
            f"Python {version}; the supported range is unknown (iris-harness is not installed)",
        )
    if _satisfies(version, spec):
        return DoctorCheck("Python", HealthState.GREEN, f"Python {version} (supported: {spec})")
    return DoctorCheck(
        "Python",
        HealthState.RED,
        f"Python {version} is outside the supported range {spec}",
        fix=f"Reinstall IRIS on a supported Python ({spec}): uv tool install --python <version>",
        blocks="all",
    )


def _is_wsl(facts: HostFacts) -> bool:
    return facts.system.lower() == "linux" and "microsoft" in facts.release.lower()


def check_platform(facts: HostFacts, config: DoctorConfig) -> DoctorCheck:
    system, machine = facts.system.lower(), facts.machine.lower()
    label = f"{facts.system} {facts.machine}"
    if system == "windows":
        return DoctorCheck(
            "Platform",
            HealthState.RED,
            f"{label}: native Windows is not supported",
            fix="Install WSL2 (wsl --install) and run IRIS inside it",
            blocks="all",
        )
    if machine in config.platforms.get(system, ()):
        if _is_wsl(facts):
            return DoctorCheck(
                "Platform",
                HealthState.GREEN,
                f"{label} (WSL2: no OS keyring, so the vault key lives in an env var)",
            )
        return DoctorCheck("Platform", HealthState.GREEN, label)
    return DoctorCheck(
        "Platform",
        HealthState.YELLOW,
        f"{label} is not a supported platform (Apple Silicon or Linux); it may still work",
    )


def check_ram(total_bytes: int, config: DoctorConfig, choice: ModelSetChoice) -> DoctorCheck:
    """Total RAM against the chosen model set. Rounded to whole GiB: a 16 GB Linux box
    reports ~15.6 GiB after the kernel's reservations, and it is a 16 GB machine."""
    gib = total_bytes / _GIB
    shown = f"{gib:.1f} GB RAM"
    if round(gib) < config.ram_demo_gb:
        return DoctorCheck(
            "Memory",
            HealthState.RED,
            f"{shown}: below the {config.ram_demo_gb:g} GB minimum",
            fix=f"Use a machine with {config.ram_floor_gb:g} GB or more",
            blocks="all",
        )
    if choice.fits:
        return DoctorCheck("Memory", HealthState.GREEN, f"{shown} ({choice.name} model set)")
    need = choice.model_set.ram_gb
    # A smaller set that is not part of the automatic choice (unmeasured yet) is still
    # the owner's to try; say so instead of only "buy a bigger machine".
    manual = [s for s in config.model_sets if not s.auto and round(gib) >= s.ram_gb]
    if manual and not choice.forced:
        fix = (
            f"iris doctor --model-set {manual[-1].name}  (the {manual[-1].name} set, "
            f"{manual[-1].ram_gb:g} GB; not yet measured on the email holdout)"
        )
    else:
        fix = f"Use a machine with {need:g} GB or more for real use"
    return DoctorCheck(
        "Memory",
        HealthState.RED,
        f"{shown}: enough for the demo, below the {need:g} GB the {choice.name} model set needs",
        fix=fix,
        blocks="use",
    )


def check_model_set(choice: ModelSetChoice) -> DoctorCheck:
    """Which set the report is sized to, and whether ``--fix`` has to write it.

    Information when the applied set is this one (or none is applied and this set is
    the file's tiers); a warning with the fix when another set is applied, or a forced
    set was never written.
    """
    detail = f"{choice.name}: {choice.reason}; {choice.model_set.describe_tiers()}"
    if choice.recorded is not None and choice.recorded.name != choice.name:
        return DoctorCheck(
            "Model set",
            HealthState.YELLOW,
            f"{detail}; applied: {choice.recorded.name}",
            fix=(
                f"iris doctor --fix  (moves the tiers to {choice.name}; "
                f"`iris doctor --model-set {choice.recorded.name}` keeps {choice.recorded.name})"
            ),
        )
    if choice.apply_needed:
        return DoctorCheck(
            "Model set",
            HealthState.YELLOW,
            f"{detail}; not applied yet",
            fix="iris doctor --fix  (writes the set's tiers as tier edits)",
        )
    applied = "applied" if choice.recorded is not None else "no set applied"
    return DoctorCheck("Model set", HealthState.GREY, f"{detail}; {applied}")


def check_disk(
    volume: Path,
    free_bytes: int,
    missing: tuple[StarterModel, ...],
    config: DoctorConfig,
) -> DoctorCheck:
    free_gb = free_bytes / 1e9
    need_gb = sum(m.size_gb for m in missing) + config.data_headroom_gb
    shown = f"{free_gb:.1f} GB free on {volume}"
    if free_gb < need_gb:
        return DoctorCheck(
            "Disk",
            HealthState.RED,
            f"{shown}; {need_gb:.1f} GB needed (missing models + data)",
            fix=f"Free at least {need_gb - free_gb:.1f} GB on that volume",
            blocks="use",
        )
    if free_gb < config.warn_free_gb:
        return DoctorCheck(
            "Disk",
            HealthState.YELLOW,
            f"{shown}; below {config.warn_free_gb:g} GB, mail stores and models grow",
        )
    return DoctorCheck("Disk", HealthState.GREEN, shown)


def check_home(home: Path, writable: Callable[[Path], bool] | None = None) -> DoctorCheck:
    """IRIS_HOME is writable, or can be created (nearest existing parent writable)."""
    is_writable = writable or (lambda p: os.access(p, os.W_OK))
    probe = home.expanduser()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if probe.exists() and probe.is_dir() and is_writable(probe):
        created = "" if probe == home.expanduser() else " (will be created)"
        return DoctorCheck("IRIS_HOME", HealthState.GREEN, f"{home} is writable{created}")
    return DoctorCheck(
        "IRIS_HOME",
        HealthState.RED,
        f"{home} is not writable",
        fix="Set IRIS_HOME to a directory you can write to",
        blocks="all",
    )


def check_ollama(facts: OllamaFacts) -> DoctorCheck:
    if not facts.reachable:
        return DoctorCheck(
            "Ollama",
            HealthState.RED,
            f"not reachable at {facts.url} ({facts.error})",
            fix="Install Ollama from https://ollama.com and run `ollama serve` "
            "(or set OLLAMA_BASE_URL)",
            blocks="use",
        )
    version = f"Ollama {facts.version}" if facts.version else "Ollama"
    return DoctorCheck("Ollama", HealthState.GREEN, f"{version} at {facts.url}")


def missing_starter_models(
    facts: OllamaFacts, starter: tuple[StarterModel, ...]
) -> tuple[StarterModel, ...]:
    """Starter models Ollama does not have (none known when it is unreachable)."""
    if not facts.reachable:
        return ()
    have = {model_key(n) for n in facts.models}
    return tuple(m for m in starter if model_key(m.name) not in have)


def check_starter_models(
    facts: OllamaFacts, starter: tuple[StarterModel, ...], missing: tuple[StarterModel, ...]
) -> DoctorCheck:
    names = ", ".join(m.name for m in starter)
    if not facts.reachable:
        return DoctorCheck(
            "Starter models", HealthState.GREY, f"not checked: Ollama is not reachable ({names})"
        )
    if not missing:
        return DoctorCheck("Starter models", HealthState.GREEN, f"pulled: {names}")
    size = sum(m.size_gb for m in missing)
    return DoctorCheck(
        "Starter models",
        HealthState.RED,
        f"missing: {', '.join(m.name for m in missing)} (~{size:.1f} GB to download)",
        fix="iris doctor --fix",
        blocks="use",
    )


def check_tier_models(
    facts: OllamaFacts, configured: tuple[str, ...], starter: tuple[StarterModel, ...]
) -> DoctorCheck | None:
    """Models ``llm_tiers.yaml`` routes to beyond the starter set, when not pulled.

    A warning, not a failure: R5 makes the starter set the install's promise. Until the
    models-by-capability change narrows the tiers to it, intents routed to these tiers
    fail until their models are pulled, and the owner should know that.
    """
    if not facts.reachable:
        return None
    have = {model_key(n) for n in facts.models} | {model_key(m.name) for m in starter}
    absent = [m for m in configured if model_key(m) not in have]
    if not absent:
        return None
    return DoctorCheck(
        "Tier models",
        HealthState.YELLOW,
        f"llm_tiers.yaml also routes to {len(absent)} model(s) not pulled: {', '.join(absent)}",
        fix=" && ".join(f"ollama pull {m}" for m in absent),
    )


def check_vault_key(
    status: vault_keys.MasterKeyStatus, audit_state: AuditKeyState = "unresolved"
) -> DoctorCheck:
    """The master key every governed tool call needs (#741).

    ``audit_state`` is this process's own answer (``audit_key_status``): a process that
    already resolved the key (the API) needs no further look.
    """
    if audit_state == "ready" or status.present:
        where = "resolved by this process" if audit_state == "ready" else status.detail
        return DoctorCheck("Vault key", HealthState.GREEN, f"master key present ({where})")
    if status.source == "invalid":
        return DoctorCheck(
            "Vault key",
            HealthState.RED,
            status.detail,
            fix="Unset IRIS_VAULT_MASTER_KEY or set it to the valid Fernet key your vault uses",
            blocks="use",
        )
    if status.source == "not_read" and audit_state != "unavailable":
        return DoctorCheck(
            "Vault key",
            HealthState.YELLOW,
            "an OS keyring is present but was not read (non-interactive run)",
            fix="Run `iris doctor` in a terminal, or set IRIS_VAULT_MASTER_KEY",
        )
    how = "prints an export line" if status.source == "no_keyring" else "stores one in the keyring"
    return DoctorCheck(
        "Vault key",
        HealthState.RED,
        f"no master key: governed tool calls are refused ({status.detail})",
        fix=f"iris doctor --fix  ({how})",
        blocks="use",
    )


def check_extra(extra: OptionalExtra, installed: bool) -> DoctorCheck:
    state = (
        "installed" if installed else f'not installed (pip install "iris-harness[{extra.name}]")'
    )
    return DoctorCheck(f"Extra: {extra.name}", HealthState.GREY, f"{extra.purpose}: {state}")


# ── the run ─────────────────────────────────────────────────────────────────────


def http_client() -> Any:
    """The HTTP client doctor talks to Ollama with (one place for a test to replace)."""
    import httpx

    return httpx.Client()


def run_doctor(
    *,
    read_keyring: bool,
    config: DoctorConfig | None = None,
    client: Any = None,
    home: Path | None = None,
    audit_state: AuditKeyState | None = None,
    model_set: str | None = None,
) -> DoctorReport:
    """Build the preflight report. Reads only; installs, pulls and stores nothing.

    ``read_keyring=False`` keeps the OS keyring closed (a Keychain dialog must not open
    in a script or a server). ``client`` is an ``httpx.Client``-like object, else
    :func:`http_client`. ``model_set`` forces a set by name (``KeyError`` for an
    unknown one); otherwise the host picks (:func:`choose_model_set`). Each fact comes
    from one module-level reader (``host_facts``, ``disk_free_bytes``,
    ``requires_python``, ``recorded_model_set``, ...), which is what a test replaces.
    """
    cfg = config or load_doctor_config()
    facts = host_facts()
    home_dir = home or iris_home()
    spec = requires_python()
    if audit_state is None:
        audit_state = audit_key_status()[0]
    choice = choose_model_set(
        cfg, facts.ram_total_bytes, force=model_set, recorded=recorded_model_set()
    )
    starter = choice.model_set.starter_models

    root = ollama_root_url()
    if client is None:
        with http_client() as own:
            ollama = ollama_facts(own, root)
    else:
        ollama = ollama_facts(client, root)
    missing = missing_starter_models(ollama, starter)
    key_status = vault_keys.master_key_status(read_keyring=read_keyring)
    volume, free = disk_free_bytes(home_dir)

    checks: list[DoctorCheck] = [
        check_python(facts.python_version, spec),
        check_platform(facts, cfg),
        check_ram(facts.ram_total_bytes, cfg, choice),
        check_model_set(choice),
        check_disk(volume, free, missing, cfg),
        check_home(home_dir),
        check_vault_key(key_status, audit_state),
        check_ollama(ollama),
        check_starter_models(ollama, starter, missing),
    ]
    tier_row = check_tier_models(
        ollama,
        configured_ollama_models(overrides=choice.model_set.tiers),
        starter,
    )
    if tier_row is not None:
        checks.append(tier_row)
    checks.extend(check_extra(e, extra_installed(e.module)) for e in cfg.optional_extras)
    return DoctorReport(
        checks=tuple(checks),
        verdict=verdict_for(checks),
        missing_models=missing,
        key_status=key_status,
        ollama_url=root,
        model_set=choice,
    )


# ── safe fixes ──────────────────────────────────────────────────────────────────


class DoctorFixError(RuntimeError):
    """A fix could not complete; the message says why."""


@dataclass(frozen=True)
class PullProgress:
    status: str
    completed: int | None = None
    total: int | None = None


def pull_model(
    name: str,
    *,
    client: Any,
    root: str | None = None,
    on_progress: Callable[[PullProgress], None] | None = None,
) -> None:
    """Pull ``name`` through Ollama's ``/api/pull`` stream. Raises :class:`DoctorFixError`."""
    base = root or ollama_root_url()
    url = f"{base}/api/pull"
    log_egress(destination=urlparse(url).netloc, method="POST", kind="llm", purpose="doctor-pull")
    try:
        with client.stream(
            "POST", url, json={"model": name, "stream": True}, timeout=_PULL_TIMEOUT_S
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise DoctorFixError(f"pull {name}: HTTP {resp.status_code} {resp.text[:200]}")
            last = ""
            for line in resp.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise DoctorFixError(f"pull {name}: {event['error']}")
                last = str(event.get("status", ""))
                if on_progress is not None:
                    on_progress(PullProgress(last, event.get("completed"), event.get("total")))
    except DoctorFixError:
        raise
    except Exception as exc:  # transport/JSON errors become one fix error
        raise DoctorFixError(f"pull {name}: {exc}") from exc
    if last != "success":
        raise DoctorFixError(f"pull {name}: the stream ended without success ({last or 'empty'})")


def apply_model_set(choice: ModelSetChoice, *, actor: str = "doctor") -> str:
    """Write the chosen set's tiers as tier edits (ADR-0120) and record the set.

    Loads the router the way the runtime does (``llm_tiers.yaml`` plus the saved edits)
    so the diff against the file is the one the app would compute. Returns one line
    saying what changed. Raises :class:`DoctorFixError` when a tier the set names is
    not in the file.
    """
    from iris_harness.llm.tier_router import TierRouter

    store = settings_store()
    router = TierRouter.load_from_yaml(config_path("llm_tiers.yaml"), settings=store)
    try:
        tier_edits.apply_model_set(
            router,
            store,
            choice.name,
            choice.model_set.tiers,
            forced=choice.forced,
            actor=actor,
        )
    except KeyError as exc:
        raise DoctorFixError(
            f"model set {choice.name}: llm_tiers.yaml has no tier {exc.args[0]!r}"
        ) from exc
    except tier_edits.TierEditError as exc:
        raise DoctorFixError(f"model set {choice.name}: {exc}") from exc
    return f"model set {choice.name} applied: {choice.model_set.describe_tiers()}"


KeyFixOutcome = Literal["kept", "stored", "export", "invalid"]

#: Where a key printed for an env var should be kept (no keyring on this host).
PERSIST_HINT = (
    "No OS keyring here (WSL2, headless Linux, containers), so the key lives in an env "
    "var. Add the export line to your shell profile (~/.bashrc, ~/.zshrc or ~/.profile) "
    "and to the environment of whatever runs IRIS (a systemd Environment= line, Docker "
    "-e or env_file). Keep a copy somewhere safe: vault secrets cannot be read without it."
)


@dataclass(frozen=True)
class KeyFix:
    outcome: KeyFixOutcome
    detail: str
    export_line: str | None = None


def fix_master_key(
    *,
    generate: Callable[[], str] = vault_keys.generate_master_key,
) -> KeyFix:
    """Make sure a master key exists, never replacing one.

    Reads the keyring (the owner asked for the fix, so a Keychain prompt here is theirs
    to answer). A key already there, in the env or the keyring, is kept. With a working
    keyring a new key is stored in it; without one the key is returned as an export line
    for the owner to persist (:data:`PERSIST_HINT`).
    """
    status = vault_keys.master_key_status(read_keyring=True)
    if status.present:
        return KeyFix("kept", f"a master key is already set ({status.detail}); left as is")
    if status.source == "invalid":
        return KeyFix("invalid", status.detail)
    key = generate()
    if status.source == "absent":
        try:
            vault_keys.store_master_key_in_keyring(key)
        except vault_keys.MasterKeyExistsError:
            return KeyFix("kept", "a master key appeared in the OS keyring; left as is")
        except vault_keys.VaultMasterKeyUnavailableError:
            pass  # the keyring refused the write: fall through to the export line
        else:
            return KeyFix("stored", "a new master key is stored in the OS keyring")
    return KeyFix(
        "export",
        PERSIST_HINT,
        export_line=f"export {vault_keys.MASTER_KEY_ENV}={key}",
    )


__all__ = [
    "PERSIST_HINT",
    "DoctorCheck",
    "DoctorConfig",
    "DoctorConfigError",
    "DoctorFixError",
    "DoctorReport",
    "HostFacts",
    "KeyFix",
    "ModelSet",
    "ModelSetChoice",
    "OllamaFacts",
    "OptionalExtra",
    "PullProgress",
    "StarterModel",
    "Verdict",
    "apply_model_set",
    "check_disk",
    "check_extra",
    "check_home",
    "check_model_set",
    "check_ollama",
    "check_platform",
    "check_python",
    "check_ram",
    "check_starter_models",
    "check_tier_models",
    "check_vault_key",
    "choose_model_set",
    "fix_master_key",
    "http_client",
    "key_needs_fix",
    "load_doctor_config",
    "missing_starter_models",
    "ollama_facts",
    "pull_model",
    "recorded_model_set",
    "run_doctor",
    "settings_store",
    "verdict_for",
]
