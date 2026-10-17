"""``iris doctor``: the render, the verdict's exit code, and ``--fix`` (models + key).

Ollama is an ``httpx.MockTransport`` behind ``doctor.http_client``; the keyring is the
suite's in-memory one. CliRunner's stdin is not a terminal, so the command runs as a
script would unless a test says otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import keyring
import pytest
from keyring.backends import fail
from typer.testing import CliRunner

from iris_harness.cli import doctor as doctor_cli
from iris_harness.kernel.governance.vault import keys as vault_keys
from iris_harness.main import app
from iris_harness.services.system import doctor as dr

runner = CliRunner()
_GIB = 1024**3


class FakeOllama:
    """Version, tags, and a pull that adds the model to the tags."""

    def __init__(self, models: list[str]) -> None:
        self.models = list(models)
        self.pulls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.7"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if path == "/api/pull":
            name = json.loads(request.content)["model"]
            self.pulls.append(name)
            self.models.append(name)
            body = [{"status": "pulling manifest"}, {"status": "success"}]
            return httpx.Response(200, content="\n".join(json.dumps(b) for b in body).encode())
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    from iris_harness.foundation.settings import SettingsStore

    state: dict[str, Any] = {
        "ollama": FakeOllama(["qwen2.5:7b-instruct"]),
        "config": dr.DoctorConfig(
            ram_demo_gb=8,
            platforms={"darwin": ("arm64",)},
            data_headroom_gb=2,
            warn_free_gb=20,
            model_sets=(
                # The lite tiers name a tier the shipped llm_tiers.yaml has: --fix
                # writes them against the real file.
                dr.ModelSet(
                    "lite",
                    8,
                    (dr.StarterModel("tiny:1b", 0.8),),
                    tiers={"tier1": {"model": "tiny:1b"}},
                    auto=False,
                ),
                dr.ModelSet(
                    "standard",
                    16,
                    (dr.StarterModel("qwen2.5:7b-instruct", 4.7), dr.StarterModel("tiny:1b", 0.8)),
                ),
            ),
        ),
        "settings_db": tmp_path / "settings.db",
    }
    host = dr.HostFacts("3.12.4", "Darwin", "arm64", "24.0.0", 32 * _GIB)
    monkeypatch.setattr(dr, "host_facts", lambda: host)
    monkeypatch.setattr(dr, "disk_free_bytes", lambda p: (tmp_path, 100 * 10**9))
    monkeypatch.setattr(dr, "requires_python", lambda: ">=3.12,<3.14")
    monkeypatch.setattr(dr, "configured_ollama_models", lambda overrides=None: ())
    monkeypatch.setattr(dr, "extra_installed", lambda module: True)
    monkeypatch.setattr(dr, "load_doctor_config", lambda path=None: state["config"])
    monkeypatch.setattr(dr, "http_client", lambda: state["ollama"].client())
    monkeypatch.setattr(dr, "settings_store", lambda: SettingsStore(db_path=state["settings_db"]))
    monkeypatch.setattr(doctor_cli, "_interactive", lambda: False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    monkeypatch.delenv(vault_keys.MASTER_KEY_ENV, raising=False)
    return state


def _entries() -> dict[tuple[str, str], str]:
    return keyring.get_keyring().entries  # type: ignore[attr-defined,no-any-return]


def test_ready_exits_zero(env: dict[str, Any]) -> None:
    env["ollama"].models.append("tiny:1b")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "Ready." in result.output
    assert "pass" in result.output


def test_missing_model_is_demo_only_and_nothing_is_pulled(env: dict[str, Any]) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "Ready for the demo only." in result.output
    assert "tiny:1b" in result.output
    assert "iris doctor --fix" in result.output
    assert env["ollama"].pulls == []


def test_fix_yes_pulls_only_the_missing_model_and_shows_its_size(env: dict[str, Any]) -> None:
    result = runner.invoke(app, ["doctor", "--fix", "--yes"])
    assert env["ollama"].pulls == ["tiny:1b"]  # the present starter model is not pulled
    assert "~0.8 GB" in result.output
    assert result.exit_code == 0, result.output  # the re-run after the fix is ready


def test_fix_without_yes_in_a_script_changes_nothing(env: dict[str, Any]) -> None:
    result = runner.invoke(app, ["doctor", "--fix"])
    assert "pass --yes" in result.output
    assert env["ollama"].pulls == []
    assert result.exit_code == 1


def test_interactive_fix_asks_per_fix(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_cli, "_interactive", lambda: True)
    # "Apply the safe fixes now?" yes, then "Pull tiny:1b?" no.
    result = runner.invoke(app, ["doctor"], input="y\nn\n")
    assert "Pull tiny:1b (~0.8 GB download)?" in result.output
    assert env["ollama"].pulls == []
    assert result.exit_code == 1


def test_no_key_without_a_keyring_prints_the_export_line(env: dict[str, Any]) -> None:
    env["ollama"].models.append("tiny:1b")
    keyring.set_keyring(fail.Keyring())
    result = runner.invoke(app, ["doctor", "--fix", "--yes"])
    lines = result.output.splitlines()
    exports = [ln for ln in lines if ln.startswith("export IRIS_VAULT_MASTER_KEY=")]
    assert len(exports) == 1
    assert vault_keys.is_fernet_key(exports[0].split("=", 1)[1])
    assert "shell profile" in result.output
    # This process still has no key until the owner exports it; the demo still runs (#246).
    assert result.exit_code == 1


def test_no_key_with_a_keyring_stores_one(env: dict[str, Any]) -> None:
    env["ollama"].models.append("tiny:1b")
    _entries().clear()
    result = runner.invoke(app, ["doctor", "--fix", "--yes"])
    assert "stored in the OS keyring" in result.output
    assert vault_keys.is_fernet_key(_entries()[("iris-vault", "master-key")])
    assert result.exit_code == 0, result.output


def test_existing_key_is_never_overwritten(env: dict[str, Any]) -> None:
    before = dict(_entries())
    runner.invoke(app, ["doctor", "--fix", "--yes"])
    assert _entries() == before


def test_a_script_run_never_reads_the_keyring(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env["ollama"].models.append("tiny:1b")

    def forbidden(service: str, username: str) -> str:
        raise AssertionError("a non-interactive report read the keyring")

    monkeypatch.setattr(keyring.get_keyring(), "get_password", forbidden)
    result = runner.invoke(app, ["doctor"])
    assert "was not read" in result.output
    assert result.exit_code == 0


def test_json_output(env: dict[str, Any]) -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    body = json.loads(result.output)
    assert body["verdict"] == "demo_only"
    assert body["missing_models"] == [{"name": "tiny:1b", "size_gb": 0.8}]
    assert body["model_set"]["name"] == "standard"
    assert result.exit_code == 1
    assert env["ollama"].pulls == []


# ── model sets ──────────────────────────────────────────────────────────────────


def _store(env: dict[str, Any]) -> Any:
    from iris_harness.foundation.settings import SettingsStore

    return SettingsStore(db_path=env["settings_db"])


def test_the_report_names_the_set_the_host_fits(env: dict[str, Any]) -> None:
    result = runner.invoke(app, ["doctor"])
    assert "Model set" in result.output
    assert "standard: the largest set" in result.output
    assert _store(env).section("llm_tiers") == {}  # a plain run writes nothing


def test_forcing_lite_pulls_its_model_and_writes_its_tiers(env: dict[str, Any]) -> None:
    from iris_harness.llm import tier_edits

    result = runner.invoke(app, ["doctor", "--model-set", "lite", "--fix", "--yes"])
    assert result.exit_code == 0, result.output
    assert env["ollama"].pulls == ["tiny:1b"]
    assert "model set lite applied: tier1 -> tiny:1b" in result.output
    store = _store(env)
    assert store.get("llm_tiers", "tier:tier1") == {"model": "tiny:1b"}
    assert tier_edits.recorded_model_set(store) == tier_edits.RecordedModelSet(
        "lite", True, ("tier1",)
    )
    # The re-run after the fix sees the set as applied: nothing left to fix.
    assert "not applied yet" not in result.output.split("Ready.")[-1]


def test_a_plain_run_after_forcing_lite_warns_and_fix_moves_back(env: dict[str, Any]) -> None:
    from iris_harness.llm import tier_edits

    runner.invoke(app, ["doctor", "--model-set", "lite", "--fix", "--yes"])
    env["ollama"].models.append("qwen2.5:7b-instruct")
    plain = runner.invoke(app, ["doctor"])
    assert "applied: lite" in plain.output
    assert "--model-set lite" in plain.output  # how to keep it
    assert plain.exit_code == 0  # a warning never changes the verdict

    back = runner.invoke(app, ["doctor", "--fix", "--yes"])
    assert back.exit_code == 0, back.output
    assert "model set standard applied" in back.output
    store = _store(env)
    assert store.get("llm_tiers", "tier:tier1") is None
    assert tier_edits.recorded_model_set(store) == tier_edits.RecordedModelSet(
        "standard", False, ()
    )


def test_interactive_fix_asks_before_writing_the_set(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor_cli, "_interactive", lambda: True)
    # "Apply the safe fixes now?" yes; "Apply the lite model set?" no; "Pull tiny:1b?" no.
    result = runner.invoke(app, ["doctor", "--model-set", "lite"], input="y\nn\nn\n")
    assert "Apply the lite model set (tier1 -> tiny:1b)?" in result.output
    assert _store(env).section("llm_tiers") == {}
    assert env["ollama"].pulls == []


def test_an_unknown_set_is_a_usage_error(env: dict[str, Any]) -> None:
    result = runner.invoke(app, ["doctor", "--model-set", "huge"])
    assert result.exit_code == 2
    # Rich wraps the error panel and puts its border between the lines.
    flat = " ".join(result.output.replace("│", " ").split())
    assert "no model set 'huge'" in flat
    assert "doctor.yaml has: lite, standard" in flat
