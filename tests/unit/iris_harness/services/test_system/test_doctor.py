"""``iris doctor``'s report (``services/system/doctor.py``): each check from mocked
facts, the verdict, the key paths and the model pull. Ollama is an ``httpx.MockTransport``;
the keyring is the suite's in-memory one. Nothing touches the network or the OS keyring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import keyring
import pytest
from cryptography.fernet import Fernet
from keyring.backends import fail

from iris_harness.kernel.governance.vault import keys as vault_keys
from iris_harness.services.health.models import HealthState
from iris_harness.services.system import doctor as dr

_GIB = 1024**3
_STARTER = (dr.StarterModel("qwen2.5:7b-instruct", 4.7),)
_LITE_STARTER = (dr.StarterModel("tiny:1b", 0.8),)
# lite is forced-only (auto=False), as the shipped file has it until the holdout run.
_LITE = dr.ModelSet(
    "lite", 8, _LITE_STARTER, tiers={"email_judge": {"model": "tiny:1b"}}, auto=False
)
_STANDARD = dr.ModelSet("standard", 16, _STARTER)
_CONFIG = dr.DoctorConfig(
    ram_demo_gb=8,
    platforms={"darwin": ("arm64",), "linux": ("x86_64", "aarch64")},
    data_headroom_gb=2,
    warn_free_gb=20,
    model_sets=(_LITE, _STANDARD),
    optional_extras=(dr.OptionalExtra("ml", "sentence_transformers", "semantic discovery"),),
)


def _choice(gib: float, **kw: Any) -> dr.ModelSetChoice:
    return dr.choose_model_set(_CONFIG, int(gib * _GIB), **kw)


def _host(**overrides: Any) -> dr.HostFacts:
    base: dict[str, Any] = {
        "python_version": "3.12.4",
        "system": "Darwin",
        "machine": "arm64",
        "release": "24.0.0",
        "ram_total_bytes": 16 * _GIB,
    }
    base.update(overrides)
    return dr.HostFacts(**base)


def _ollama(models: list[str], *, pulls: list[str] | None = None) -> httpx.Client:
    """A mock Ollama: version + tags, and a pull stream that records what it pulled."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.7"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if request.url.path == "/api/pull":
            name = json.loads(request.content)["model"]
            if pulls is not None:
                pulls.append(name)
            lines = [
                {"status": "pulling manifest"},
                {"status": "pulling abc", "total": 100, "completed": 50},
                {"status": "pulling abc", "total": 100, "completed": 100},
                {"status": "success"},
            ]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _ollama_down() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def facts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Healthy defaults for every fact reader; a test changes the one it is about."""
    state: dict[str, Any] = {
        "host": _host(),
        "free": 100 * 10**9,
        "spec": ">=3.12,<3.14",
        "tiers": ("qwen2.5:7b-instruct",),
        "recorded": None,
    }
    monkeypatch.setattr(dr, "host_facts", lambda: state["host"])
    monkeypatch.setattr(dr, "disk_free_bytes", lambda p: (tmp_path, state["free"]))
    monkeypatch.setattr(dr, "requires_python", lambda: state["spec"])
    monkeypatch.setattr(dr, "configured_ollama_models", lambda overrides=None: state["tiers"])
    monkeypatch.setattr(dr, "recorded_model_set", lambda: state["recorded"])
    monkeypatch.setattr(dr, "extra_installed", lambda module: False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    monkeypatch.delenv(vault_keys.MASTER_KEY_ENV, raising=False)
    return state


def _run(tmp_path: Path, client: httpx.Client, **kw: Any) -> dr.DoctorReport:
    kw.setdefault("read_keyring", True)
    kw.setdefault("audit_state", "unresolved")
    return dr.run_doctor(config=_CONFIG, client=client, home=tmp_path / "home", **kw)


def _row(report: dr.DoctorReport, name: str) -> dr.DoctorCheck:
    return next(c for c in report.checks if c.name == name)


# ── config ──────────────────────────────────────────────────────────────────────


def test_shipped_config_has_a_lite_and_a_standard_set() -> None:
    cfg = dr.load_doctor_config()
    assert [s.name for s in cfg.model_sets] == ["lite", "standard"]
    standard = cfg.model_set("standard")
    assert standard is not None
    assert [m.name for m in standard.starter_models] == ["qwen2.5:7b-instruct"]
    assert standard.tiers == {}
    lite = cfg.model_set("lite")
    assert lite is not None
    assert lite.auto  # measured on the holdout and the tier 2 suite (ADR decision 7)
    # Only tier 2 moves, onto the shipped judge's model: every smaller judge failed the
    # holdout, and the 1B router cost scenarios on the tier 2 suite.
    assert set(lite.tiers) == {"tier2"}
    assert lite.tiers["tier2"]["model"] == "qwen3.5:4b-q4_K_M"
    assert lite.tiers["tier2"]["think"] is False
    # Every model the lite tiers point at, and the shipped judge, is in the lite starter set.
    lite_starters = {m.name for m in lite.starter_models}
    assert {f["model"] for f in lite.tiers.values()} <= lite_starters
    assert "qwen3.5:4b-q4_K_M" in lite_starters
    assert cfg.ram_floor_gb == 8  # the smallest automatic set
    assert cfg.ram_demo_gb == 8
    assert {"email", "ml"} <= {e.name for e in cfg.optional_extras}


def test_shipped_lite_tiers_exist_in_llm_tiers_yaml() -> None:
    """A set that names a tier the file lacks would fail only at ``--fix``."""
    import yaml

    from iris_harness.foundation.paths import config_path

    tiers = yaml.safe_load(config_path("llm_tiers.yaml").read_text(encoding="utf-8"))["tiers"]
    for model_set in dr.load_doctor_config().model_sets:
        assert set(model_set.tiers) <= set(tiers), model_set.name


_BASE_YAML = "ram: {demo_gb: 8}\nplatforms: {linux: [x86_64]}\ndisk: {data_headroom_gb: 2, warn_free_gb: 20}\n"


@pytest.mark.parametrize(
    ("sets", "message"),
    [
        ("", "model_sets is required"),
        ("model_sets: {a: {fits_when: {ram_gb: 8}, starter_models: []}}", "starter_models"),
        ("model_sets: {a: {starter_models: [{name: x, size_gb: 1}]}}", "fits_when.ram_gb"),
        (
            "model_sets: {a: {fits_when: {ram_gb: 8, vram_gb: 4}, "
            "starter_models: [{name: x, size_gb: 1}]}}",
            "unknown keys",
        ),
        (
            "model_sets: {a: {fits_when: {ram_gb: 8}, starter_models: [{name: x, size_gb: 1}], "
            "tiers: {tier1: {num_ctx: 4096}}}}",
            "tiers.tier1",
        ),
        (
            "model_sets:\n"
            "  big: {fits_when: {ram_gb: 16}, starter_models: [{name: x, size_gb: 1}]}\n"
            "  small: {fits_when: {ram_gb: 8}, starter_models: [{name: x, size_gb: 1}]}\n",
            "smallest to largest",
        ),
        (
            "model_sets: {a: {fits_when: {ram_gb: 8}, auto: false, "
            "starter_models: [{name: x, size_gb: 1}]}}",
            "automatic",
        ),
    ],
)
def test_config_errors_name_what_is_wrong(tmp_path: Path, sets: str, message: str) -> None:
    path = tmp_path / "doctor.yaml"
    path.write_text(_BASE_YAML + sets)
    with pytest.raises(dr.DoctorConfigError, match=message):
        dr.load_doctor_config(path)


def test_config_without_a_threshold_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "doctor.yaml"
    path.write_text(
        "platforms: {linux: [x86_64]}\n"
        "model_sets: {a: {fits_when: {ram_gb: 8}, starter_models: [{name: x, size_gb: 1}]}}\n"
    )
    with pytest.raises(dr.DoctorConfigError, match="ram.demo_gb"):
        dr.load_doctor_config(path)


# ── the model set ───────────────────────────────────────────────────────────────


def test_the_largest_automatic_set_the_host_fits_is_chosen() -> None:
    big = _choice(32)
    assert big.name == "standard" and big.fits and not big.forced
    assert "32.0 GB RAM fits" in big.reason
    assert _choice(15.6).name == "standard"  # a 16 GB Linux box
    small = _choice(8)
    assert small.name == "standard" and not small.fits  # lite is not automatic
    assert "no set fits" in small.reason and "lite" not in small.reason


def test_a_forced_set_is_taken_as_named() -> None:
    lite = _choice(8, force="lite")
    assert lite.name == "lite" and lite.fits and lite.forced
    too_big = _choice(8, force="standard")
    assert not too_big.fits and "needs 16 GB" in too_big.reason
    with pytest.raises(KeyError):
        _choice(8, force="huge")


def test_apply_needed_follows_the_recorded_set() -> None:
    from iris_harness.llm.tier_edits import RecordedModelSet

    assert not _choice(32).apply_needed  # standard = the file's tiers, nothing recorded
    assert _choice(8, force="lite").apply_needed  # lite changes tiers, nothing recorded
    applied = RecordedModelSet("lite", True, ("email_judge",))
    assert not _choice(8, force="lite", recorded=applied).apply_needed
    assert _choice(32, recorded=applied).apply_needed  # back to standard: reset lite's tiers


def test_model_set_row_states() -> None:
    from iris_harness.llm.tier_edits import RecordedModelSet

    info = dr.check_model_set(_choice(32))
    assert info.state is HealthState.GREY
    assert "standard: the largest set" in info.detail and "the shipped tiers" in info.detail
    pending = dr.check_model_set(_choice(8, force="lite"))
    assert pending.state is HealthState.YELLOW
    assert "email_judge -> tiny:1b" in pending.detail and "not applied yet" in pending.detail
    assert "--fix" in (pending.fix or "")
    moved = dr.check_model_set(_choice(32, recorded=RecordedModelSet("lite", True, ())))
    assert moved.state is HealthState.YELLOW
    assert "applied: lite" in moved.detail
    assert "--model-set lite" in (moved.fix or "")
    done = dr.check_model_set(_choice(8, force="lite", recorded=RecordedModelSet("lite", True)))
    assert done.state is HealthState.GREY
    assert done.detail.endswith("applied")


# ── each check ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "state"),
    [("3.12.1", HealthState.GREEN), ("3.13.0", HealthState.GREEN), ("3.14.0", HealthState.RED)],
)
def test_python_range(version: str, state: HealthState) -> None:
    check = dr.check_python(version, ">=3.12,<3.14")
    assert check.state is state
    if state is HealthState.RED:
        assert check.blocks == "all"
        assert check.fix


def test_python_old_version_fails() -> None:
    assert dr.check_python("3.11.9", ">=3.12,<3.14").state is HealthState.RED


def test_python_range_unknown_warns() -> None:
    assert dr.check_python("3.12.1", None).state is HealthState.YELLOW


def test_platforms() -> None:
    assert dr.check_platform(_host(), _CONFIG).state is HealthState.GREEN
    intel_mac = dr.check_platform(_host(machine="x86_64"), _CONFIG)
    assert intel_mac.state is HealthState.YELLOW
    # Native Windows is for development: a warning that points at Docker, never a block.
    windows = dr.check_platform(_host(system="Windows", machine="AMD64"), _CONFIG)
    assert windows.state is HealthState.YELLOW
    assert windows.blocks == "none"
    assert "Docker" in (windows.fix or "")
    assert "file modes are not enforced" in windows.detail
    assert "audit locks" not in windows.detail  # they hold on Windows (file_lock.py)
    wsl = dr.check_platform(
        _host(system="Linux", machine="x86_64", release="5.15.0-microsoft-standard-WSL2"),
        _CONFIG,
    )
    assert wsl.state is HealthState.GREEN
    assert "WSL2" in wsl.detail


@pytest.mark.parametrize(
    ("gib", "state", "blocks"),
    [
        (32.0, HealthState.GREEN, "none"),
        (15.6, HealthState.GREEN, "none"),  # a 16 GB Linux box after kernel reservations
        (8.0, HealthState.RED, "use"),  # demo only
        (4.0, HealthState.RED, "all"),
    ],
)
def test_ram_floor(gib: float, state: HealthState, blocks: str) -> None:
    check = dr.check_ram(int(gib * _GIB), _CONFIG, _choice(gib))
    assert check.state is state
    assert check.blocks == blocks


def test_ram_short_of_the_set_points_at_the_smaller_set_or_a_bigger_machine() -> None:
    eight = int(8 * _GIB)
    short = dr.check_ram(eight, _CONFIG, _choice(8))
    assert "below the 16 GB the standard model set needs" in short.detail
    assert "iris doctor --model-set lite" in (short.fix or "")
    forced = dr.check_ram(eight, _CONFIG, _choice(8, force="standard"))
    assert "Use a machine with 16 GB" in (forced.fix or "")
    fits = dr.check_ram(eight, _CONFIG, _choice(8, force="lite"))
    assert fits.state is HealthState.GREEN
    assert "lite model set" in fits.detail


def test_configured_models_take_the_set_overrides(tmp_path: Path) -> None:
    path = tmp_path / "llm_tiers.yaml"
    path.write_text(
        "tiers:\n"
        "  a: {provider: ollama, model: big:7b}\n"
        "  b: {provider: lmstudio, model: x/y}\n"
        "  c: {provider: ollama, model: judge:4b}\n"
    )
    assert dr.configured_ollama_models(path) == ("big:7b", "judge:4b")
    overrides = {"a": {"model": "small:1b"}, "b": {"provider": "ollama", "model": "z:1b"}}
    assert dr.configured_ollama_models(path, overrides) == ("small:1b", "z:1b", "judge:4b")


def test_disk_needs_missing_models_plus_headroom(tmp_path: Path) -> None:
    missing = _STARTER
    assert dr.check_disk(tmp_path, int(5e9), missing, _CONFIG).state is HealthState.RED
    assert dr.check_disk(tmp_path, int(10e9), missing, _CONFIG).state is HealthState.YELLOW
    assert dr.check_disk(tmp_path, int(50e9), missing, _CONFIG).state is HealthState.GREEN
    # Nothing to download: only the data headroom is needed.
    assert dr.check_disk(tmp_path, int(3e9), (), _CONFIG).state is HealthState.YELLOW


def test_iris_home(tmp_path: Path) -> None:
    assert dr.check_home(tmp_path).state is HealthState.GREEN
    fresh = dr.check_home(tmp_path / "a" / "b")
    assert fresh.state is HealthState.GREEN
    assert "will be created" in fresh.detail
    locked = dr.check_home(tmp_path, writable=lambda p: False)
    assert locked.state is HealthState.RED
    assert locked.blocks == "all"


def test_ollama_unreachable() -> None:
    down = dr.OllamaFacts(url="http://x:11434", reachable=False, error="connection refused")
    check = dr.check_ollama(down)
    assert check.state is HealthState.RED
    assert check.blocks == "use"
    starter = dr.check_starter_models(down, _STARTER, ())
    assert starter.state is HealthState.GREY  # unknown, not a second failure


def test_ollama_facts_parse_and_unreachable() -> None:
    up = dr.ollama_facts(_ollama(["qwen2.5:7b-instruct", "llama3.2"]), "http://ollama.test")
    assert up.reachable
    assert up.version == "0.5.7"
    assert dr.model_key("llama3.2") in {dr.model_key(m) for m in up.models}

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    down = dr.ollama_facts(httpx.Client(transport=httpx.MockTransport(refuse)), "http://x")
    assert not down.reachable
    assert "refused" in (down.error or "")


def test_starter_models_missing_and_present() -> None:
    have = dr.OllamaFacts(url="u", reachable=True, models=frozenset({"qwen2.5:7b-instruct"}))
    assert dr.missing_starter_models(have, _STARTER) == ()
    none = dr.OllamaFacts(url="u", reachable=True, models=frozenset())
    missing = dr.missing_starter_models(none, _STARTER)
    assert missing == _STARTER
    check = dr.check_starter_models(none, _STARTER, missing)
    assert check.state is HealthState.RED
    assert "4.7 GB" in check.detail


def test_tier_models_beyond_the_starter_set_warn() -> None:
    have = dr.OllamaFacts(url="u", reachable=True, models=frozenset({"qwen2.5:7b-instruct"}))
    row = dr.check_tier_models(have, ("qwen2.5:7b-instruct", "llama3.2:3b"), _STARTER)
    assert row is not None
    assert row.state is HealthState.YELLOW
    assert "llama3.2:3b" in row.detail
    assert dr.check_tier_models(have, ("qwen2.5:7b-instruct",), _STARTER) is None


def test_vault_key_check_states() -> None:
    present = vault_keys.MasterKeyStatus("keyring", "in the OS keyring")
    assert dr.check_vault_key(present).state is HealthState.GREEN
    assert dr.check_vault_key(vault_keys.MasterKeyStatus("not_read"), "ready").state is (
        HealthState.GREEN
    )
    unread = dr.check_vault_key(vault_keys.MasterKeyStatus("not_read"))
    assert unread.state is HealthState.YELLOW
    for source in ("absent", "no_keyring", "invalid"):
        row = dr.check_vault_key(vault_keys.MasterKeyStatus(source))  # type: ignore[arg-type]
        assert row.state is HealthState.RED
        assert row.blocks == "use"  # the demo brings its own throwaway key (#246)
    no_keyring = dr.check_vault_key(vault_keys.MasterKeyStatus("no_keyring"))
    assert "export line" in (no_keyring.fix or "")


def test_extras_are_information_only() -> None:
    extra = dr.OptionalExtra("ml", "sentence_transformers", "semantic discovery")
    assert dr.check_extra(extra, False).state is HealthState.GREY
    assert dr.check_extra(extra, True).state is HealthState.GREY


# ── verdict ─────────────────────────────────────────────────────────────────────


def test_verdict_ready(facts: dict[str, Any], tmp_path: Path) -> None:
    report = _run(tmp_path, _ollama(["qwen2.5:7b-instruct"]))
    assert report.verdict is dr.Verdict.READY
    assert report.verdict.exit_code == 0
    assert not report.fixable


def test_verdict_demo_only_on_8gb(facts: dict[str, Any], tmp_path: Path) -> None:
    facts["host"] = _host(ram_total_bytes=8 * _GIB)
    report = _run(tmp_path, _ollama(["qwen2.5:7b-instruct"]))
    assert report.verdict is dr.Verdict.DEMO_ONLY
    assert report.verdict.exit_code == 1


def test_verdict_demo_only_when_models_missing(facts: dict[str, Any], tmp_path: Path) -> None:
    report = _run(tmp_path, _ollama([]))
    assert report.verdict is dr.Verdict.DEMO_ONLY
    assert report.missing_models == _STARTER
    assert report.fixable


def test_a_forced_lite_set_makes_an_8gb_host_ready(facts: dict[str, Any], tmp_path: Path) -> None:
    """The lite set sizes every check: memory passes, the lite starter is what is
    missing, and the set itself is the one fix left once the model is pulled."""
    from iris_harness.llm.tier_edits import RecordedModelSet

    facts["host"] = _host(ram_total_bytes=8 * _GIB)
    report = _run(tmp_path, _ollama([]), model_set="lite")
    assert _row(report, "Memory").state is HealthState.GREEN
    assert report.missing_models == _LITE_STARTER
    assert report.model_set is not None and report.model_set.name == "lite"

    pulled = _run(tmp_path, _ollama(["tiny:1b"]), model_set="lite")
    assert pulled.verdict is dr.Verdict.READY  # the pending set is a warning, not a block
    assert _row(pulled, "Model set").state is HealthState.YELLOW
    assert pulled.fixable

    facts["recorded"] = RecordedModelSet("lite", True, ("email_judge",))
    applied = _run(tmp_path, _ollama(["tiny:1b"]), model_set="lite")
    assert _row(applied, "Model set").state is HealthState.GREY
    assert not applied.fixable


def test_a_recorded_set_the_host_outgrew_is_a_warning(
    facts: dict[str, Any], tmp_path: Path
) -> None:
    from iris_harness.llm.tier_edits import RecordedModelSet

    facts["recorded"] = RecordedModelSet("lite", True, ("email_judge",))
    report = _run(tmp_path, _ollama(["qwen2.5:7b-instruct"]))
    row = _row(report, "Model set")
    assert row.state is HealthState.YELLOW
    assert "applied: lite" in row.detail
    assert report.verdict is dr.Verdict.READY
    assert report.fixable
    assert report.as_dict()["model_set"]["apply_needed"] is True


def test_an_unknown_forced_set_is_a_key_error(facts: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        _run(tmp_path, _ollama([]), model_set="huge")


def test_apply_model_set_writes_and_resets_tier_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Against the shipped files: lite's tiers land as tier edits, a reload of the
    router sees them, and moving back to standard clears them."""
    from iris_harness.foundation.paths import config_path
    from iris_harness.foundation.settings import SettingsStore
    from iris_harness.llm import tier_edits
    from iris_harness.llm.tier_router import TierRouter

    db = tmp_path / "settings.db"
    monkeypatch.setattr(dr, "settings_store", lambda: SettingsStore(db_path=db))
    cfg = dr.load_doctor_config()

    line = dr.apply_model_set(dr.choose_model_set(cfg, 8 * _GIB, force="lite"))
    assert line.startswith("model set lite applied")
    store = SettingsStore(db_path=db)
    assert tier_edits.recorded_model_set(store) == tier_edits.RecordedModelSet(
        "lite", True, ("tier2",)
    )
    router = TierRouter.load_from_yaml(config_path("llm_tiers.yaml"), settings=store)
    assert router.get_tier_by_name("tier2").model == "qwen3.5:4b-q4_K_M"
    assert router.get_tier_by_name("tier2").think is False
    assert router.get_tier_by_name("router").model == router._declared_tiers["router"].model
    # The judge is the file's: lite does not touch it (ADR decision 7).
    judge = router.get_tier_by_name("email_judge")
    assert judge.model == router._declared_tiers["email_judge"].model
    assert router.get_tier_by_name("tier1").model == router._declared_tiers["tier1"].model

    recorded = tier_edits.recorded_model_set(store)
    dr.apply_model_set(dr.choose_model_set(cfg, 32 * _GIB, recorded=recorded))
    store = SettingsStore(db_path=db)
    assert tier_edits.recorded_model_set(store) == tier_edits.RecordedModelSet(
        "standard", False, ()
    )
    assert {k for k in store.section("llm_tiers") if k.startswith("tier:")} == set()
    again = TierRouter.load_from_yaml(config_path("llm_tiers.yaml"), settings=store)
    assert again.get_tier_by_name("tier2").model == again._declared_tiers["tier2"].model


def test_verdict_demo_only_without_a_key(facts: dict[str, Any], tmp_path: Path) -> None:
    """The owner's vault key is a real-use requirement: `iris email demo` runs in its
    own home with a throwaway key, so a missing key must not read as 'not ready'."""
    keyring.set_keyring(fail.Keyring())
    report = _run(tmp_path, _ollama(["qwen2.5:7b-instruct"]))
    assert report.verdict is dr.Verdict.DEMO_ONLY
    assert report.verdict.exit_code == 1
    assert report.key_status.source == "no_keyring"
    assert report.fixable


def test_verdict_demo_only_on_a_headless_8gb_box(facts: dict[str, Any], tmp_path: Path) -> None:
    """#246: no keyring, no Ollama, 7.7 GiB (rounds to the 8 GB demo floor) -- the demo
    prerequisites are met, so the verdict is demo-only with exit 1."""
    keyring.set_keyring(fail.Keyring())
    facts["host"] = _host(ram_total_bytes=int(7.7 * _GIB))
    report = _run(tmp_path, _ollama_down())
    assert _row(report, "Memory").blocks == "use"
    assert _row(report, "Vault key").blocks == "use"
    assert _row(report, "Ollama").state is HealthState.RED
    assert report.verdict is dr.Verdict.DEMO_ONLY
    assert report.verdict.exit_code == 1


def test_verdict_not_ready_under_the_demo_floor_even_with_a_key(
    facts: dict[str, Any], tmp_path: Path
) -> None:
    facts["host"] = _host(ram_total_bytes=4 * _GIB)
    report = _run(tmp_path, _ollama(["qwen2.5:7b-instruct"]))
    assert report.verdict is dr.Verdict.NOT_READY
    assert report.verdict.exit_code == 2


def test_report_does_not_read_the_keyring_when_told_not_to(
    facts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(service: str, username: str) -> str:
        raise AssertionError("the keyring was read")

    monkeypatch.setattr(keyring.get_keyring(), "get_password", forbidden)
    report = _run(tmp_path, _ollama(["qwen2.5:7b-instruct"]), read_keyring=False)
    assert report.key_status.source == "not_read"
    assert _row(report, "Vault key").state is HealthState.YELLOW
    assert report.verdict is dr.Verdict.READY  # a warning never changes the verdict


def test_report_as_dict_is_json(facts: dict[str, Any], tmp_path: Path) -> None:
    body = _run(tmp_path, _ollama([])).as_dict()
    json.dumps(body)
    assert body["verdict"] == "demo_only"
    assert body["missing_models"] == [{"name": "qwen2.5:7b-instruct", "size_gb": 4.7}]
    assert {c["status"] for c in body["checks"]} <= {"pass", "warn", "fail", "info"}
    assert body["model_set"]["name"] == "standard"
    assert body["model_set"]["forced"] is False
    assert body["model_set"]["recorded"] is None


# ── fixes ───────────────────────────────────────────────────────────────────────


def test_pull_model_streams_to_success() -> None:
    pulls: list[str] = []
    seen: list[dr.PullProgress] = []
    dr.pull_model(
        "qwen2.5:7b-instruct",
        client=_ollama([], pulls=pulls),
        root="http://ollama.test",
        on_progress=seen.append,
    )
    assert pulls == ["qwen2.5:7b-instruct"]
    assert seen[-1].status == "success"


def test_pull_model_reports_an_error_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"error": "pull model manifest: file does not exist"}')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(dr.DoctorFixError, match="does not exist"):
        dr.pull_model("nope:1b", client=client, root="http://ollama.test")


def test_fix_key_keeps_an_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(vault_keys.MASTER_KEY_ENV, Fernet.generate_key().decode())
    assert dr.fix_master_key().outcome == "kept"


def test_fix_key_keeps_a_keyring_key() -> None:
    entries = keyring.get_keyring().entries  # type: ignore[attr-defined]
    before = dict(entries)
    result = dr.fix_master_key()
    assert result.outcome == "kept"
    assert entries == before  # never overwritten


def test_fix_key_stores_a_new_key_in_the_keyring() -> None:
    entries = keyring.get_keyring().entries  # type: ignore[attr-defined]
    entries.clear()
    result = dr.fix_master_key()
    assert result.outcome == "stored"
    assert vault_keys.is_fernet_key(entries[("iris-vault", "master-key")])
    assert result.export_line is None


def test_fix_key_without_a_keyring_prints_an_export_line() -> None:
    keyring.set_keyring(fail.Keyring())
    result = dr.fix_master_key(generate=lambda: "k" * 43 + "=")
    assert result.outcome == "export"
    assert result.export_line == f"export IRIS_VAULT_MASTER_KEY={'k' * 43}="
    assert "shell profile" in result.detail


def test_fix_key_falls_back_to_export_when_the_keyring_refuses_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring.get_keyring().entries.clear()  # type: ignore[attr-defined]

    def refuse(service: str, username: str, password: str) -> None:
        raise RuntimeError("locked")

    monkeypatch.setattr(keyring.get_keyring(), "set_password", refuse)
    result = dr.fix_master_key()
    assert result.outcome == "export"
    assert result.export_line is not None


def test_fix_key_leaves_an_invalid_env_key_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(vault_keys.MASTER_KEY_ENV, "garbage")
    result = dr.fix_master_key()
    assert result.outcome == "invalid"
    assert result.export_line is None
