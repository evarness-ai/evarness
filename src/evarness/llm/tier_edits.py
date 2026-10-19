"""The owner's edits to ``llm_tiers.yaml``, saved on the data volume (ADR-0120).

On the cloud VM ``llm_tiers.yaml`` is a read-only mount and a deploy replaces it, so a
change made from the app is saved in the settings store (section ``llm_tiers``) and laid
over the file when a router loads (``TierRouter.load_from_yaml``). Two kinds:

* ``tier:<name>`` -> the fields of that tier that differ from the file (``model``,
  ``provider``, ``max_tokens``, ``temperature``, ``timeout_seconds``);
* ``intent:<intent>`` -> the tier that intent runs on, when not the file's.

Only the fields that differ are stored, so a later deploy still changes the ones the
owner never touched. An edit applies to the live router at once, so the next turn uses
it; helpers that built their client at startup pick it up after a restart.

A third key, ``model_set``, records which of ``doctor.yaml``'s model sets ``evarness doctor
--fix`` applied (the set's name, whether it was forced, and the tiers it wrote) so a
later doctor run can tell the active set from the one the host fits, and a change of
set can reset the tiers the old set touched. A set is written as ordinary ``tier:``
edits (:func:`apply_model_set`), so everything above applies to it unchanged.

Guarded (the owner confirms, and the API enforces it): changing a tier's provider — its
route — and moving an intent to a tier on a different provider. Either can change where
a prompt goes, and on the VM the providers are routes with different privacy.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evarness.foundation.settings.store import SettingsStore
    from evarness.llm.tier_router import TierConfig, TierRouter

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "llm_tiers"
MODEL_SET_KEY = "model_set"
EDITABLE_FIELDS = ("provider", "model", "max_tokens", "temperature", "timeout_seconds", "think")
PROVIDERS = ("ollama", "lmstudio", "anthropic", "openrouter", "github")
_LIMITS = {
    "max_tokens": (1, 200_000),
    "timeout_seconds": (1, 3_600),
    "temperature": (0.0, 2.0),
}


class TierEditError(ValueError):
    """An edit that does not fit a tier's field or names nothing that exists."""


def validate_fields(changes: dict[str, Any]) -> dict[str, Any]:
    """Normalise ``changes`` to typed field values, or raise TierEditError."""
    out: dict[str, Any] = {}
    for key, raw in changes.items():
        if key not in EDITABLE_FIELDS:
            raise TierEditError(f"{key!r} is not an editable tier field")
        if key == "provider":
            value = str(raw).strip()
            if value not in PROVIDERS:
                raise TierEditError(f"provider must be one of {', '.join(PROVIDERS)}")
            out[key] = value
        elif key == "think":
            # A reasoning model's thinking on/off (Ollama); None is "the model's
            # default" (a tier the file leaves unset). Anything else is an error.
            if raw is not None and not isinstance(raw, bool):
                raise TierEditError("think must be true or false")
            out[key] = raw
        elif key == "model":
            value = str(raw).strip()
            if not value or len(value) > 200 or "\n" in value:
                raise TierEditError("model must be one line of at most 200 characters")
            out[key] = value
        else:
            number: float
            try:
                if isinstance(raw, bool):
                    raise TypeError
                number = float(raw) if key == "temperature" else int(str(raw).strip())
            except (TypeError, ValueError):
                raise TierEditError(f"{key} must be a number") from None
            low, high = _LIMITS[key]
            if not low <= number <= high:
                raise TierEditError(f"{key} must be between {low} and {high}")
            out[key] = number
    return out


def _fields(tier: TierConfig) -> dict[str, Any]:
    return {f: getattr(tier, f) for f in EDITABLE_FIELDS}


def apply_saved(router: TierRouter, store: SettingsStore) -> None:
    """Lay the saved edits over a freshly loaded router. A saved edit that no longer
    fits (a tier or intent gone from the file, a value out of range) is skipped with a
    warning: the file's value is a safe place to land."""
    try:
        saved = store.section(SETTINGS_SECTION)
    except Exception:  # a broken store must not stop model routing
        logger.exception("llm tiers: could not read saved edits; using the file")
        return
    for key, value in saved.items():
        kind, _, name = key.partition(":")
        try:
            if kind == "tier" and isinstance(value, dict):
                _set_tier(router, name, validate_fields(value))
            elif kind == "intent" and isinstance(value, str):
                _move(router, name, value)
        except (TierEditError, KeyError) as exc:
            logger.warning("llm tiers: ignoring saved edit %s (%s)", key, exc)


def _set_tier(router: TierRouter, name: str, fields: dict[str, Any]) -> None:
    tier = router._tiers.get(name)  # the router's own overlay
    if tier is None:
        raise KeyError(name)
    router._tiers[name] = replace(tier, **fields)


def _move(router: TierRouter, intent: str, tier_name: str) -> None:
    if tier_name not in router._tiers:
        raise KeyError(tier_name)
    if intent not in router._intent_to_tier:
        raise KeyError(intent)
    old = router._intent_to_tier[intent]
    router._intent_to_tier[intent] = tier_name
    # Keep each tier's use_for in step, so every view of the mapping agrees.
    if old in router._tiers and old != tier_name:
        before = router._tiers[old]
        router._tiers[old] = replace(
            before, use_for=tuple(i for i in before.use_for if i != intent)
        )
    target = router._tiers[tier_name]
    if intent not in target.use_for:
        router._tiers[tier_name] = replace(target, use_for=(*target.use_for, intent))


def is_guarded_tier_change(router: TierRouter, name: str, fields: dict[str, Any]) -> bool:
    tier = router._tiers.get(name)
    return tier is not None and "provider" in fields and fields["provider"] != tier.provider


def is_guarded_move(router: TierRouter, intent: str, tier_name: str) -> bool:
    current = router.get_tier_by_name(router._intent_to_tier.get(intent, ""))
    target = router.get_tier_by_name(tier_name)
    return current is not None and target is not None and current.provider != target.provider


def update_tier(
    router: TierRouter, store: SettingsStore, name: str, changes: dict[str, Any], *, actor: str
) -> TierConfig:
    """Change a tier's fields now and save the ones that differ from the file."""
    declared = router._declared_tiers.get(name)
    current = router._tiers.get(name)
    if declared is None or current is None:
        raise KeyError(name)
    fields = validate_fields(changes)
    target = replace(current, **fields)
    if _fields(target) == _fields(current):
        return current
    diff = {f: v for f, v in _fields(target).items() if v != getattr(declared, f)}
    if diff:
        store.set(
            SETTINGS_SECTION,
            f"tier:{name}",
            diff,
            old=_fields(current),
            new=_fields(target),
            actor=actor,
        )
    else:
        store.clear(
            SETTINGS_SECTION, f"tier:{name}", old=_fields(current), new=_fields(target), actor=actor
        )
    _set_tier(router, name, fields)
    return router._tiers[name]


def reset_tier(router: TierRouter, store: SettingsStore, name: str, *, actor: str) -> TierConfig:
    declared = router._declared_tiers.get(name)
    if declared is None:
        raise KeyError(name)
    return update_tier(router, store, name, _fields(declared), actor=actor)


def move_intent(
    router: TierRouter, store: SettingsStore, intent: str, tier_name: str, *, actor: str
) -> str:
    """Run ``intent`` on ``tier_name`` from the next turn; save it unless it is the file's."""
    current = router._intent_to_tier.get(intent)
    if current is None:
        raise KeyError(intent)
    if tier_name not in router._tiers:
        raise KeyError(tier_name)
    if tier_name == current:
        return current
    key = f"intent:{intent}"
    if tier_name == router._declared_intents.get(intent):
        store.clear(SETTINGS_SECTION, key, old=current, new=tier_name, actor=actor)
    else:
        store.set(SETTINGS_SECTION, key, tier_name, old=current, actor=actor)
    _move(router, intent, tier_name)
    return tier_name


def reset_intent(router: TierRouter, store: SettingsStore, intent: str, *, actor: str) -> str:
    declared = router._declared_intents.get(intent)
    if declared is None:
        raise KeyError(intent)
    return move_intent(router, store, intent, declared, actor=actor)


# ── model sets (doctor.yaml) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordedModelSet:
    """The ``model_set`` row: what ``evarness doctor --fix`` last applied."""

    name: str
    forced: bool
    #: The tiers that set wrote, so the next set can reset the ones it does not touch.
    tiers: tuple[str, ...] = ()


def recorded_model_set(store: SettingsStore) -> RecordedModelSet | None:
    """The applied set, or ``None`` when no set was ever applied (the file's tiers)."""
    try:
        raw = store.get(SETTINGS_SECTION, MODEL_SET_KEY)
    except Exception:  # a broken store reads as "nothing applied"
        logger.exception("llm tiers: could not read the applied model set")
        return None
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    tiers = raw.get("tiers") or ()
    return RecordedModelSet(
        name=str(raw["name"]),
        forced=bool(raw.get("forced", False)),
        tiers=tuple(str(t) for t in tiers),
    )


def apply_model_set(
    router: TierRouter,
    store: SettingsStore,
    name: str,
    tiers: Mapping[str, Mapping[str, Any]],
    *,
    forced: bool,
    actor: str,
) -> RecordedModelSet:
    """Write a model set: each of its ``tiers`` as a tier edit, and tiers the previous
    set wrote but this one does not back to the file. Then record the set.

    The set's tiers replace the owner's own edits on those tiers: choosing a set is a
    deliberate move of the install, and the owner can still edit a tier afterwards.
    Raises ``KeyError`` for a tier the file does not have and :class:`TierEditError`
    for a field that does not fit, before anything is written.
    """
    validated = {tier: validate_fields(dict(fields)) for tier, fields in tiers.items()}
    for tier in validated:
        if tier not in router._declared_tiers:
            raise KeyError(tier)
    previous = recorded_model_set(store)
    stale = () if previous is None else tuple(t for t in previous.tiers if t not in validated)
    for tier in stale:
        if tier in router._declared_tiers:
            reset_tier(router, store, tier, actor=actor)
    for tier, fields in validated.items():
        declared = router._declared_tiers[tier]
        update_tier(router, store, tier, {**_fields(declared), **fields}, actor=actor)
    record = RecordedModelSet(name=name, forced=forced, tiers=tuple(validated))
    store.set(
        SETTINGS_SECTION,
        MODEL_SET_KEY,
        {"name": record.name, "forced": record.forced, "tiers": list(record.tiers)},
        old=None if previous is None else {"name": previous.name, "forced": previous.forced},
        actor=actor,
    )
    return record
