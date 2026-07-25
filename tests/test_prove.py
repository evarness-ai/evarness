"""Proof bundles (D53) — hashing, reproduction, verdicts, honesty, CLI gate."""

import json

from evarness.domains.agents import patterns
from evarness.core.prove import graph_hash, prove, render_proof_html
from evarness.core.graph import GraphModel

FLAGSHIP = "governed_email_assistant"
GATED = "approval_gated_send"


def _subject(pattern_id):
    graph = GraphModel.model_validate(patterns.load_pattern(pattern_id))
    scenarios = [
        (n, patterns.fixture_text(pattern_id, n)) for n in patterns.fixture_names(pattern_id)
    ]
    return graph, scenarios


def test_flagship_proof_holds():
    graph, scenarios = _subject(FLAGSHIP)
    proof = prove(graph, scenarios, pattern_id=FLAGSHIP)
    v = proof["verdict"]
    assert v["ok"] and v["invariants_pass"] and v["reproduced"]
    assert v["scenarios"] == len(scenarios) >= 2  # happy + failure
    by_name = {s["fixture"]: s for s in proof["scenarios"]}
    assert by_name["happy"]["status"] == "completed"
    assert by_name["failure"]["status"] == "blocked"  # recorded, not judged
    for s in proof["scenarios"]:
        assert s["trace_digest"].startswith("c1:sha256:")
        assert s["reproduced"] is True  # ran twice, digests equal
        assert s["invariants"]["failed"] == 0
        assert all("ts" not in e for e in s["events"])  # canonical stream


def test_graph_hash_is_stable_and_content_sensitive():
    graph, _ = _subject(FLAGSHIP)
    a, b = graph_hash(graph), graph_hash(GraphModel.model_validate(patterns.load_pattern(FLAGSHIP)))
    assert a == b and a.startswith("sha256:")
    graph.params.seed = 99
    assert graph_hash(graph) != a


def test_failing_invariant_fails_the_proof():
    graph, scenarios = _subject(FLAGSHIP)
    graph.params.invariants = ["no-model-calls-ever"]
    defs = {"no-model-calls-ever": {"assert": {"never": {"type": "llm_request"}}}}
    proof = prove(graph, scenarios, invariant_defs=defs)
    assert not proof["verdict"]["ok"]
    happy = next(s for s in proof["scenarios"] if s["fixture"] == "happy")
    assert happy["invariants"]["failed"] == 1


def test_no_invariants_means_nothing_proven():
    graph, scenarios = _subject(FLAGSHIP)
    graph.params.invariants = []
    proof = prove(graph, scenarios)
    assert not proof["verdict"]["ok"]
    assert "nothing was asserted" in proof["verdict"]["note"]


def test_paused_scenario_is_honestly_noted_and_approve_unblocks():
    graph, scenarios = _subject(GATED)
    defs = patterns.invariant_defs(GATED)
    paused = prove(graph, scenarios, invariant_defs=defs)
    sc = paused["scenarios"][0]
    assert sc["status"] == "paused" and sc["invariants"] is None
    assert any("paused awaiting a human decision" in n for n in paused["not_proven"])
    approved = prove(graph, scenarios, invariant_defs=defs, approvals={"n3": "approve"})
    sc = approved["scenarios"][0]
    assert sc["status"] == "completed" and approved["verdict"]["ok"] is True


# ------------------------------------------------- pending verdicts (E8)
# A paused-only bundle evaluated zero invariants and attempted zero
# reproductions — it must never claim a proof. Regression for the
# pre-release finding where optimistic verdict flags made it ok=true.


def test_paused_only_bundle_is_pending_not_ok():
    graph, scenarios = _subject(GATED)
    proof = prove(graph, scenarios, invariant_defs=patterns.invariant_defs(GATED))
    v = proof["verdict"]
    assert v["ok"] is None  # pending: not proven, not failed
    assert v["invariants_pass"] is None  # nothing evaluated — no vacuous True
    assert v["reproduced"] is None  # nothing attempted — no vacuous True
    assert "pending" in v["note"] and "--approve" in v["note"]


def test_verdict_precedence_failure_beats_pending():
    from evarness.core.prove import _verdict

    # a real failure in one scenario sinks the proof even if another paused
    v = _verdict(2, declared=True, invariants_pass=False, reproduced=None, paused=1)
    assert v["ok"] is False
    v = _verdict(2, declared=True, invariants_pass=None, reproduced=False, paused=1)
    assert v["ok"] is False
    # nothing declared beats everything — NOTHING ASSERTED even when paused
    v = _verdict(1, declared=False, invariants_pass=None, reproduced=None, paused=1)
    assert v["ok"] is False and "nothing was asserted" in v["note"]
    # nothing failed + a pause = pending; no pause = holds
    v = _verdict(2, declared=True, invariants_pass=True, reproduced=True, paused=1)
    assert v["ok"] is None
    v = _verdict(1, declared=True, invariants_pass=True, reproduced=True, paused=0)
    assert v["ok"] is True and v["note"] is None


def test_pending_bundle_verifies_consistent_but_forged_ok_is_caught():
    from evarness.core.prove import verify_proof

    graph, scenarios = _subject(GATED)
    proof = prove(graph, scenarios, invariant_defs=patterns.invariant_defs(GATED))
    honest = verify_proof(proof)
    assert honest["ok"]  # pending is internally consistent — verify passes
    # a pre-E8-style bundle (or a forger) claiming ok=true over a paused row
    # must be flagged by the reviewer side
    forged = json.loads(json.dumps(proof))
    forged["verdict"].update(ok=True, invariants_pass=True, reproduced=True)
    result = verify_proof(forged)
    assert not result["ok"]
    bad = next(c for c in result["checks"] if c["check"] == "verdict consistent with scenario rows")
    assert bad["ok"] is False


def test_pending_gates_junit_sarif_and_html():
    from evarness.core.prove import render_junit, render_sarif

    graph, scenarios = _subject(GATED)
    proof = prove(graph, scenarios, invariant_defs=patterns.invariant_defs(GATED))
    junit = render_junit(proof)
    assert 'name="proof complete"' in junit and "failure message=" in junit
    sarif = json.loads(render_sarif(proof))
    assert any(r["ruleId"] == "proof-pending" for r in sarif["runs"][0]["results"])
    assert "PROOF PENDING" in render_proof_html(proof)


def test_cli_pending_prove_exits_nonzero(capsys, tmp_path, monkeypatch):
    from evarness.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["prove", GATED, "-o", "pending.json"]) == 1
    out = capsys.readouterr().out
    assert "PROOF: PENDING" in out and "--approve" in out


def test_bundle_carries_its_own_limits_and_metadata():
    graph, scenarios = _subject(FLAGSHIP)
    proof = prove(graph, scenarios, pattern_id=FLAGSHIP, include_events=False)
    assert proof["proof_version"] == "p2"
    assert proof["engine"]["canonicalization"] == "c1"
    assert proof["subject"]["pattern"] == FLAGSHIP
    assert any("universal safety" in n for n in proof["not_proven"])
    assert "events" not in proof["scenarios"][0]  # --no-events honored
    assert proof["scenarios"][0]["events_count"] > 0


def test_html_report_renders_the_evidence():
    graph, scenarios = _subject(FLAGSHIP)
    proof = prove(graph, scenarios, pattern_id=FLAGSHIP)
    html = render_proof_html(proof)
    assert "PROOF HOLDS" in html
    assert proof["scenarios"][0]["trace_digest"] in html
    assert "What this does not prove" in html
    assert "no-model-call-after-block" in html


def test_cli_prove_writes_bundle_and_gates_exit(tmp_path, capsys, monkeypatch):
    from evarness.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["prove", FLAGSHIP, "-o", "p.json", "--html", "p.html"]) == 0
    out = capsys.readouterr().out
    assert "PROOF: HOLDS" in out
    proof = json.loads((tmp_path / "p.json").read_text())
    assert proof["verdict"]["ok"]
    assert "PROOF HOLDS" in (tmp_path / "p.html").read_text()
    # a graph file with a failing sibling contract exits 1
    doc = patterns.load_pattern(FLAGSHIP)
    doc["params"]["invariants"] = ["impossible"]
    (tmp_path / "g.json").write_text(json.dumps(doc))
    (tmp_path / "invariants.yaml").write_text(
        "invariants:\n  impossible:\n    assert: {eventually: {type: no_such_event}}\n"
    )
    fx = str(patterns.fixture_path(FLAGSHIP, "happy"))
    assert main(["prove", str(tmp_path / "g.json"), "--fixture", fx, "-o", "p2.json"]) == 1
    assert "PROOF: FAILED" in capsys.readouterr().out
