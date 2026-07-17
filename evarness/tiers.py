"""Model-tier routing — route each intent to a tier, downshift to stay legal.

The economy a privacy-first personal agent needs: map every intent to a model
tier (cheap local router, fast local, local reasoning, big cloud opt-in), start
at the cheapest tier that can do the job, and DOWNSHIFT under pressure. Harness
Lab ran one provider for the whole graph — no notion of "this intent is cheap,
keep it local; that one earns the cloud".

The payoff of stacking on data classification: a tier carries a declared
LOCALITY, and the egress law already knows personal/secret content may not reach
cloud. So tier routing and the egress law COMPOSE — a personal-classified run
whose intent maps to a cloud tier is DOWNSHIFTED to a local tier automatically.
"Personal never leaves local tiers" stops being only a boundary block and
becomes a routing decision, made early, traced.

Platform shape: tier definitions and the intent->tier map are DATA
(packaged ``tiers.yaml`` + ``~/.evarness/tiers.yaml`` / ``$EVARNESS_TIERS``
overlay, merged per section); the node selects behavior (downshift | block |
warn on a forbidden-egress tier) and may override the map per harness. The
selection STRATEGY itself (intent map + egress-aware downshift) is deliberately
fixed — like intent_router and policy_gate, its extension surface is the config.

Teaching fidelity: in sim every tier resolves to a deterministic twin, so tiers
differ only by their DECLARED locality/label (that's what the egress law and the
trace reason about). Point a tier at a real provider in the overlay and the run
is non-deterministic — exactly like flipping any llm node to real, and the
trace says so.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# a model call's egress destination, declared per tier (the egress law reasons
# about THIS, not the sim twin's actual kind — see the module docstring)
LOCALITIES = ["sim", "local", "cloud"]

_PACKAGED = Path(__file__).parent / "tiers.yaml"
_config_cache: dict | None = None


def _overlay_path() -> Path:
    return Path(os.environ.get("EVARNESS_TIERS",
                               str(Path.home() / ".evarness" / "tiers.yaml")))


def tiers_config() -> dict:
    """Packaged tiers.yaml with the user overlay merged per section — an overlay
    can repoint one tier at a real provider, remap one intent, or change the
    fallback without copying the packaged file."""
    global _config_cache
    if _config_cache is None:
        cfg = yaml.safe_load(_PACKAGED.read_text()) or {}
        tiers = dict(cfg.get("tiers") or {})
        intents = dict(cfg.get("intents") or {})
        default_tier = cfg.get("default_tier")
        fallback_tier = cfg.get("fallback_tier")
        overlay = _overlay_path()
        if overlay.is_file():
            user = yaml.safe_load(overlay.read_text()) or {}
            for name, spec in (user.get("tiers") or {}).items():
                merged = dict(tiers.get(name) or {})
                merged.update(spec or {})
                tiers[name] = merged
            intents.update(user.get("intents") or {})
            default_tier = user.get("default_tier", default_tier)
            fallback_tier = user.get("fallback_tier", fallback_tier)
        _config_cache = {"tiers": tiers, "intents": intents,
                         "default_tier": default_tier, "fallback_tier": fallback_tier}
    return _config_cache


def reload_tiers_config() -> None:
    """Drop the cached config (tests; picking up an edited overlay)."""
    global _config_cache
    _config_cache = None


def tier_def(name: str) -> dict:
    return tiers_config()["tiers"].get(name or "", {})


def tier_locality(name: str) -> str:
    """A tier's declared egress locality; an undeclared/unknown tier fails CLOSED
    as cloud (the most-restricted destination), never silently local."""
    return tier_def(name).get("locality", "cloud")


def tier_provider(name: str) -> str:
    return tier_def(name).get("provider", "sim:helpful-v1")


def resolve_tier(intent: str | None, intents_override: dict | None,
                 default_override: str) -> tuple[str, bool]:
    """Map an intent to a tier name. Overrides (node config) win over the YAML
    map. Returns (tier_name, unknown) — unknown flags a configured tier that
    isn't defined, so the misconfig is traced, not silent."""
    cfg = tiers_config()
    table = {**cfg["intents"], **(intents_override or {})}
    default_tier = default_override or cfg["default_tier"] or cfg["fallback_tier"]
    name = table.get(intent or "", default_tier)
    unknown = bool(name) and name not in cfg["tiers"]
    return name, unknown


def fallback_tier() -> str:
    cfg = tiers_config()
    return cfg["fallback_tier"] or cfg["default_tier"] or ""


def tier_router_uses_real_provider(node_cfg: dict) -> bool:
    """Determinism scan (engine): true if ANY tier this node could resolve to
    points at a non-sim provider. Conservative — like a single real-mode tool,
    one reachable real tier makes the run non-reproducible."""
    cfg = tiers_config()
    reachable = set((cfg["intents"] or {}).values())
    reachable |= set((node_cfg.get("intents") or {}).values())
    for key in ("default_tier", "fallback_tier"):
        if cfg.get(key):
            reachable.add(cfg[key])
    if node_cfg.get("default_tier"):
        reachable.add(node_cfg["default_tier"])
    return any(not str(tier_provider(t)).startswith("sim:") for t in reachable if t)
