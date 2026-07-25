"""Invariant contracts (D52) — primitives, resolution order, honesty rules,
engine/CLI integration, and pattern dogfooding."""

import json

import pytest

from evarness.core import store
from evarness.domains.agents import patterns
from evarness.core.executor import execute
from evarness.core.invariants import check_invariants, load_invariant_defs, register_invariant_check
from evarness.core.graph import GraphModel
from evarness.domains.agents.sim import load_fixture

FLAGSHIP = "governed_email_assistant"
GATED = "approval_gated_send"


def load(pattern_id: str, fixture: str = "happy"):
    graph = GraphModel.model_validate(patterns.load_pattern(pattern_id))
    fx = load_fixture(patterns.fixture_path(pattern_id, fixture))
    return graph, fx


def ev(seq, type_, **payload):
    return {"seq": seq, "ts": 0.0, "node_id": "nX", "type": type_, "payload": payload}


def one(ids, events, extra=None):
    return check_invariants(ids, events, extra=extra)["results"][0]


# ---------------------------------------------------------------- primitives

SEND = ev(5, "tool_called", tool="email.send")
OTHER_TOOL = ev(5, "tool_called", tool="email.search")
GRANT = ev(3, "approval_granted")
REJECT = ev(3, "approval_rejected")

D = {  # local definitions used via `extra`
    "no-send": {"assert": {"never": {"type": "tool_called", "where": {"tool": "email.send"}}}},
    "no-send-after-reject": {
        "assert": {
            "never": {
                "type": "tool_called",
                "where": {"tool": "email.send"},
                "after": {"type": "approval_rejected"},
            }
        }
    },
    "sends-something": {"assert": {"eventually": {"type": "tool_called"}}},
    "grant-before-send": {
        "assert": {
            "precedes": {
                "first": {"type": "approval_granted"},
                "second": {"type": "tool_called", "where": {"tool": "email.send"}},
            }
        }
    },
    "responses-nonempty": {
        "assert": {"every": {"match": {"type": "llm_response"}, "satisfies": "nonempty_output"}}
    },
}


def test_never_fails_on_match_with_evidence():
    r = one(["no-send"], [GRANT, SEND], extra=D)
    assert not r["ok"] and r["evidence_seq"] == [5]


def test_never_passes_without_match():
    assert one(["no-send"], [GRANT, OTHER_TOOL], extra=D)["ok"]


def test_never_after_scopes_to_events_following_the_marker():
    # send BEFORE the rejection is fine; send AFTER it violates
    assert one(["no-send-after-reject"], [SEND, REJECT], extra=D)["ok"]
    assert not one(["no-send-after-reject"], [REJECT, SEND], extra=D)["ok"]


def test_eventually_requires_at_least_one_match():
    assert one(["sends-something"], [SEND], extra=D)["ok"]
    assert not one(["sends-something"], [GRANT], extra=D)["ok"]


def test_precedes_holds_and_fails_and_is_vacuous_without_second():
    assert one(["grant-before-send"], [GRANT, SEND], extra=D)["ok"]
    bad = one(["grant-before-send"], [SEND, GRANT], extra=D)
    assert not bad["ok"] and bad["evidence_seq"] == [5]
    # no send at all -> vacuously true (assert presence with `eventually`)
    assert one(["grant-before-send"], [REJECT], extra=D)["ok"]


def test_every_with_registered_check():
    good = ev(7, "llm_response", output="an answer")
    empty = ev(8, "llm_response", output="  ")
    assert one(["responses-nonempty"], [good], extra=D)["ok"]
    r = one(["responses-nonempty"], [good, empty], extra=D)
    assert not r["ok"] and r["evidence_seq"] == [8]
    # zero matching events pass vacuously
    assert one(["responses-nonempty"], [GRANT], extra=D)["ok"]


# ---------------------------------------------------------------- where ops


def test_where_operators_in_gt_contains_and_dot_path():
    defs = {
        "no-big": {"assert": {"never": {"type": "t", "where": {"tokens": {"gt": 100}}}}},
        "no-classified": {
            "assert": {
                "never": {"type": "t", "where": {"classification": {"in": ["personal", "secret"]}}}
            }
        },
        "no-oops": {"assert": {"never": {"type": "t", "where": {"msg": {"contains": "oops"}}}}},
        "no-nested": {"assert": {"never": {"type": "t", "where": {"meta.kind": "x"}}}},
    }
    assert not one(["no-big"], [ev(1, "t", tokens=101)], extra=defs)["ok"]
    assert one(["no-big"], [ev(1, "t", tokens=100)], extra=defs)["ok"]
    assert not one(["no-classified"], [ev(1, "t", classification="secret")], extra=defs)["ok"]
    assert not one(["no-oops"], [ev(1, "t", msg="well oops indeed")], extra=defs)["ok"]
    assert not one(["no-nested"], [ev(1, "t", meta={"kind": "x"})], extra=defs)["ok"]
    assert one(["no-nested"], [ev(1, "t", meta={"kind": "y"})], extra=defs)["ok"]


# ---------------------------------------------------------------- honesty


def test_unknown_invariant_id_is_a_failed_verdict():
    r = one(["definitely-not-defined"], [])
    assert not r["ok"] and "unknown invariant" in r["detail"]


def test_unknown_check_operator_and_primitive_are_uncheckable_failures():
    bad_op = {"x": {"assert": {"never": {"type": "t", "where": {"a": {"regex": "b"}}}}}}
    r = one(["x"], [ev(1, "t", a="b")], extra=bad_op)
    assert not r["ok"] and "uncheckable" in r["detail"]
    bad_prim = {"y": {"assert": {"sometime": {"type": "t"}}}}
    r = one(["y"], [], extra=bad_prim)
    assert not r["ok"] and "unknown primitive" in r["detail"]
    bad_check = {"z": {"assert": {"every": {"match": {"type": "t"}, "satisfies": "nope"}}}}
    r = one(["z"], [ev(1, "t")], extra=bad_check)
    assert not r["ok"] and "unknown satisfies-check" in r["detail"]


def test_custom_registered_check_is_usable():
    @register_invariant_check("payload_has_flag")
    def _flag(event):
        return bool(event.get("payload", {}).get("flag"))

    defs = {
        "flagged": {"assert": {"every": {"match": {"type": "t"}, "satisfies": "payload_has_flag"}}}
    }
    assert one(["flagged"], [ev(1, "t", flag=True)], extra=defs)["ok"]
    assert not one(["flagged"], [ev(1, "t")], extra=defs)["ok"]


# ---------------------------------------------------------------- resolution


def test_overlay_and_extra_resolution_order(tmp_path, monkeypatch):
    overlay = tmp_path / "invariants.yaml"
    overlay.write_text(
        "invariants:\n"
        "  my-custom:\n    assert: {eventually: {type: run_finished}}\n"
        "  run-completes:\n    assert: {eventually: {type: never_happens}}\n"
    )
    monkeypatch.setenv("EVARNESS_INVARIANTS", str(overlay))
    defs = load_invariant_defs()
    assert "my-custom" in defs  # overlay adds
    assert "no-model-call-after-block" in defs  # packaged still present
    # overlay OVERRIDES packaged: run-completes now demands a bogus event
    assert not one(["run-completes"], [ev(1, "run_finished")])["ok"]
    # pattern-local `extra` wins over the overlay
    extra = {"run-completes": {"assert": {"eventually": {"type": "run_finished"}}}}
    assert one(["run-completes"], [ev(1, "run_finished")], extra=extra)["ok"]


# ---------------------------------------------------------------- engine


def test_flagship_declares_and_passes_its_invariant():
    graph, fx = load(FLAGSHIP)
    run = execute(graph, fx)
    assert run.invariants and run.invariants["failed"] == 0
    ids = [r["id"] for r in run.invariants["results"]]
    assert "no-model-call-after-block" in ids


def test_flagship_blocked_run_still_upholds_the_contract():
    graph, fx = load(FLAGSHIP, "failure")
    run = execute(graph, fx)
    assert run.status == "blocked"
    assert run.invariants["failed"] == 0  # blocked BEFORE the model — honest


def test_gated_pattern_all_contracts_hold_with_pattern_local_defs():
    graph, fx = load(GATED, "send")
    extra = patterns.invariant_defs(GATED)
    assert "no-send-after-rejection" in extra
    run = execute(graph, fx, approvals={"n3": "approve"}, invariant_defs=extra)
    assert run.status == "completed"
    assert run.invariants["failed"] == 0 and run.invariants["passed"] == 3


def test_pattern_local_id_without_extra_is_an_honest_failure():
    # a caller that forgets the pattern context gets a loud failed verdict,
    # never a silent skip
    graph, fx = load(GATED, "send")
    run = execute(graph, fx, approvals={"n3": "approve"})
    bad = [r for r in run.invariants["results"] if not r["ok"]]
    assert [b["id"] for b in bad] == ["no-send-after-rejection"]
    assert "unknown invariant" in bad[0]["detail"]


def test_paused_run_defers_checking_resume_checks():
    graph, fx = load(GATED, "send")
    extra = patterns.invariant_defs(GATED)
    paused = execute(graph, fx, invariant_defs=extra)
    assert paused.status == "paused" and paused.invariants is None
    resumed = execute(
        graph, fx, approvals={paused.pending["node_id"]: "approve"}, invariant_defs=extra
    )
    assert resumed.status == "completed" and resumed.invariants["failed"] == 0


def test_graph_without_invariants_reports_none():
    graph, fx = load(GATED, "send")
    graph.params.invariants = []
    run = execute(graph, fx, approvals={"n3": "approve"})
    assert run.invariants is None


# ---------------------------------------------------------------- persistence


def test_verdicts_persist_with_the_run(tmp_path):
    db = str(tmp_path / "t.db")
    store.init_db(db)
    graph, fx = load(FLAGSHIP)
    run = execute(graph, fx)
    store.save_run(run, "h-test", fx.scenario, graph.params.seed, db_path=db)
    got = store.get_run(run.id, db_path=db)
    assert got["invariants"] == run.invariants


# ---------------------------------------------------------------- publish/bundle


def test_publish_validates_and_bundles_carry_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("EVARNESS_PATTERNS", str(tmp_path / "patterns"))
    graph_doc = patterns.load_pattern(GATED)
    fixtures = {"send": patterns.fixture_text(GATED, "send")}
    inv_yaml = "invariants:\n" "  local-rule:\n" "    assert: {eventually: {type: run_finished}}\n"
    patterns.publish_pattern("my_gated", graph_doc, "lesson", fixtures, invariants_yaml=inv_yaml)
    assert patterns.invariant_defs("my_gated") == {
        "local-rule": {"assert": {"eventually": {"type": "run_finished"}}}
    }
    # bundle round-trip keeps the contract file
    data = patterns.export_bundle("my_gated")
    patterns.delete_pattern("my_gated")
    patterns.import_bundle(data)
    assert "local-rule" in patterns.invariant_defs("my_gated")
    patterns.delete_pattern("my_gated")
    # invalid contracts are rejected at publish time
    with pytest.raises(ValueError, match="invalid invariant"):
        patterns.publish_pattern(
            "my_bad",
            graph_doc,
            "",
            fixtures,
            invariants_yaml="invariants:\n  b:\n    assert: {nope: {}}\n",
        )


# ---------------------------------------------------------------- CLI gate


def test_cli_exit_gates_on_invariant_failure(tmp_path, capsys):
    from evarness.cli import main

    graph_doc = patterns.load_pattern(FLAGSHIP)
    graph_doc["params"]["invariants"] = ["no-model-calls"]
    (tmp_path / "graph.json").write_text(json.dumps(graph_doc))
    (tmp_path / "invariants.yaml").write_text(
        "invariants:\n  no-model-calls:\n    assert: {never: {type: llm_request}}\n"
    )
    fx = str(patterns.fixture_path(FLAGSHIP, "happy"))
    # the run completes, but the sibling invariants.yaml contract fails -> exit 1
    assert main(["run", str(tmp_path / "graph.json"), "--fixture", fx]) == 1
    out = capsys.readouterr().out
    assert "STATUS: completed" in out and "no-model-calls" in out and "FAILED" in out


# ---------------------------------------------------------------- API library
