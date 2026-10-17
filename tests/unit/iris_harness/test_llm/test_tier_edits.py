"""The owner's edits to llm_tiers.yaml: live, saved, and back after a reload (ADR-0120)."""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_harness.foundation.settings import SETTINGS_DB_NAME, SettingsStore
from iris_harness.llm import tier_edits
from iris_harness.llm.tier_router import TierRouter

YAML = """
tiers:
  tier1:
    name: Fast
    provider: lmstudio
    model: granite4:latest
    max_tokens: 1024
    temperature: 0.3
    timeout_seconds: 90
    use_for: [general, simple_query]
  tier2:
    name: Advanced
    provider: lmstudio
    model: qwen2.5:7b-instruct
    max_tokens: 4096
    temperature: 0.7
    timeout_seconds: 150
    use_for: [calendar]
  private:
    name: Advanced (private)
    provider: ollama
    model: qwen2.5:7b-instruct
    max_tokens: 4096
    temperature: 0.7
    timeout_seconds: 90
    use_for: [finance, communication]
"""


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, SettingsStore]:
    path = tmp_path / "llm_tiers.yaml"
    path.write_text(YAML, encoding="utf-8")
    return path, SettingsStore(db_path=tmp_path / SETTINGS_DB_NAME)


def _load(files: tuple[Path, SettingsStore]) -> TierRouter:
    path, store = files
    return TierRouter.load_from_yaml(path, settings=SettingsStore(db_path=store.db_path))


def test_a_tier_edit_applies_to_the_next_call_and_survives_a_reload(files) -> None:
    router = _load(files)
    _, store = files

    tier_edits.update_tier(
        router, store, "tier1", {"model": "qwen2.5:7b-instruct", "timeout_seconds": "60"}, actor="d"
    )

    assert router.get_llm_config("general").model == "qwen2.5:7b-instruct"
    assert store.get("llm_tiers", "tier:tier1") == {
        "model": "qwen2.5:7b-instruct",
        "timeout_seconds": 60,
    }
    again = _load(files)
    assert again.get_tier_by_name("tier1").model == "qwen2.5:7b-instruct"
    assert again.get_tier_by_name("tier1").timeout_seconds == 60
    assert again._declared_tiers["tier1"].model == "granite4:latest"


def test_a_later_file_change_still_reaches_fields_the_owner_never_touched(files) -> None:
    path, store = files
    tier_edits.update_tier(_load(files), store, "tier1", {"model": "custom:1b"}, actor="d")
    path.write_text(YAML.replace("max_tokens: 1024", "max_tokens: 2048"), encoding="utf-8")

    tier = _load(files).get_tier_by_name("tier1")

    assert tier.model == "custom:1b"
    assert tier.max_tokens == 2048


def test_editing_back_to_the_file_clears_the_saved_edit_and_reset_restores(files) -> None:
    router = _load(files)
    _, store = files
    tier_edits.update_tier(router, store, "tier2", {"temperature": 0.1}, actor="d")
    tier_edits.update_tier(router, store, "tier2", {"temperature": 0.7}, actor="d")
    assert store.get("llm_tiers", "tier:tier2") is None

    tier_edits.update_tier(router, store, "tier2", {"max_tokens": 100}, actor="d")
    back = tier_edits.reset_tier(router, store, "tier2", actor="d")
    assert back.max_tokens == 4096
    assert store.get("llm_tiers", "tier:tier2") is None


def test_moving_an_intent_routes_the_next_turn_and_keeps_use_for_in_step(files) -> None:
    router = _load(files)
    _, store = files

    tier_edits.move_intent(router, store, "calendar", "tier1", actor="d")

    assert router.get_llm_config("calendar").model == "granite4:latest"
    assert "calendar" in router.get_tier_by_name("tier1").use_for
    assert "calendar" not in router.get_tier_by_name("tier2").use_for
    assert _load(files).intent_tier_map()["calendar"] == "tier1"
    tier_edits.reset_intent(router, store, "calendar", actor="d")
    assert router.intent_tier_map()["calendar"] == "tier2"
    assert store.get("llm_tiers", "intent:calendar") is None


def test_route_changes_are_guarded_other_edits_are_not(files) -> None:
    router = _load(files)
    assert tier_edits.is_guarded_tier_change(router, "private", {"provider": "lmstudio"})
    assert not tier_edits.is_guarded_tier_change(router, "private", {"model": "x"})
    # finance leaves the Mac-only provider: guarded. calendar between two lmstudio tiers: not.
    assert tier_edits.is_guarded_move(router, "finance", "tier2")
    assert not tier_edits.is_guarded_move(router, "calendar", "tier1")


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "somewhere"},
        {"temperature": 5},
        {"max_tokens": 0},
        {"timeout_seconds": "soon"},
        {"model": ""},
        {"num_ctx": 4096},
    ],
)
def test_bad_edits_change_nothing(files, change: dict[str, object]) -> None:
    router = _load(files)
    _, store = files
    with pytest.raises(tier_edits.TierEditError):
        tier_edits.update_tier(router, store, "tier1", change, actor="d")
    assert store.history() == []
    assert router.get_tier_by_name("tier1").model == "granite4:latest"


def test_unknown_tier_or_intent_is_a_key_error(files) -> None:
    router = _load(files)
    _, store = files
    with pytest.raises(KeyError):
        tier_edits.update_tier(router, store, "tier9", {"model": "x"}, actor="d")
    with pytest.raises(KeyError):
        tier_edits.move_intent(router, store, "nope", "tier1", actor="d")
    with pytest.raises(KeyError):
        tier_edits.move_intent(router, store, "calendar", "tier9", actor="d")


def test_a_model_set_writes_its_tiers_resets_the_old_ones_and_is_recorded(files) -> None:
    router = _load(files)
    _, store = files
    assert tier_edits.recorded_model_set(store) is None

    record = tier_edits.apply_model_set(
        router,
        store,
        "lite",
        {"tier1": {"model": "small:1b", "timeout_seconds": 60}},
        forced=True,
        actor="doctor",
    )

    assert record == tier_edits.RecordedModelSet("lite", True, ("tier1",))
    assert router.get_tier_by_name("tier1").model == "small:1b"
    assert store.get("llm_tiers", "tier:tier1") == {"model": "small:1b", "timeout_seconds": 60}
    assert tier_edits.recorded_model_set(store) == record
    again = _load(files)  # the reload lays the set over the file like any edit
    assert again.get_tier_by_name("tier1").model == "small:1b"

    # Moving to a set without tier1 puts tier1 back to the file.
    back = tier_edits.apply_model_set(
        router, store, "standard", {"tier2": {"temperature": 0.2}}, forced=False, actor="doctor"
    )
    assert back == tier_edits.RecordedModelSet("standard", False, ("tier2",))
    assert router.get_tier_by_name("tier1").model == "granite4:latest"
    assert store.get("llm_tiers", "tier:tier1") is None
    assert store.get("llm_tiers", "tier:tier2") == {"temperature": 0.2}


def test_a_model_set_replaces_the_owners_edit_on_the_tiers_it_names_only(files) -> None:
    router = _load(files)
    _, store = files
    tier_edits.update_tier(router, store, "tier1", {"model": "mine:3b"}, actor="owner")
    tier_edits.update_tier(router, store, "private", {"temperature": 0.1}, actor="owner")

    tier_edits.apply_model_set(
        router, store, "lite", {"tier1": {"model": "small:1b"}}, forced=True, actor="doctor"
    )

    assert router.get_tier_by_name("tier1").model == "small:1b"
    assert router.get_tier_by_name("private").temperature == 0.1  # untouched


def test_a_model_set_naming_an_unknown_tier_or_field_writes_nothing(files) -> None:
    router = _load(files)
    _, store = files
    with pytest.raises(KeyError):
        tier_edits.apply_model_set(
            router,
            store,
            "lite",
            {"tier1": {"model": "ok:1b"}, "tier9": {"model": "x"}},
            forced=True,
            actor="doctor",
        )
    with pytest.raises(tier_edits.TierEditError):
        tier_edits.apply_model_set(
            router, store, "lite", {"tier1": {"num_ctx": 1}}, forced=True, actor="doctor"
        )
    assert store.history() == []
    assert router.get_tier_by_name("tier1").model == "granite4:latest"


def test_the_model_set_record_is_not_read_as_a_tier_edit(files) -> None:
    _, store = files
    store.set("llm_tiers", "model_set", {"name": "lite", "forced": True}, old=None, actor="d")
    router = _load(files)
    assert router.get_tier_by_name("tier1").model == "granite4:latest"
    assert tier_edits.recorded_model_set(store) == tier_edits.RecordedModelSet("lite", True, ())


def test_a_saved_edit_that_no_longer_fits_falls_back_to_the_file(files) -> None:
    _, store = files
    store.set("llm_tiers", "tier:tier1", {"temperature": 9}, old=None, actor="d")
    store.set("llm_tiers", "tier:gone", {"model": "x"}, old=None, actor="d")
    store.set("llm_tiers", "intent:calendar", "gone", old=None, actor="d")

    router = _load(files)

    assert router.get_tier_by_name("tier1").temperature == 0.3
    assert router.intent_tier_map()["calendar"] == "tier2"
