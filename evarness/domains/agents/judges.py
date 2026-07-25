"""Response judges — an ordered curation chain, as an extension point.

A single llm_judge is one scoring gate: score, threshold, pass or block. A
production response curator is a CHAIN of judges with different powers, run in a
deliberate order, where the FIRST halt short-circuits the rest:

- a safety judge HALTS on dangerous content (no retry — you don't retry a bomb
  recipe into safety)
- a schema judge is REPAIRABLE — a malformed answer gets a bounded number of
  repair attempts before it degrades
- a faithfulness judge WARNS or halts on a low grounded-ness score
- a leak judge HALTS on architecture/secret disclosure

Two things a single gate can't express and this chain does:

1. **Retry budget.** A `retry` verdict re-runs the judge up to N times, applying
   the judge's registered repair between attempts. Repairable failures (schema)
   recover; unrepairable ones (faithfulness) exhaust the budget and DEGRADE to
   the judge's on_exhausted verdict, traced — never a silent pass.
2. **Fail-open on timeout.** A judge the fixture marks as timing out does NOT
   block the response; it emits a degraded banner and the chain continues. An
   unavailable judge is an availability failure, not a safety verdict — failing
   closed there would take the whole agent down every time a judge hiccups.

Platform shape: the CODE of a judge is registered under a name; its
BEHAVIOR (thresholds, deny terms, on_fail/on_exhausted) is YAML config
(`judges.yaml` + `~/.evarness/judges.yaml` overlay); the node picks the
ordered list. Bring your own::

    from evarness.domains.agents.judges import register_judge, JudgeSignal

    @register_judge("pii")
    def pii(text, cfg, ctx):
        return JudgeSignal("pii", "halt", reason="SSN in output") if _ssn(text) \
            else JudgeSignal("pii", "pass")

then add "pii" to a judge_chain node's `judges` list.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

VERDICTS = ("pass", "warn", "retry", "halt")


@dataclass
class JudgeSignal:
    name: str
    verdict: str  # pass | warn | retry | halt
    score: float | None = None
    reason: str = ""


# a judge: (text, judge_cfg, ctx) -> JudgeSignal
Judge = Callable[[str, dict, object], JudgeSignal]
# an optional repair: (text) -> repaired text, tried between retry attempts
Repair = Callable[[str], str]

_JUDGES: dict[str, Judge] = {}
_REPAIRS: dict[str, Repair] = {}
DEFAULT_JUDGES = ["safety", "faithfulness"]


def register_judge(name: str, repair: Repair | None = None):
    """Register a judge under ``name``; optionally a repair tried between retries."""

    def deco(fn: Judge) -> Judge:
        _JUDGES[name.lower()] = fn
        if repair is not None:
            _REPAIRS[name.lower()] = repair
        return fn

    return deco


def get_judge(name: str) -> Judge | None:
    return _JUDGES.get((name or "").lower())


def get_repair(name: str) -> Repair | None:
    return _REPAIRS.get((name or "").lower())


def available_judges() -> list[str]:
    return sorted(_JUDGES)


# ------------------------------------------------------------------ YAML config

_PACKAGED = Path(__file__).parent / "judges.yaml"
_config_cache: dict | None = None


def _overlay_path() -> Path:
    return Path(os.environ.get("EVARNESS_JUDGES", str(Path.home() / ".evarness" / "judges.yaml")))


def judges_config() -> dict:
    global _config_cache
    if _config_cache is None:
        cfg = yaml.safe_load(_PACKAGED.read_text()) or {}
        judges = dict(cfg.get("judges") or {})
        panel = dict(cfg.get("panel") or {})
        overlay = _overlay_path()
        if overlay.is_file():
            user = yaml.safe_load(overlay.read_text()) or {}
            for name, knobs in (user.get("judges") or {}).items():
                merged = dict(judges.get(name) or {})
                merged.update(knobs or {})
                judges[name] = merged
            panel.update(user.get("panel") or {})
        _config_cache = {"judges": judges, "panel": panel}
    return _config_cache


def reload_judges_config() -> None:
    global _config_cache
    _config_cache = None


def judge_config(name: str) -> dict:
    return judges_config()["judges"].get((name or "").lower(), {})


def panel_config() -> dict:
    """Aggregation defaults for the judge_panel node (``panel:`` section)."""
    return judges_config()["panel"]


# ------------------------------------------------------------------ built-in judges

# hard-halt patterns: secrets that must never appear in an ANSWER, and clearly
# dangerous content. Deterministic — a safety verdict is not a judgment call.
_SAFETY_PATTERNS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS key leaked in output
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:build|make|construct)\s+a\s+bomb\b", re.I),
]


@register_judge("safety")
def safety_judge(text: str, cfg: dict, ctx) -> JudgeSignal:
    """Deterministic hard halt on secret leakage / dangerous content. No retry."""
    for pat in _SAFETY_PATTERNS:
        if pat.search(text):
            return JudgeSignal("safety", "halt", reason="unsafe content in output")
    for term in cfg.get("deny") or []:
        if str(term).lower() in text.lower():
            return JudgeSignal("safety", "halt", reason=f"denied content: {term}")
    return JudgeSignal("safety", "pass")


@register_judge("leak")
def leak_judge(text: str, cfg: dict, ctx) -> JudgeSignal:
    """Halt on architecture / identity disclosure — deny terms from config."""
    hit = next((t for t in (cfg.get("deny") or []) if str(t).lower() in text.lower()), None)
    if hit:
        return JudgeSignal("leak", "halt", reason=f"disclosure: {hit}")
    return JudgeSignal("leak", "pass")


def _extract_json(text: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", cleaned):
        try:
            return dec.raw_decode(cleaned[m.start() :])[0]
        except ValueError:
            continue
    return None


def _schema_repair(text: str) -> str:
    """Repair between retries: pull the first JSON value out and re-serialize it."""
    value = _extract_json(text)
    return json.dumps(value, ensure_ascii=False) if value is not None else text


@register_judge("schema", repair=_schema_repair)
def schema_judge(text: str, cfg: dict, ctx) -> JudgeSignal:
    """When JSON output is expected: valid -> pass; malformed -> retry (repairable
    by _schema_repair). Text format always passes."""
    if cfg.get("format", "json") != "json":
        return JudgeSignal("schema", "pass")
    try:
        json.loads(text.strip())
        return JudgeSignal("schema", "pass")
    except (ValueError, TypeError):
        return JudgeSignal("schema", "retry", reason="output is not valid JSON")


def _scored(name: str, dim: str, text: str, cfg: dict, ctx) -> JudgeSignal:
    scores = ctx.fixture.judge_scores(text, [dim])
    s = scores.get(dim, 0.75)
    if s >= float(cfg.get("threshold", 0.6)):
        return JudgeSignal(name, "pass", score=s)
    return JudgeSignal(
        name,
        cfg.get("on_fail", "warn"),
        score=s,
        reason=f"{dim} {s} below {cfg.get('threshold', 0.6)}",
    )


@register_judge("faithfulness")
def faithfulness_judge(text: str, cfg: dict, ctx) -> JudgeSignal:
    return _scored("faithfulness", "faithfulness", text, cfg, ctx)


@register_judge("groundedness")
def groundedness_judge(text: str, cfg: dict, ctx) -> JudgeSignal:
    return _scored("groundedness", "groundedness", text, cfg, ctx)
