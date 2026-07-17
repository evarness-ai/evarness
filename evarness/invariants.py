"""Invariant contracts — declared assertions checked against the event stream.

A contract states what must (or must never) appear in a run's trace, by name:

    approval-precedes-send:
      description: a send-capable tool never fires without a prior human approval
      assert:
        precedes:
          first:  {type: approval_granted}
          second: {type: tool_called, where: {tool: email.send}}

Primitives (exactly one per contract):
- ``never``      — no event matches (optional ``after`` scopes it to events
                   following the first match of another matcher)
- ``eventually`` — at least one event matches
- ``every``      — all events matching ``match`` satisfy a condition
                   (``where`` payload constraints and/or a registered check)
- ``precedes``   — every event matching ``second`` has an earlier event
                   matching ``first``

A matcher is ``{type?, node_id?, where?, satisfies?}``. ``where`` constrains
payload fields (dot-paths allowed): a bare value means equality, and
``{in: [...]}, {gt/gte/lt/lte: n}, {contains: s}`` are the operators.
``satisfies`` names a Python predicate registered with
``@register_invariant_check`` — the escape hatch for anything the declarative
syntax can't express.

Resolution order for definitions (highest wins), same overlay discipline as
prompts/ui/grounding: pattern-local ``invariants.yaml`` (passed in by the
caller as ``extra``) > ``~/.evarness/invariants.yaml`` ($EVARNESS_INVARIANTS)
> packaged ``invariants.yaml``. A graph opts in via ``params.invariants: [ids]``.

Honesty rules: an unknown invariant id, an unknown ``satisfies`` check, or an
unparseable definition is a FAILED verdict (a typo'd contract must not pass CI),
never a silent skip. ``every``/``precedes`` with zero matching events pass
vacuously — assert presence separately with ``eventually`` when you need it.

Verdicts live OUTSIDE the event stream: the trace is the evidence and the
verdict is about the evidence, so checking a run never changes its canonical
digest. Results attach to RunResult.invariants, are persisted with the
run, and are re-checkable from stored events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .prompts import load_overlaid_yaml

# ------------------------------------------------------------- check registry

CHECKS: dict[str, Callable[[dict], bool]] = {}


def register_invariant_check(name: str):
    """Register a named predicate usable as ``satisfies: <name>`` in matchers.
    The function receives one event dict ``{seq, ts, node_id, type, payload}``
    and returns truthy when the event satisfies the condition."""
    def deco(fn: Callable[[dict], bool]):
        CHECKS[name] = fn
        return fn
    return deco


@register_invariant_check("nonempty_output")
def _nonempty_output(event: dict) -> bool:
    """The event's payload carries a non-empty ``output``/``text``/``answer``."""
    p = event.get("payload", {})
    return bool(str(p.get("output") or p.get("text") or p.get("answer") or "").strip())


# ------------------------------------------------------------- definitions

_PACKAGED = Path(__file__).parent / "invariants.yaml"
_SECTIONS = ("invariants",)


def load_invariant_defs(extra: dict | None = None) -> dict[str, dict]:
    """Merged contract definitions: packaged < user overlay < caller-supplied
    ``extra`` (pattern-local)."""
    data = load_overlaid_yaml(_PACKAGED, "EVARNESS_INVARIANTS",
                              Path.home() / ".evarness" / "invariants.yaml",
                              _SECTIONS)
    defs = dict(data.get("invariants") or {})
    if extra:
        defs.update(extra)
    return defs


# ------------------------------------------------------------- matching

_OPS = {
    "in": lambda v, arg: v in arg,
    "gt": lambda v, arg: isinstance(v, (int, float)) and v > arg,
    "gte": lambda v, arg: isinstance(v, (int, float)) and v >= arg,
    "lt": lambda v, arg: isinstance(v, (int, float)) and v < arg,
    "lte": lambda v, arg: isinstance(v, (int, float)) and v <= arg,
    "contains": lambda v, arg: arg in str(v),
}


class InvariantConfigError(Exception):
    """A contract that cannot even be checked — always a failed verdict."""


def _payload_get(payload: dict, dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _where_ok(payload: dict, where: dict) -> bool:
    for field, cond in (where or {}).items():
        val = _payload_get(payload, field)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op not in _OPS:
                    raise InvariantConfigError(f"unknown where-operator '{op}'")
                if not _OPS[op](val, arg):
                    return False
        elif val != cond:
            return False
    return True


def _matches(event: dict, matcher: dict) -> bool:
    if not isinstance(matcher, dict) or not matcher:
        raise InvariantConfigError("matcher must be a non-empty mapping")
    if "type" in matcher and event.get("type") != matcher["type"]:
        return False
    if "node_id" in matcher and event.get("node_id") != matcher["node_id"]:
        return False
    if not _where_ok(event.get("payload", {}), matcher.get("where", {})):
        return False
    if "satisfies" in matcher:
        name = matcher["satisfies"]
        if name not in CHECKS:
            raise InvariantConfigError(f"unknown satisfies-check '{name}'")
        if not CHECKS[name](event):
            return False
    return True


# ------------------------------------------------------------- primitives

def _assert_never(spec: dict, events: list[dict]) -> tuple[bool, str, list[int]]:
    matcher = {k: v for k, v in spec.items() if k != "after"}
    scope = events
    if "after" in spec:
        idx = next((i for i, e in enumerate(events) if _matches(e, spec["after"])), None)
        scope = events[idx + 1:] if idx is not None else []
    hits = [e["seq"] for e in scope if _matches(e, matcher)]
    if hits:
        return False, f"{len(hits)} forbidden event(s) matched", hits[:5]
    return True, "no forbidden event", []


def _assert_eventually(spec: dict, events: list[dict]) -> tuple[bool, str, list[int]]:
    for e in events:
        if _matches(e, spec):
            return True, "matched", [e["seq"]]
    return False, "no event matched", []


def _assert_every(spec: dict, events: list[dict]) -> tuple[bool, str, list[int]]:
    match = spec.get("match")
    condition = {k: spec[k] for k in ("where", "satisfies") if k in spec}
    if not match or not condition:
        raise InvariantConfigError("'every' needs 'match' and a 'where'/'satisfies' condition")
    selected = [e for e in events if _matches(e, match)]
    bad = [e["seq"] for e in selected if not _matches(e, condition)]
    if bad:
        return False, f"{len(bad)}/{len(selected)} matching event(s) violate the condition", bad[:5]
    return True, f"all {len(selected)} matching event(s) satisfy the condition", []


def _assert_precedes(spec: dict, events: list[dict]) -> tuple[bool, str, list[int]]:
    first, second = spec.get("first"), spec.get("second")
    if not first or not second:
        raise InvariantConfigError("'precedes' needs 'first' and 'second' matchers")
    seen_first = False
    bad: list[int] = []
    for e in events:
        if not seen_first and _matches(e, first):
            seen_first = True
        if _matches(e, second) and not seen_first:
            bad.append(e["seq"])
    if bad:
        return False, f"{len(bad)} event(s) occurred without the required predecessor", bad[:5]
    return True, "ordering holds", []


_PRIMITIVES = {"never": _assert_never, "eventually": _assert_eventually,
               "every": _assert_every, "precedes": _assert_precedes}


# ------------------------------------------------------------- checking

def check_invariants(ids: list[str], events: list[dict],
                     extra: dict | None = None) -> dict:
    """Evaluate the graph's declared invariants against a finished run's events.

    Returns ``{passed, failed, results: [{id, ok, detail, evidence_seq}]}``.
    Misconfiguration (unknown id, unknown primitive/check/operator) is a failed
    verdict with the reason in ``detail`` — never a silent skip.
    """
    defs = load_invariant_defs(extra)
    results = []
    for inv_id in ids:
        spec = defs.get(inv_id)
        if spec is None:
            results.append({"id": inv_id, "ok": False, "evidence_seq": [],
                            "detail": "unknown invariant — not defined in packaged, "
                                      "overlay, or pattern-local invariants.yaml"})
            continue
        body = (spec or {}).get("assert") or {}
        try:
            if len(body) != 1:
                raise InvariantConfigError(
                    f"'assert' must contain exactly one of {sorted(_PRIMITIVES)}")
            (prim, prim_spec), = body.items()
            if prim not in _PRIMITIVES:
                raise InvariantConfigError(f"unknown primitive '{prim}'")
            ok, detail, evidence = _PRIMITIVES[prim](prim_spec or {}, events)
            results.append({"id": inv_id, "ok": ok, "detail": detail,
                            "evidence_seq": evidence})
        except InvariantConfigError as exc:
            results.append({"id": inv_id, "ok": False, "evidence_seq": [],
                            "detail": f"uncheckable contract: {exc}"})
    return {"passed": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results}
