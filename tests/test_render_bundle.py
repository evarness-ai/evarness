"""Proof-bundle browser (E13) — a bundle as one browsable page.

The guarantees under test: the tri-state badge tracks the E8 verdict, one
viewer per scenario with per-viewer playheads, the canvas is drawn only from
a hash-verified graph (mismatch raises, absence is an honest omission), the
bundle's not_proven section renders verbatim, a signed bundle is labeled
"signature NOT checked by this page", and — the load-bearing one — the whole
bundle rides in the data island, so extracting it and running verify_proof
re-checks the proof offline from the HTML file alone.
"""

import json
import re

import pytest

from evarness.core.graph import GraphModel
from evarness.core.prove import graph_hash, prove, verify_proof
from evarness.domains.agents import patterns
from evarness.domains.agents.nodes.base import presentation
from evarness.io.render import (
    RENDER_VERSION,
    RenderMismatchError,
    render_proof_browser,
)

FLAGSHIP = "governed_email_assistant"
GATED = "approval_gated_send"


def make_bundle(pattern_id: str, approvals=None, include_events: bool = True) -> dict:
    graph = GraphModel.model_validate(patterns.load_pattern(pattern_id))
    scenarios = [
        (n, patterns.fixture_text(pattern_id, n) or "") for n in patterns.fixture_names(pattern_id)
    ]
    return prove(
        graph,
        scenarios,
        pattern_id=pattern_id,
        invariant_defs=patterns.invariant_defs(pattern_id) or None,
        approvals=approvals,
        include_events=include_events,
    )


def graph_of(pattern_id: str) -> GraphModel:
    return GraphModel.model_validate(patterns.load_pattern(pattern_id))


def pres_of(graph: GraphModel) -> dict:
    return {t: presentation(t) for t in {n.type for n in graph.nodes}}


def island_of(doc: str) -> dict:
    m = re.search(r'<script type="application/json" id="evarness-data">(.*?)</script>', doc, re.S)
    assert m, "data island missing"
    return json.loads(m.group(1))


# ------------------------------------------------------------------ the badge


def test_holds_badge_and_one_viewer_per_scenario():
    g = graph_of(FLAGSHIP)
    doc = render_proof_browser(make_bundle(FLAGSHIP), graph=g, presentation=pres_of(g))
    assert "PROOF HOLDS" in doc
    assert doc.count('<section class="viewer" data-viewer>') == 2  # happy + failure
    assert doc.count('<input type="range"') == 2  # each viewer scrubs its own playhead


def test_pending_badge_for_paused_bundle():
    doc = render_proof_browser(make_bundle(GATED))
    assert "PROOF PENDING" in doc
    assert "paused awaiting a human decision" in doc  # not_proven line, verbatim


# ------------------------------------------------- the island IS the bundle


def test_bundle_extracted_from_island_verifies_offline():
    bundle = make_bundle(FLAGSHIP)
    assert (bundle.get("verdict") or {}).get("ok") is True
    doc = render_proof_browser(bundle)
    extracted = island_of(doc)["bundle"]
    result = verify_proof(extracted)
    assert result["ok"] is True  # the HTML file alone carries a verifiable proof


# --------------------------------------------------- canvas identity honesty


def test_mismatched_graph_raises():
    bundle = make_bundle(FLAGSHIP)
    tampered = graph_of(FLAGSHIP)
    tampered.nodes[0].label = "tampered"
    assert graph_hash(tampered) != (bundle["subject"] or {})["graph_sha256"]
    with pytest.raises(RenderMismatchError, match="proven graph"):
        render_proof_browser(bundle, graph=tampered)


def test_missing_graph_is_an_honest_omission():
    doc = render_proof_browser(make_bundle(FLAGSHIP))  # no graph passed
    assert "Canvas omitted" in doc
    assert "<svg" not in doc
    assert island_of(doc)["meta"]["graph_attached"] is False


def test_matching_graph_draws_the_canvas():
    g = graph_of(FLAGSHIP)
    doc = render_proof_browser(make_bundle(FLAGSHIP), graph=g, presentation=pres_of(g))
    assert "<svg" in doc and island_of(doc)["meta"]["graph_attached"] is True


# ------------------------------------------------------------- honesty lines


def test_no_events_bundle_says_not_replayable():
    doc = render_proof_browser(make_bundle(FLAGSHIP, include_events=False))
    assert "not replayable here" in doc
    assert "data-ev " not in doc  # no event rows without embedded events


def test_signed_bundle_is_labeled_unchecked():
    bundle = make_bundle(FLAGSHIP)
    bundle["attestation"] = {"algorithm": "ed25519", "public_key": "x", "signature": "y"}
    doc = render_proof_browser(bundle)
    assert "signature NOT checked by this page" in doc
    assert "verify --require-signature" in doc


def test_page_never_claims_to_verify():
    doc = render_proof_browser(make_bundle(FLAGSHIP))
    assert "It verifies nothing itself" in doc
    assert "What this bundle does not prove" in doc


# ----------------------------------------------------------------- stability


def test_browser_is_deterministic_per_bundle():
    bundle = make_bundle(FLAGSHIP)
    g = graph_of(FLAGSHIP)
    a = render_proof_browser(bundle, graph=g, presentation=pres_of(g))
    b = render_proof_browser(bundle, graph=g, presentation=pres_of(g))
    assert a == b
    assert RENDER_VERSION in a


def test_no_external_origins_in_browser():
    doc = render_proof_browser(make_bundle(FLAGSHIP))
    assert "http://" not in doc and "https://" not in doc
    assert not re.search(r'(?:src|href|action)\s*=\s*["\']?//', doc)


# --------------------------------------------------------------- cli surface


def test_cli_renders_a_bundle_with_pattern_autoresolve(tmp_path, capsys):
    from evarness.cli import main

    bundle_path = tmp_path / "proof.json"
    bundle_path.write_text(json.dumps(make_bundle(FLAGSHIP)))
    out = tmp_path / "proof.html"
    assert main(["render", str(bundle_path), "-o", str(out)]) == 0
    doc = out.read_text()
    # subject.pattern resolved and hash-verified -> canvas drawn
    assert "PROOF HOLDS" in doc and "<svg" in doc
    assert "proof browser" in capsys.readouterr().out


def test_cli_renders_a_pattern_id(tmp_path):
    from evarness.cli import main

    out = tmp_path / "pattern.html"
    assert main(["render", FLAGSHIP, "-o", str(out)]) == 0
    assert "no run attached" in out.read_text().lower()
