"""Data classification + egress rules — governance the flow can carry.

The governance capability a privacy-first personal agent needs: tag every piece
of content ``public / internal / personal / secret`` and enforce an egress law
at runtime — personal data never leaves local model tiers, secrets never enter
any prompt at all. Evarness knew WHERE content flowed, never WHAT was
flowing; this adds the missing dimension.

Platform shape: the CODE of a
classifier is registered under a name; its BEHAVIOR (markers, patterns) and the
egress table are YAML config; the harness selects the classifier per node.
Bring your own without a core edit::

    from evarness.classification import register_classifier

    @register_classifier("dlp")
    def dlp(text, cfg):
        # cfg = this classifier's section from classification.yaml (+ overlay)
        # return (classification, signal names) — signals NAME what matched,
        # they must never ECHO the matched content into the trace
        return ("secret", ["customer_id"]) if "cust-" in text else ("public", [])

then set ``classifier: dlp`` on a data_classifier node — per-harness config,
sweepable in Experiments, tunable in ``~/.evarness/classification.yaml``
(or ``$EVARNESS_CLASSIFICATION``), which merges over the packaged file.

Enforcement is topological, not aspectual: a data_classifier node ARMS the run
(sets its classification high-water mark + egress mode); the model and tool
boundaries (llm, tool, loop_controller) then check the mark against the egress
table before anything leaves. A denied run blocks BEFORE the provider is
called — the trace shows egress_denied and no llm_request, honoring the rule
"the model must never be called on a blocked run".

Privacy rule for this module: classification signals carry marker NAMES
(``api_key``, ``ssn``), never the matched text — a trace must not become the
leak it exists to prevent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

import yaml

# ordered low -> high; the run keeps a monotonic high-water mark
CLASS_ORDER = ["public", "internal", "personal", "secret"]
_RANK = {c: i for i, c in enumerate(CLASS_ORDER)}

# egress destinations the boundaries report (see _egress_gate call sites):
# sim (fixture twin) · local (on-host model/tool) · cloud (hosted model API) ·
# network (a tool that carries content off-host)
DESTINATIONS = ["sim", "local", "cloud", "network"]

# a classifier: (text, classifier_cfg) -> (classification, signal names)
Classifier = Callable[[str, dict], tuple[str, list]]

_CLASSIFIERS: dict[str, Classifier] = {}
DEFAULT_CLASSIFIER = "keyword"


def register_classifier(name: str):
    """Register a classifier under ``name`` (classifiers are pure functions)."""

    def deco(fn: Classifier) -> Classifier:
        _CLASSIFIERS[name.lower()] = fn
        return fn

    return deco


def get_classifier(name: str) -> Classifier | None:
    return _CLASSIFIERS.get((name or "").lower())


def available_classifiers() -> list[str]:
    return sorted(_CLASSIFIERS)


def max_class(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


# ------------------------------------------------------------------ YAML config

_PACKAGED = Path(__file__).parent / "classification.yaml"
_config_cache: dict | None = None


def _overlay_path() -> Path:
    return Path(
        os.environ.get(
            "EVARNESS_CLASSIFICATION", str(Path.home() / ".evarness" / "classification.yaml")
        )
    )


def classification_config() -> dict:
    """Packaged classification.yaml with the user overlay merged per section —
    an overlay can loosen one egress row (opt internal into cloud) or extend one
    classifier's markers without copying the packaged file."""
    global _config_cache
    if _config_cache is None:
        cfg = yaml.safe_load(_PACKAGED.read_text()) or {}
        classifiers = dict(cfg.get("classifiers") or {})
        egress = dict(cfg.get("egress") or {})
        overlay = _overlay_path()
        if overlay.is_file():
            user = yaml.safe_load(overlay.read_text()) or {}
            for name, knobs in (user.get("classifiers") or {}).items():
                merged = dict(classifiers.get(name) or {})
                merged.update(knobs or {})
                classifiers[name] = merged
            for cls_, dests in (user.get("egress") or {}).items():
                egress[cls_] = list(dests or [])
        _config_cache = {"classifiers": classifiers, "egress": egress}
    return _config_cache


def reload_classification_config() -> None:
    """Drop the cached config (tests; picking up an edited overlay)."""
    global _config_cache
    _config_cache = None


def classifier_config(name: str) -> dict:
    return classification_config()["classifiers"].get((name or "").lower(), {})


def egress_allowed(classification: str, destination: str) -> bool:
    """True when the egress table allows this classification at this destination.
    Unknown classifications fail CLOSED (treated as secret); unknown destinations
    fail closed too — an egress law with silent holes is not a law."""
    table = classification_config()["egress"]
    allowed = table.get(classification)
    if allowed is None:
        return False
    return destination in allowed


# ------------------------------------------------------------------ dispatch


def classify(text: str, name: str | None = None) -> tuple[str, list, str | None]:
    """Run the named classifier; unknown names are TRACED, never silent:
    returns (classification, signals, unknown_name) — on an unknown name the
    built-in keyword classifier runs and unknown_name carries the misconfig."""
    wanted = (name or DEFAULT_CLASSIFIER).lower()
    fn = get_classifier(wanted)
    unknown = None
    if fn is None:
        unknown, wanted = wanted, DEFAULT_CLASSIFIER
        fn = get_classifier(DEFAULT_CLASSIFIER)
    assert fn is not None  # the default classifier is always registered
    classification, signals = fn(text or "", classifier_config(wanted))
    if classification not in _RANK:  # a broken custom classifier fails closed
        classification, signals = "secret", list(signals) + ["invalid_classification"]
    return classification, list(signals), unknown


# ------------------------------------------------------------------ built-in: keyword

# secret shapes are PATTERNS (key material has structure); personal/internal are
# marker driven. All tunable in classification.yaml / the user overlay.
_SECRET_PATTERNS = {
    "api_key": re.compile(r"\b(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+", re.I),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\b(?:sk|pk|ghp|xox[bap])-[A-Za-z0-9_-]{8,}", re.I),
}
_PERSONAL_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email_address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "phone": re.compile(r"\b(?:\+?\d{1,2}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
}


@register_classifier("keyword")
def keyword_classifier(text: str, cfg: dict) -> tuple[str, list]:
    """Deterministic marker/pattern classifier — the sim-faithful default.
    Returns the HIGHEST class found plus the names of every signal that fired."""
    low = text.lower()
    signals: list[str] = []
    found = "public"
    for sig, pat in _SECRET_PATTERNS.items():
        if pat.search(text):
            signals.append(sig)
            found = "secret"
    for marker in cfg.get("secret_markers") or []:
        if str(marker).lower() in low:
            signals.append(f"marker:{marker}")
            found = "secret"
    for sig, pat in _PERSONAL_PATTERNS.items():
        if pat.search(text):
            signals.append(sig)
            found = max_class(found, "personal")
    for marker in cfg.get("personal_markers") or []:
        if str(marker).lower() in low:
            signals.append(f"marker:{marker}")
            found = max_class(found, "personal")
    for marker in cfg.get("internal_markers") or []:
        if str(marker).lower() in low:
            signals.append(f"marker:{marker}")
            found = max_class(found, "internal")
    return found, signals
