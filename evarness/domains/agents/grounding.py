"""Grounding rules — deterministic answer-faithfulness checks, as a
user-extensible extension point.

Born from a live distortion the evidence guard could not catch: the model HAD real
sources (8 search results) but garbled a headline — "US, soccer politics at World
Cup" became "the U.S. Open controversy" — a fabricated event name attached to a
genuine source. Evidence *presence* was guaranteed; evidence *faithfulness* wasn't.

Platform shape: the CODE of a
rule is registered under a name; its BEHAVIOR is YAML config; the harness picks
rules per node. Bring your own rule without a core edit::

    from evarness.domains.agents.grounding import register_grounding_rule

    @register_grounding_rule("citation_required")
    def citation_required(answer, evidence, cfg, context):
        # cfg = this rule's section from grounding.yaml (+ user overlay)
        # context = {"question", "queries", "observations"} from the loop
        return ["missing citation"] if "http" not in answer else []

Three governance dimensions, one registry (all found live, in order):
- evidence PRESENCE — did the model consult tools at all?
- evidence FAITHFULNESS — ``entity_support``: no fabricated entities/figures
- request COVERAGE — ``topic_coverage`` / ``count_intent`` / ``semantic_coverage``:
  did the answer address every topic and honor the requested count? (a
  faithful answer that ignores half the request is still a failure)

then add ``citation_required`` to a loop node's ``grounding_rules`` list — it is
now per-harness config, sweepable in Experiments, and tunable in
``~/.evarness/grounding.yaml`` (or ``$EVARNESS_GROUNDING``), which merges
per-rule over the packaged ``grounding.yaml``.

Built-in rule ``entity_support`` — what the live experiments taught (all encoded,
regression-tested):
- possessives chain DIFFERENT entities — "Balogun's World Cup" is Balogun + World
  Cup, and the evidence never contains that contiguous phrase; split before matching
- normalization must strip possessives ("China's" -> china) and tolerate
  singular/plural ("Olympic" vs "Olympics"), or real runs false-positive
- a lone capitalized word at sentence start is capitalization, not an entity, and
  a sentence-start function word is not part of one ("The U.S. Open ...")
- lowercase paraphrase ("labor shortages" for "worker shortfall") is legitimate
  summarization — only entities and figures are gated, never wording
- contiguous-phrase matching is TOO STRICT (found live, run 01e3499856e3): models
  legitimately COMPOSE supported fragments — "U.S. Senator Lindsey Graham" from
  "the senior U.S. senator" + "Lindsey Graham", or section headers like "Global
  Politics" from a "global ... politics" question. The harm signal is a word with
  NO support anywhere in the evidence ("Open"), so the rule is word-level: a span
  is flagged only when one of its words appears nowhere in the evidence.
- markdown answers hide structure (found live, run 1cf3148bf730): "3. **Economy**:"
  is a list-item label, i.e. sentence-start capitalization — strip emphasis/list
  markers before the sentence-start test.

Scope honesty: recompositions of individually-supported words ("World Cup" +
"Los Angeles" -> "Los Angeles Cup") and unsupported claims made entirely in
lowercase prose pass entity_support. It narrows the gap deterministically; an
llm_judge downstream — or your own registered rule — can narrow it further.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

import yaml

# a rule: (answer, evidence, rule_cfg, context) -> list of violations. context
# carries the loop's structured view: {"question": str, "queries": [str],
# "observations": str} — empty dict when a rule is called outside a loop.
GroundingRule = Callable[[str, str, dict, dict], list]

_RULES: dict[str, GroundingRule] = {}
DEFAULT_RULES = ["entity_support"]


def register_grounding_rule(name: str):
    """Register a grounding rule under ``name`` (rules are pure functions)."""

    def deco(fn: GroundingRule) -> GroundingRule:
        _RULES[name.lower()] = fn
        return fn

    return deco


def get_grounding_rule(name: str) -> GroundingRule | None:
    return _RULES.get((name or "").lower())


def available_grounding_rules() -> list[str]:
    return sorted(_RULES)


# ------------------------------------------------------------------ YAML config

_PACKAGED = Path(__file__).parent / "grounding.yaml"
_config_cache: dict | None = None


def _overlay_path() -> Path:
    return Path(
        os.environ.get("EVARNESS_GROUNDING", str(Path.home() / ".evarness" / "grounding.yaml"))
    )


def rules_config() -> dict:
    """Packaged grounding.yaml with the user overlay merged per rule — an overlay
    can override one knob of a built-in rule or configure a bring-your-own rule
    without copying the packaged file."""
    global _config_cache
    if _config_cache is None:
        cfg = yaml.safe_load(_PACKAGED.read_text()) or {}
        rules = dict(cfg.get("rules") or {})
        overlay = _overlay_path()
        if overlay.is_file():
            user = yaml.safe_load(overlay.read_text()) or {}
            for name, knobs in (user.get("rules") or {}).items():
                merged = dict(rules.get(name) or {})
                merged.update(knobs or {})
                rules[name] = merged
        _config_cache = {"rules": rules}
    return _config_cache


def reload_rules_config() -> None:
    """Drop the cached config (tests; picking up an edited overlay)."""
    global _config_cache
    _config_cache = None


def rule_config(name: str) -> dict:
    return rules_config()["rules"].get((name or "").lower(), {})


# ------------------------------------------------------------------ dispatch


def check_grounding(
    answer: str, evidence: str, rules: list[str] | None = None, context: dict | None = None
) -> tuple[list[str], list[str]]:
    """Run the named rules; returns (violations, not_run). ``not_run`` lists rules
    that could not check anything — unknown names and rules that errored (e.g. a
    missing optional dependency) — annotated with why. Never silently dropped:
    a rule that quietly checks nothing would be fake governance."""
    violations: list[str] = []
    not_run: list[str] = []
    for name in (rules if rules is not None else DEFAULT_RULES):
        fn = get_grounding_rule(name)
        if fn is None:
            not_run.append(name)
            continue
        try:
            found = fn(answer, evidence, rule_config(name), context or {})
        except Exception as exc:  # e.g. [semantic] extra not installed
            not_run.append(f"{name}: {exc}")
            continue
        for claim in found:
            if claim not in violations:
                violations.append(claim)
    return violations, not_run


# ------------------------------------------------------------------ built-in rule

_SPAN = re.compile(r"(?:[A-Z][\w.'’-]*|\$?\d[\w.,%$-]*)(?:[ ](?:[A-Z][\w.'’-]*|\$?\d[\w.,%$-]*))*")
_SENT_END = ".!?;:\n"


def _tokens(text: str) -> list[str]:
    # possessive -> bare word BEFORE stripping apostrophes ("China's" -> "china")
    text = re.sub(r"([A-Za-z])[’']s?\b", r"\1", text)
    text = text.lower().replace(".", "")
    return re.findall(r"[a-z0-9$%]+", text)


def _word_supported(word: str, ev: set[str], plural_tolerance: bool) -> bool:
    if word in ev:
        return True
    if plural_tolerance:
        # "olympic" matches "olympics"
        return word + "s" in ev or (word.endswith("s") and word[:-1] in ev)
    return False


@register_grounding_rule("entity_support")
def entity_support(answer: str, evidence: str, cfg: dict, context: dict) -> list[str]:
    """Entity spans / figures in ``answer`` containing a word that appears nowhere
    in ``evidence``. Empty list = every checkable claim is supported (by this rule).
    Knobs (grounding.yaml): stop_capitals, plural_tolerance, split_possessives,
    max_reported."""
    stopcaps = set(cfg.get("stop_capitals") or [])
    plural = bool(cfg.get("plural_tolerance", True))
    split_poss = bool(cfg.get("split_possessives", True))
    max_reported = int(cfg.get("max_reported", 8))

    ev = set(_tokens(evidence))
    out: list[str] = []
    seen: set[str] = set()
    for m in _SPAN.finditer(answer):
        span = m.group().strip(" .,;:'")
        # markdown markup hides the structural context of a span — "3. **Economy**:"
        # is a list-item label (found live, run 1cf3148bf730), so strip emphasis/
        # list/header markers before deciding whether the span starts a sentence.
        # Strip spaces only (NOT the newline): a list line whose predecessor lacks
        # ending punctuation is still a line start (found live, run 3a6382391999)
        prev = answer[: m.start()].rstrip(" \t").rstrip("*#_>•-").rstrip(" \t")
        at_sentence_start = not prev or prev[-1] in _SENT_END
        # an enumeration marker is structure, not a claim — "1. AI ..." must not
        # flag the "1" (found live, run 5adfc56a3a7f); what follows it is
        # sentence-start capitalization
        if at_sentence_start:
            span = re.sub(r"^\d{1,2}[.)]\s*", "", span)
            if not span:
                continue
        words = span.split()
        # sentence-start function words are capitalization, not part of the entity
        # ("The U.S. Open dominates." -> entity is "U.S. Open"); words after a
        # stripped stopword are capitalized by CHOICE, so they stay checkable
        stripped = False
        while at_sentence_start and words and words[0] in stopcaps:
            words, stripped = words[1:], True
        if not words:
            continue
        span = " ".join(words)
        # a lone capitalized word at sentence start is capitalization, not an entity
        if len(words) == 1 and at_sentence_start and not stripped:
            continue
        if len(words) == 1 and len(words[0]) < 2:
            continue  # "I", "A"
        # possessives chain DIFFERENT entities ("Balogun's World Cup" = Balogun +
        # World Cup) — split there so the report names the offending sub-entity
        subs = re.split(r"(?<=[’'])s?\s+|[’']s\s+", span) if split_poss else [span]
        for sub in subs:
            sub = sub.strip(" .,;:'’")
            toks = _tokens(sub)
            if not toks:
                continue
            key = " ".join(toks)
            if key in seen:
                continue
            seen.add(key)
            if not all(_word_supported(t, ev, plural) for t in toks):
                out.append(sub)
                if len(out) >= max_reported:
                    return out
    return out


def unsupported_entities(answer: str, evidence: str) -> list[str]:
    """Back-compat convenience: the built-in entity_support rule with its
    configured knobs."""
    return entity_support(answer, evidence, rule_config("entity_support"), {})


# ------------------------------------------------------- request-coverage rules


def requested_count(question: str) -> int | None:
    """The item count the request contracts for ("top 10" -> 10), from the
    count_intent patterns, capped by max_count. None when no count is asked."""
    cfg = rule_config("count_intent")
    for pat in cfg.get("patterns") or []:
        m = re.search(pat, question, re.IGNORECASE)
        if m:
            return min(int(m.group(1)), int(cfg.get("max_count", 20)))
    return None


@register_grounding_rule("topic_coverage")
def topic_coverage(answer: str, evidence: str, cfg: dict, context: dict) -> list[str]:
    """Every content word of the REQUEST must appear in some tool query or
    observation — "AI news and FIFA controversies" is not answered by searching
    only AI (found live, run 63b134fb1fb0). Deterministic and explainable.
    Users typo their requests ("controvercies", "word cup") and the model
    searches the CORRECT spelling — bounded edit distance bridges that
    deterministically (measured: embeddings do not, MiniLM scored the typo 0.26)."""
    question = context.get("question", "")
    if not question:
        return []
    stop = set(cfg.get("stopwords") or [])
    min_len = int(cfg.get("min_word_len", 3))
    plural = bool(cfg.get("plural_tolerance", True))
    typo = bool(cfg.get("typo_tolerance", True))
    typo_min = int(cfg.get("typo_min_len", 4))
    typo_two = int(cfg.get("typo_len_for_two", 8))
    max_reported = int(cfg.get("max_reported", 6))
    covered = set(
        _tokens(" ".join(context.get("queries") or []) + " " + context.get("observations", ""))
    )
    missing, seen = [], set()
    for tok in _tokens(question):
        if len(tok) < min_len or tok in stop or tok.isdigit() or tok in seen:
            continue
        seen.add(tok)
        if _word_supported(tok, covered, plural):
            continue
        if typo and _typo_covered(tok, covered, typo_min, typo_two):
            continue
        missing.append(tok)
    return [f"request topic '{w}' was never searched or retrieved" for w in missing[:max_reported]]


def _typo_covered(word: str, ev: set[str], min_len: int, len_for_two: int) -> bool:
    k = 2 if len(word) >= len_for_two else 1 if len(word) >= min_len else 0
    if not k:
        return False
    return any(abs(len(word) - len(t)) <= k and _edit_le(word, t, k) for t in ev)


def _edit_le(a: str, b: str, k: int) -> bool:
    """Bounded Levenshtein: distance(a, b) <= k, with early exit."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > k:
            return False
        prev = cur
    return prev[-1] <= k


@register_grounding_rule("count_intent")
def count_intent(answer: str, evidence: str, cfg: dict, context: dict) -> list[str]:
    """ "top 10" is a contract: enumerate that many items or say honestly how many
    were found (found live, run 63b134fb1fb0: "top 10" answered with 7 prose
    fragments). Honesty phrases let a genuine shortfall pass."""
    n = requested_count(context.get("question", ""))
    if not n:
        return []
    # count enumerated items: numbered/bulleted lines, or inline "1. ... 2. ..."
    lines = len(re.findall(r"(?m)^\s*(?:\d{1,2}[.)]|[-*•])\s+", answer))
    inline = len(set(re.findall(r"\b(\d{1,2})[.)]\s", answer)))
    k = max(lines, inline)
    if k >= n:
        return []
    low = answer.lower()
    if any(p in low for p in (cfg.get("honesty_phrases") or [])):
        return []
    if any(re.search(p, answer) for p in (cfg.get("honesty_patterns") or [])):
        return []  # "(3/10)" — an admitted shortfall, found live
    return [f"the request asked for {n} items but the answer enumerates {k}"]


@register_grounding_rule("semantic_coverage")
def semantic_coverage(answer: str, evidence: str, cfg: dict, context: dict) -> list[str]:
    """Paraphrase-aware twin of topic_coverage: each request facet must reach the
    cosine-similarity threshold against some query/observation. Needs the
    [semantic] extra (fastembed: ONNX MiniLM, local, no torch); without it the
    rule lands in grounding_checked as not-run — visible, never silent."""
    question = context.get("question", "")
    if not question:
        return []
    threshold = float(cfg.get("threshold", 0.55))
    max_reported = int(cfg.get("max_reported", 6))
    model = str(cfg.get("model", "sentence-transformers/all-MiniLM-L6-v2"))
    facets = _facets(question)
    corpus = [q for q in (context.get("queries") or []) if q.strip()]
    corpus += [
        ln.strip() for ln in re.split(r"[;\n]", context.get("observations", "")) if ln.strip()
    ]
    if not facets or not corpus:
        return []
    vecs = _embed_texts(facets + corpus, model)
    fv, cv = vecs[: len(facets)], vecs[len(facets) :]
    out = []
    for facet, v in zip(facets, fv):
        best = max(_cos(v, c) for c in cv)
        if best < threshold:
            out.append(
                f"request facet '{facet}' not covered "
                f"(best similarity {best:.2f} < {threshold})"
            )
            if len(out) >= max_reported:
                break
    return out


def _facets(question: str) -> list[str]:
    """Split a request into topic facets at conjunctions/punctuation, dropping
    facets that are only stopwords/counts ("fetch the top 10" contributes none)."""
    stop = set(rule_config("topic_coverage").get("stopwords") or [])
    facets = []
    for part in re.split(r",|;|\band\b|\bplus\b|\balso\b", question, flags=re.IGNORECASE):
        toks = [t for t in _tokens(part) if t not in stop and not t.isdigit()]
        if toks:
            facets.append(" ".join(toks))
    return facets


_EMBED_CACHE: dict[str, Any] = {}


def _embed_texts(texts: list[str], model: str) -> list[list[float]]:
    """Embed via fastembed (ONNX, local). Module-level seam: tests fake this;
    the ImportError message tells the user exactly what to install."""
    try:
        from fastembed import TextEmbedding  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "semantic_coverage needs the [semantic] extra (pip install 'evarness[semantic]')"
        ) from exc
    if model not in _EMBED_CACHE:
        _EMBED_CACHE[model] = TextEmbedding(model_name=model)
    return [list(v) for v in _EMBED_CACHE[model].embed(texts)]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


__all__ = [
    "register_grounding_rule",
    "get_grounding_rule",
    "available_grounding_rules",
    "check_grounding",
    "rule_config",
    "reload_rules_config",
    "unsupported_entities",
    "requested_count",
    "DEFAULT_RULES",
]
