"""Simulation layer: SimLLM, SimVectorStore, mock tools, scenario fixtures.

Deterministic by contract: (graph, fixture, seed) -> identical event sequence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .prompts import DEFAULTS


@dataclass
class Completion:
    """What any LLM provider returns: text + honest usage/cost accounting."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def approx_tokens(text: str) -> int:
    """v1 approximation (~4 chars/token). Deliberate simplification (no real tokenizer) — see DECISIONS.md."""
    return max(1, len(str(text)) // 4)


class Fixture:
    """A scenario fixture: scripted LLM responses, tool results, memory, faults, attacks.

    Long-term memory sections (phase 4): `episodic` (past-session interaction records),
    `facts` (semantic user profile), `instructions` (procedural standing orders).
    `judge` scripts LLM-judge scores; `react` scripts loop-controller decisions.
    """

    def __init__(self, data: dict | None = None):
        d = data or {}
        self.scenario: str = d.get("scenario", "adhoc")
        self.user_input: str = d.get("user_input", "Hello")
        self.memory: list[dict] = d.get("memory", [])
        self.llm_rules: list[dict] = d.get("llm", [])
        self.default_response: dict = d.get(
            "default_response", {"text": DEFAULTS["sim_fallback_response"]}
        )
        self.tools: dict[str, list[dict]] = d.get("tools", {})
        self.faults: dict = d.get("faults", {})
        self.attacks: list[dict] = d.get("attacks", [])  # [{type, marker, severity}]
        self.episodic: list[dict] = d.get("episodic", [])  # [{ts, text, salience}]
        self.facts: list = d.get("facts", [])  # ["..."] or [{fact, confidence}]
        self.instructions: list = d.get("instructions", [])  # ["..."] or [{text, approved}]
        self.judge_rules: list[dict] = d.get("judge", [])  # [{match: {contains}, scores: {dim: x}}]
        self.default_judge_scores: dict = d.get("default_judge_scores", {})
        self.react_rules: list[dict] = d.get("react", [])  # [{match, action|respond}]

    # ---- llm ----
    def llm_response(self, prompt_text: str) -> str:
        low = prompt_text.lower()
        for rule in self.llm_rules:
            match = rule.get("match", {})
            needle = str(match.get("contains", "")).lower()
            if needle and needle in low:
                return rule.get("respond", {}).get("text", "")
        return self.default_response.get("text", "")

    # ---- tools ----
    def tool_result(self, tool_name: str, query: str) -> list[dict]:
        if self.faults.get("tool_error_rate", 0) >= 1.0:
            raise ToolError(f"simulated failure calling {tool_name}")
        rules = self.tools.get(tool_name, [])
        low = query.lower()
        for rule in rules:
            match = rule.get("match", {})
            needle = str(match.get("query_contains", "")).lower()
            if needle and needle in low:
                return rule.get("result", [])
        # unmatched: empty result (or forced by fault knob)
        return [] if not self.faults.get("empty_results") else []

    @property
    def blocklist(self) -> list[str]:
        return [str(t).lower() for t in self.faults.get("blocklist", [])]

    @property
    def judge_timeouts(self) -> set:
        """Judges the fixture marks as timing out — the judge_chain fails OPEN on
        these (a banner, not a halt): an unavailable judge must not block a
        response."""
        return {str(n).lower() for n in self.faults.get("judge_timeout", [])}

    # ---- react ----
    def react_decision(self, transcript: str, used: set) -> tuple[int | None, dict | None]:
        """Scripted ReAct step: first UNUSED rule whose needle appears in the transcript
        (question + accumulated observations). As observations land, later rules start
        matching — that's how the scripted 'model' reacts to what its tools returned."""
        low = transcript.lower()
        for i, rule in enumerate(self.react_rules):
            if i in used:
                continue
            needle = str(rule.get("match", {}).get("contains", "")).lower()
            if needle and needle in low:
                return i, rule
        return None, None

    # ---- judge ----
    def judge_scores(self, text: str, rubric: list[str]) -> dict[str, float]:
        """Scripted LLM-judge scores: first matching rule wins, then fixture defaults,
        then an honest flat 0.75 (the sim judge has no opinion it wasn't given)."""
        low = text.lower()
        scores: dict = {}
        for rule in self.judge_rules:
            needle = str(rule.get("match", {}).get("contains", "")).lower()
            if needle and needle in low:
                scores = rule.get("scores", {})
                break
        return {
            dim: round(float(scores.get(dim, self.default_judge_scores.get(dim, 0.75))), 4)
            for dim in rubric
        }


class ToolError(RuntimeError):
    pass


def load_fixture(source: str | Path | dict | None) -> Fixture:
    if source is None:
        return Fixture()
    if isinstance(source, dict):
        return Fixture(source)
    return Fixture(yaml.safe_load(Path(source).read_text()))


class SimLLMProvider:
    """LLMProvider sim implementation. Real adapters (anthropic/ollama)
    live behind this same interface in providers.py — flipping is one graph param."""

    deterministic = True
    locality = "sim"  # egress destination for model calls

    def __init__(self, name: str, fixture: Fixture):
        self.name = name
        self.fixture = fixture

    def complete(self, prompt: str, temperature: float = 0.2, max_tokens: int = 512) -> Completion:
        text = self.fixture.llm_response(prompt)
        return Completion(
            text=text,
            input_tokens=approx_tokens(prompt),
            output_tokens=approx_tokens(text),
            cost_usd=0.0,
        )


class SimVectorStore:
    """Deterministic scoring over candidate documents (VectorStore interface).

    Uses score_hint from the fixture when present; otherwise a stable word-overlap score.
    Corruptible via fixture faults for failure labs.
    """

    @staticmethod
    def score(query: str, doc: dict) -> float:
        if "score_hint" in doc:
            return float(doc["score_hint"])
        q = set(query.lower().split())
        text = f"{doc.get('subject','')} {doc.get('snippet','')} {doc.get('text','')}".lower()
        d = set(text.split())
        if not q or not d:
            return 0.0
        overlap = len(q & d) / len(q)
        # stable jitter from content hash so ties break deterministically
        h = int(hashlib.sha256(text.encode()).hexdigest()[:6], 16) / 0xFFFFFF * 0.01
        return round(min(1.0, overlap + h), 4)

    @classmethod
    def query(cls, query: str, docs: list[dict], top_k: int, min_score: float) -> list[dict]:
        scored = [{**d, "_score": cls.score(query, d)} for d in docs]
        kept = [d for d in scored if d["_score"] >= min_score]
        kept.sort(key=lambda d: (-d["_score"], str(d.get("id", ""))))
        return kept[:top_k]


REDACTION_RULES: dict[str, str] = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.]+\b",
    "phone": r"\b\d{3}[-.]\d{3}[-.]\d{4}\b",
    "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
}


def redact(text: str, rules: list[str], mask: str = "████") -> tuple[str, int, list[str]]:
    """Deterministic rule-selectable redaction — pure regex, provable, unit-testable."""
    import re

    count, hit = 0, []
    for name in rules:
        pattern = REDACTION_RULES.get(name)
        if not pattern:
            continue
        text, n = re.subn(pattern, mask, text)
        if n:
            hit.append(name)
        count += n
    return text, count, hit


def redact_pii(text: str) -> tuple[str, int]:
    """All-rules redaction (pii_scrub interceptor keeps its original contract)."""
    out, count, _ = redact(text, list(REDACTION_RULES))
    return out, count
