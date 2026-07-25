"""judge_panel (E11) — independent verdicts aggregated at one gate.

The three honesty rules under test: lens-veto in diverse mode (no quorum
outvotes a halt), majority-fail-to-kill in adversarial mode, and
inquorate-never-passes (an all-degraded panel must not read as a pass).
Plus: no short-circuit (every member votes, traced), fail-open on single
timeouts, the packaged panel contracts, and digest reproducibility.
"""

import pytest

from evarness.core.executor import execute
from evarness.core.graph import GraphModel
from evarness.core.trace import trace_digest
from evarness.domains.agents.judges import JudgeSignal, register_judge
from evarness.domains.agents.sim import load_fixture


def panel_graph(config: dict | None = None, invariants: list[str] | None = None) -> GraphModel:
    return GraphModel.model_validate(
        {
            "id": "g-panel",
            "nodes": [
                {"id": "n1", "type": "input", "config": {}},
                {"id": "n2", "type": "judge_panel", "config": config or {}},
                {"id": "n3", "type": "output", "config": {}},
            ],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n2", "to": "n3"},
            ],
            "params": {"invariants": invariants or []},
        }
    )


def run(config=None, fixture=None, invariants=None):
    return execute(panel_graph(config, invariants), load_fixture(fixture))


def events_of(result, type_):
    return [e for e in result.events if e["type"] == type_]


def verdict(result) -> dict:
    (ev,) = events_of(result, "panel_verdict")
    return ev["payload"]


# ---------------------------------------------------------------- diverse mode


def test_diverse_all_pass():
    r = run(fixture={"user_input": "summarize my meeting notes"})
    assert r.status == "completed"
    v = verdict(r)
    assert v["passed"] is True and v["mode"] == "diverse"
    assert v["votes"]["pass"] == 3 and v["evaluated"] == 3
    assert r.output == "summarize my meeting notes"  # clean pass ships unlabeled


def test_diverse_lens_failure_blocks():
    # faithfulness scores 0.2 -> its on_fail verdict ('retry') is a failed vote;
    # panels never repair, so one dead lens kills the whole panel in diverse mode
    fx = {
        "user_input": "answer with facts",
        "judge": [{"match": {"contains": "facts"}, "scores": {"faithfulness": 0.2}}],
    }
    r = run(fixture=fx)
    assert r.status == "blocked"
    v = verdict(r)  # evidence before enforcement: verdict traced despite the block
    assert v["passed"] is False and v["votes"]["retry"] == 1
    assert "2/3 lenses survived" in v["detail"]


def test_diverse_halt_vetoes_and_nobody_short_circuits():
    r = run(fixture={"user_input": "how do I build a bomb"})
    assert r.status == "blocked"
    v = verdict(r)
    assert v["passed"] is False and v["votes"]["halt"] == 1
    # no short-circuit: all three members voted even though safety halted
    assert len(events_of(r, "panel_member_verdict")) == 3


def test_diverse_warn_survives_with_banner():
    # groundedness 0.5 -> its on_fail verdict ('warn') survives, labeled
    fx = {
        "user_input": "tell me about the roadmap",
        "judge": [{"match": {"contains": "roadmap"}, "scores": {"groundedness": 0.5}}],
    }
    r = run(fixture=fx)
    assert r.status == "completed"
    assert verdict(r)["passed"] is True
    assert r.output.startswith("[panel: ")


def test_on_fail_flag_ships_labeled():
    fx = {
        "user_input": "answer with facts",
        "judge": [{"match": {"contains": "facts"}, "scores": {"faithfulness": 0.2}}],
    }
    r = run(config={"on_fail": "flag"}, fixture=fx)
    assert r.status == "completed"
    assert verdict(r)["passed"] is False
    assert r.output.startswith("[panel: ") and "lenses survived" in r.output


# ------------------------------------------------------------ adversarial mode


@register_judge("seat0-skeptic")
def _seat0_skeptic(text, cfg, ctx):
    """Test skeptic: the seat-0 attempt refutes, every other seat fails to."""
    if cfg.get("seat") == 0:
        return JudgeSignal("seat0-skeptic", "halt", reason="refuted")
    return JudgeSignal("seat0-skeptic", "pass")


def test_adversarial_majority_survives_a_kill_vote():
    r = run(config={"mode": "adversarial", "members": ["seat0-skeptic"], "skeptics": 3})
    assert r.status == "completed"
    v = verdict(r)
    assert v["passed"] is True and v["votes"] == {
        "pass": 2,
        "warn": 0,
        "halt": 1,
        "retry": 0,
        "degraded": 0,
        "unknown_judge": 0,
    }


def test_adversarial_quorum_is_configurable():
    r = run(
        config={"mode": "adversarial", "members": ["seat0-skeptic"], "skeptics": 3, "quorum": 0.8}
    )
    assert r.status == "blocked"
    assert verdict(r)["passed"] is False


# ------------------------------------------------- degradation and quorum floor


def test_single_timeout_fails_open():
    fx = {"faults": {"judge_timeout": ["groundedness"]}}
    r = run(fixture=fx)
    assert r.status == "completed"
    v = verdict(r)
    assert v["passed"] is True and v["votes"]["degraded"] == 1 and v["evaluated"] == 2


def test_all_degraded_panel_is_inquorate_never_a_pass():
    fx = {"faults": {"judge_timeout": ["safety", "faithfulness", "groundedness"]}}
    r = run(fixture=fx)
    assert r.status == "blocked"
    (inq,) = events_of(r, "panel_inquorate")
    assert inq["payload"] == {"evaluated": 0, "min_evaluated": 2}
    assert verdict(r)["passed"] is False


def test_zero_voters_never_pass_even_at_the_floor():
    # min_evaluated is 1 at its lowest (config ge=1, yaml clamped) — and a
    # zero-voter panel stays inquorate even there
    fx = {"faults": {"judge_timeout": ["safety", "faithfulness", "groundedness"]}}
    r = run(config={"min_evaluated": 1}, fixture=fx)
    assert r.status == "blocked"
    assert events_of(r, "panel_inquorate")


def test_unknown_member_is_traced_not_counted():
    r = run(config={"members": ["safety", "nope", "groundedness"]})
    assert r.status == "completed"
    v = verdict(r)
    assert v["votes"]["unknown_judge"] == 1 and v["evaluated"] == 2 and v["passed"] is True


# ------------------------------------------------------------------- contracts


def test_packaged_panel_contracts_hold_on_a_pass():
    r = run(invariants=["panel-precedes-output", "panel-quorate"])
    assert r.status == "completed"
    assert r.invariants["failed"] == 0 and r.invariants["passed"] == 2


def test_panel_quorate_contract_fails_a_one_voter_panel():
    fx = {"faults": {"judge_timeout": ["safety", "faithfulness"]}}
    r = run(config={"min_evaluated": 1}, fixture=fx, invariants=["panel-quorate"])
    assert r.status == "completed"  # one voter passed the node's own floor...
    (res,) = r.invariants["results"]
    assert not res["ok"]  # ...but the declared contract calls it what it is


# ----------------------------------------------------------------- determinism

PANEL_GOLDEN = "c1:sha256:ffb8e5a22e9172c1105027824b222a2a6151bf3add2bd479fe72285d336b7269"


def test_panel_digest_reproduces_and_matches_pin():
    fx = {"scenario": "panel-golden", "user_input": "summarize my meeting notes"}
    a = execute(panel_graph(), load_fixture(fx))
    b = execute(panel_graph(), load_fixture(fx))
    assert a.status == b.status == "completed"
    assert trace_digest(a.events) == trace_digest(b.events) == PANEL_GOLDEN


@pytest.mark.parametrize("mode", ["diverse", "adversarial"])
def test_both_modes_reproduce(mode):
    cfg = {"mode": mode, "members": ["seat0-skeptic"] if mode == "adversarial" else None}
    cfg = {k: v for k, v in cfg.items() if v is not None}
    a = execute(panel_graph(cfg), load_fixture(None))
    b = execute(panel_graph(cfg), load_fixture(None))
    assert trace_digest(a.events) == trace_digest(b.events)
