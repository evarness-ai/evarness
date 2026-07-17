"""Trace exporters (D56) — registry honesty, jsonl/otlp projections, JUnit/SARIF
proof reports, CLI + API surfaces."""
import importlib
import json
import xml.etree.ElementTree as ET

import pytest

from evarness import patterns
from evarness.engine import execute
from evarness.exporters import (EXPORTERS, export_formats, export_trace,
                                  register_exporter)
from evarness.prove import prove, render_junit, render_sarif
from evarness.schema import GraphModel
from evarness.sim import load_fixture
from evarness.trace import canonical_trace, trace_digest

FLAGSHIP = "governed_email_assistant"


def _run(fixture="happy"):
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    fx = load_fixture(patterns.fixture_path(FLAGSHIP, fixture))
    return graph, execute(graph, fx)


def _meta(graph, run):
    return {"run_id": run.id, "name": graph.name, "graph_id": graph.id,
            "status": run.status, "reason": run.reason,
            "seed": graph.params.seed, "provider": graph.params.provider,
            "trace_digest": trace_digest(run.events)}


# ------------------------------------------------------------------- registry

def test_unknown_format_is_loud_and_names_the_alternatives():
    with pytest.raises(ValueError) as exc:
        export_trace("protobuf", [], {})
    assert "jsonl" in str(exc.value) and "otlp" in str(exc.value)


def test_register_exporter_extension_point():
    @register_exporter("test-count", media_type="text/plain", extension=".txt")
    def count(events, meta, cfg):
        return f"{len(events)} events\n"
    try:
        assert "test-count" in export_formats()
        doc, media = export_trace("test-count", [{"seq": 0}, {"seq": 1}], {})
        assert doc == "2 events\n" and media == "text/plain"
    finally:
        EXPORTERS.pop("test-count", None)


# --------------------------------------------------------------------- jsonl

def test_jsonl_is_the_canonical_trace_line_by_line():
    graph, run = _run()
    doc, media = export_trace("jsonl", run.events, _meta(graph, run))
    assert media == "application/x-ndjson"
    parsed = [json.loads(line) for line in doc.strip().split("\n")]
    assert parsed == canonical_trace(run.events)
    assert all("ts" not in e for e in parsed)
    # the export IS the digest input: recomputing over it names the same trace
    assert trace_digest(parsed) == trace_digest(run.events)


# ---------------------------------------------------------------------- otlp

def test_otlp_run_becomes_root_span_nodes_become_children():
    graph, run = _run()
    doc, media = export_trace("otlp", run.events, _meta(graph, run))
    assert media == "application/json"
    spans = json.loads(doc)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, children = spans[0], spans[1:]
    assert root["name"].startswith("evarness.run:")
    assert len(root["traceId"]) == 32 and len(root["spanId"]) == 16
    assert {s["traceId"] for s in spans} == {root["traceId"]}
    assert all(s["parentSpanId"] == root["spanId"] for s in children)
    node_starts = [e for e in run.events if e["type"] == "node_started"]
    assert len(children) == len(node_starts)
    assert root["status"] == {"code": "STATUS_CODE_OK"}
    attrs = {a["key"]: a["value"] for a in root["attributes"]}
    assert attrs["evarness.trace_digest"]["stringValue"].startswith("c1:sha256:")
    assert attrs["evarness.deterministic"]["boolValue"] is True
    assert attrs["gen_ai.provider.name"]["stringValue"] == "sim"
    assert int(attrs["gen_ai.usage.total_tokens"]["intValue"]) > 0
    # spans are honest about time: end >= start, and children nest inside root
    for s in spans:
        assert int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"])
        assert int(s["startTimeUnixNano"]) >= int(root["startTimeUnixNano"])


def test_otlp_blocked_run_marks_error_and_carries_the_violation():
    graph, run = _run("failure")
    assert run.status == "blocked"
    doc, _ = export_trace("otlp", run.events, _meta(graph, run))
    spans = json.loads(doc)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = spans[0]
    assert root["status"]["code"] == "STATUS_CODE_ERROR"
    assert root["status"]["message"]
    # the policy_violation rides on the span of the node that was executing
    violating = [s for s in spans[1:]
                 if any(e["name"] == "policy_violation" for e in s["events"])]
    assert violating and violating[0]["status"]["code"] == "STATUS_CODE_ERROR"


def test_otlp_span_ids_are_content_derived_and_reproducible():
    graph, run = _run()
    a, _ = export_trace("otlp", run.events, _meta(graph, run))
    b, _ = export_trace("otlp", run.events, _meta(graph, run))
    ids = lambda doc: [(s["traceId"], s["spanId"]) for s in
                       json.loads(doc)["resourceSpans"][0]["scopeSpans"][0]["spans"]]
    assert ids(a) == ids(b)
    assert len(set(ids(a))) == len(ids(a))          # and unique within the trace


def test_otlp_service_name_comes_from_exporters_yaml():
    graph, run = _run()
    doc, _ = export_trace("otlp", run.events, _meta(graph, run))
    res = json.loads(doc)["resourceSpans"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "evarness"}} in res


# ------------------------------------------------------------ JUnit and SARIF

def _proof(**kw):
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    scenarios = [(n, patterns.fixture_text(FLAGSHIP, n))
                 for n in patterns.fixture_names(FLAGSHIP)]
    if "invariants" in kw:
        graph.params.invariants = kw.pop("invariants")
    return prove(graph, scenarios, pattern_id=FLAGSHIP, **kw)


def test_junit_holding_proof_has_zero_failures():
    xml = render_junit(_proof())
    root = ET.fromstring(xml)
    assert root.get("failures") == "0"
    names = [c.get("name") for s in root for c in s]
    assert "digest reproduced" in names
    assert any(n.startswith("invariant:") for n in names)


def test_junit_failed_invariant_is_a_failure_with_evidence():
    proof = _proof(invariants=["no-model-calls-ever"],
                   invariant_defs={"no-model-calls-ever":
                                   {"assert": {"never": {"type": "llm_request"}}}})
    root = ET.fromstring(render_junit(proof))
    assert int(root.get("failures")) >= 1
    failures = [f for s in root for c in s for f in c.findall("failure")]
    assert failures and "trace: c1:sha256:" in failures[0].text


def test_junit_nothing_asserted_fails_never_passes_silently():
    root = ET.fromstring(render_junit(_proof(invariants=[])))
    cases = {c.get("name"): c for s in root for c in s}
    assert cases["invariants declared"].find("failure") is not None
    assert int(root.get("failures")) >= 1


def test_sarif_violations_become_results_with_rules():
    proof = _proof(invariants=["no-model-calls-ever"],
                   invariant_defs={"no-model-calls-ever":
                                   {"assert": {"never": {"type": "llm_request"}}}})
    doc = json.loads(render_sarif(proof))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert {"no-model-calls-ever", "digest-reproducibility"} <= rule_ids
    hits = [r for r in run["results"] if r["ruleId"] == "no-model-calls-ever"]
    assert hits and hits[0]["level"] == "error"
    assert hits[0]["properties"]["trace_digest"].startswith("c1:sha256:")
    assert hits[0]["properties"]["evidence_seq"]


def test_sarif_holding_proof_is_clean_and_nothing_asserted_warns():
    clean = json.loads(render_sarif(_proof()))
    assert clean["runs"][0]["results"] == []
    hollow = json.loads(render_sarif(_proof(invariants=[])))
    warns = [r for r in hollow["runs"][0]["results"]
             if r["ruleId"] == "nothing-asserted"]
    assert warns and warns[0]["level"] == "warning"


# ------------------------------------------------------------- CLI + API

def test_cli_run_trace_out_writes_the_export(tmp_path, capsys):
    from evarness.cli import main
    graph_path = tmp_path / "g.json"
    graph_path.write_text(json.dumps(patterns.load_pattern(FLAGSHIP)))
    out = tmp_path / "trace.otlp.json"
    fx = str(patterns.fixture_path(FLAGSHIP, "happy"))
    assert main(["run", str(graph_path), "--fixture", fx,
                 "--trace-out", str(out), "--trace-format", "otlp"]) == 0
    spans = json.loads(out.read_text())["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[0]["name"].startswith("evarness.run:")


def test_cli_prove_junit_and_sarif(tmp_path, capsys, monkeypatch):
    from evarness.cli import main
    monkeypatch.chdir(tmp_path)
    assert main(["prove", FLAGSHIP, "-o", "p.json",
                 "--junit", "p.xml", "--sarif", "p.sarif"]) == 0
    assert ET.fromstring((tmp_path / "p.xml").read_text()).get("failures") == "0"
    sarif = json.loads((tmp_path / "p.sarif").read_text())
    assert sarif["runs"][0]["results"] == []
    assert "p.xml, p.sarif" in capsys.readouterr().out




