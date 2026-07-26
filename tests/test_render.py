"""Render artifacts (E12) — self-contained HTML views of graphs, runs, verdicts.

The guarantees under test: hard self-containment (no external origins, ever),
hostile fixture content renders inert (XSS is the threat model — this file
gets opened by auditors), evidence/judgment separation survives rendering,
the digest is recomputable from the artifact's data island alone, honesty
lines for paused/zero-contract runs, and byte-stable output pinned by a
golden hash (same discipline as test_golden_digests.py).
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from evarness.core.executor import execute
from evarness.core.graph import GraphModel
from evarness.core.trace import canonical_trace, trace_digest
from evarness.domains.agents import patterns
from evarness.domains.agents.nodes.base import presentation
from evarness.domains.agents.sim import load_fixture
from evarness.io.render import (
    RENDER_VERSION,
    RenderFormatError,
    RenderSubject,
    layered_layout,
    render,
)

FLAGSHIP = "governed_email_assistant"


def subject_for(pattern_id: str, fixture: str, approvals=None) -> RenderSubject:
    graph = GraphModel.model_validate(patterns.load_pattern(pattern_id))
    fx = load_fixture(patterns.fixture_path(pattern_id, fixture))
    run = execute(graph, fx, approvals=approvals)
    return RenderSubject(
        graph=graph,
        events=canonical_trace(run.events),
        verdicts=run.invariants,
        presentation={t: presentation(t) for t in {n.type for n in graph.nodes}},
        meta={
            "scenario": fx.scenario,
            "status": run.status,
            "provider": graph.params.provider,
            "seed": graph.params.seed,
            "deterministic": run.events[0]["payload"].get("deterministic"),
        },
    )


def island_of(doc: str) -> dict:
    m = re.search(r'<script type="application/json" id="evarness-data">(.*?)</script>', doc, re.S)
    assert m, "data island missing"
    return json.loads(m.group(1))


# ------------------------------------------------------------ self-containment


def test_no_external_origins_ever():
    doc = render(subject_for(FLAGSHIP, "happy"))
    assert "http://" not in doc and "https://" not in doc
    # also catch protocol-relative URLs (//host/…) that bypass scheme checks
    assert not re.search(r'(?:src|href|action)\s*=\s*["\']?//', doc)
    assert "Content-Security-Policy" in doc and "default-src 'none'" in doc


def test_renderer_source_never_uses_innerhtml():
    src = (Path(__file__).parent.parent / "evarness" / "io" / "render.py").read_text()
    assert "innerHTML" not in src


# --------------------------------------------------------------- xss hardening


def test_hostile_fixture_content_renders_inert():
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    hostile = "</script><script>alert(1)</script><img src=x onerror=alert(2)>"
    fx = load_fixture({"scenario": "hostile", "user_input": f"search {hostile}"})
    run = execute(graph, fx)
    doc = render(
        RenderSubject(graph=graph, events=canonical_trace(run.events), meta={"status": run.status})
    )
    # no attacker '<' survives as a tag-open anywhere in the document
    assert "<script>alert" not in doc
    assert "<img" not in doc
    # the island still parses, and the hostile text survives escaping intact
    data = island_of(doc)
    flat = json.dumps(data["canonical_events"])
    assert "alert(1)" in flat  # content preserved as data, not as markup


# ----------------------------------------------- evidence / judgment separation


def test_digest_recomputable_from_island_and_unchanged_by_verdicts():
    with_verdicts = subject_for(FLAGSHIP, "happy")
    assert with_verdicts.verdicts is not None  # the pattern declares invariants
    without = RenderSubject(
        graph=with_verdicts.graph,
        events=with_verdicts.events,
        presentation=with_verdicts.presentation,
        meta=with_verdicts.meta,
    )
    for doc in (render(with_verdicts), render(without)):
        data = island_of(doc)
        # judgment never contaminates evidence: recompute the digest from the
        # island's events and it must match the digest the artifact claims
        assert trace_digest(data["canonical_events"]) == data["meta"]["trace_digest"]
    assert "verdicts" in island_of(render(with_verdicts))
    assert "verdicts" not in island_of(render(without))


def test_judgment_pane_lists_verdicts_with_seek_links():
    s = subject_for(FLAGSHIP, "failure")
    s.verdicts = {
        "passed": 0,
        "failed": 1,
        "results": [
            {"id": "run-completes", "ok": False, "detail": "no event matched", "evidence_seq": [3]}
        ],
    }
    doc = render(s)
    assert "run-completes" in doc
    assert 'data-seek="3"' in doc


# -------------------------------------------------------------- honesty lines


def test_paused_run_says_invariants_never_evaluated():
    s = subject_for("approval_gated_send", "send")  # pauses at the human gate
    assert s.meta["status"] == "paused" and s.verdicts is None
    doc = render(s)
    assert "NOT evaluated" in doc
    assert "never" in doc and "evaluated" in doc  # footer honesty line


def test_zero_contract_run_says_nothing_asserted():
    graph = GraphModel.model_validate(
        {
            "id": "g-bare",
            "nodes": [
                {"id": "n1", "type": "input", "config": {}},
                {"id": "n2", "type": "output", "config": {}},
            ],
            "edges": [{"from": "n1", "to": "n2"}],
        }
    )
    run = execute(graph, load_fixture(None))
    doc = render(
        RenderSubject(graph=graph, events=canonical_trace(run.events), meta={"status": run.status})
    )
    assert "nothing was asserted" in doc.lower()


def test_graph_only_render_declares_itself():
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    doc = render(RenderSubject(graph=graph, meta={"lint": []}))
    assert "no run attached" in doc.lower()
    assert '<input type="range"' not in doc  # no playhead without events
    data = island_of(doc)
    assert "canonical_events" not in data and "trace_digest" not in data["meta"]


# -------------------------------------------------------------------- layout


DIAMOND = {
    "id": "g-diamond",
    "nodes": [
        {"id": "a", "type": "input", "config": {}},
        {"id": "b1", "type": "prompt_template", "config": {}},
        {"id": "b2", "type": "prompt_template", "config": {}},
        {"id": "c", "type": "output", "config": {}},
    ],
    "edges": [
        {"from": "a", "to": "b1"},
        {"from": "a", "to": "b2"},
        {"from": "b1", "to": "c"},
        {"from": "b2", "to": "c"},
    ],
}


def test_layout_is_deterministic_and_layered():
    graph = GraphModel.model_validate(DIAMOND)
    a, b = layered_layout(graph), layered_layout(graph)
    assert a == b
    for e in graph.edges:  # computed layout: an edge always points a column right
        assert a[e.from_][0] < a[e.to][0]
    assert a["b1"][0] == a["b2"][0] and a["b1"][1] < a["b2"][1]  # same layer, id order


def test_authored_positions_win():
    doc = json.loads(json.dumps(DIAMOND))
    doc["nodes"][0]["position"] = {"x": 999, "y": 777}
    graph = GraphModel.model_validate(doc)
    layout = layered_layout(graph)
    assert layout["a"] == (999, 777)  # authored wins
    assert layout["c"] != (999, 777)  # the rest still computed


# ------------------------------------------------------------------- registry


def test_unknown_renderer_is_loud():
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    with pytest.raises(RenderFormatError, match="html"):
        render(RenderSubject(graph=graph), renderer="nope")


# ----------------------------------------------------------------- cli surface


def test_cli_run_html_and_render(tmp_path):
    from evarness.cli import main

    gpath = tmp_path / "bare.json"
    gpath.write_text(
        json.dumps(
            {
                "id": "g-bare",
                "nodes": [
                    {"id": "n1", "type": "input", "config": {}},
                    {"id": "n2", "type": "output", "config": {}},
                ],
                "edges": [{"from": "n1", "to": "n2"}],
            }
        )
    )
    replay = tmp_path / "run.html"
    assert main(["run", str(gpath), "--html", str(replay)]) == 0
    data = island_of(replay.read_text())
    assert trace_digest(data["canonical_events"]) == data["meta"]["trace_digest"]

    assert main(["render", str(gpath), "-o", str(tmp_path / "static.html")]) == 0
    static = (tmp_path / "static.html").read_text()
    assert "no run attached" in static.lower()


# ------------------------------------------------------------------ stability


# r3 (platform design language: dark canvas, group dots, state progression,
# arrowhead edges) — the pin moves ONLY with a RENDER_VERSION bump, never as
# a routine test update
GOLDEN_ARTIFACT_SHA256 = "4bd8db78bb893c2f07784fb42f30cc1e36e8d04a80349fc035e1e673386afa3b"


def test_artifact_is_byte_stable_and_pinned():
    a = render(subject_for(FLAGSHIP, "happy"))
    b = render(subject_for(FLAGSHIP, "happy"))
    assert a == b
    assert RENDER_VERSION in a
    assert hashlib.sha256(a.encode()).hexdigest() == GOLDEN_ARTIFACT_SHA256
