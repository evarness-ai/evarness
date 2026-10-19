"""setup-windows.ps1: the one-command Windows setup (Evarness in Docker, models on Ollama for Windows).

Each property below broke a real Windows install or would, and none fails loudly: the
script still runs, then Evarness does not answer. Structural checks only: CI has neither
Windows nor Docker. The script itself was run end to end on Windows 11.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from evarness.services.system import doctor

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "setup-windows.ps1"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _up_line() -> str:
    lines = [line for line in _script().splitlines() if re.search(r"Invoke-Compose up\b", line)]
    assert len(lines) == 1, lines
    return lines[0]


def test_the_script_is_ascii_so_windows_powershell_reads_it() -> None:
    # Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page.
    assert _script().isascii()


def test_it_never_starts_the_bundled_ollama() -> None:
    """Ollama for Windows holds port 11434; the bundled one then fails to bind."""
    script = _script()
    assert '"-f", "docker-compose.yml", "-f", "docker-compose.hostllm.yml"' in script
    up = _up_line()
    assert "--no-deps" in up and up.rstrip().endswith("governor evarness-api")


def test_it_runs_the_image_it_just_built() -> None:
    """./config is mounted from the checkout: an old image on new config crashed doctor,
    and Compose kept the old container after a rebuild of the shared evarness:latest tag."""
    up = _up_line()
    assert "--build" in up and "--force-recreate" in up


def test_the_model_set_comes_from_doctor_not_the_script() -> None:
    script = _script()
    assert '"evarness", "doctor", "--fix", "--yes"' in script
    shipped = yaml.safe_load((ROOT / "config" / "llm_tiers.yaml").read_text(encoding="utf-8"))
    models = {str(t["model"]) for t in shipped["tiers"].values() if t.get("model")}
    for model_set in doctor.load_doctor_config().model_sets:
        models |= {m.name for m in model_set.starter_models}
    assert not [m for m in models if m in script], "a model name is hard-coded in the script"


def test_it_sizes_the_model_set_for_the_machine_the_models_run_on() -> None:
    assert f'"{doctor.MODEL_HOST_RAM_ENV}"' in _script()


def test_env_is_written_as_ascii_and_keeps_existing_secrets() -> None:
    script = _script()
    assert "-Encoding ascii" in script  # PowerShell's default UTF-16 is unreadable to Compose
    for name in ("EVARNESS_AUTH_SECRET", "EVARNESS_VAULT_MASTER_KEY"):
        assert f'if (-not (Get-EnvValue $envLines "{name}"))' in script


def test_it_lets_ollama_use_an_integrated_gpu_only_when_ollama_skipped_one() -> None:
    script = _script()
    assert "dropping integrated GPU" in script  # Ollama's own log line, not a name guess
    assert '"OLLAMA_IGPU_ENABLE", "1", "User"' in script
