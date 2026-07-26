"""Bundle export (E16) — a verified proof bundle unpacked into the standard
interchange set.

The guarantees under test: export refuses a bundle that fails verification
(nothing written — export never launders a tampered bundle), the exported
JSONL is the digest input (recomputing the digest from the file reproduces
the scenario's pinned digest), OTLP carries the digest inside, JUnit/SARIF
verdicts are always exported, --no-events scenarios are named in the manifest
but never silently skipped, every file gets a sha256 receipt, and unknown
formats are loud before anything is written.
"""

import hashlib
import json

import pytest

from evarness.core.graph import GraphModel
from evarness.core.prove import prove
from evarness.core.trace import trace_digest
from evarness.domains.agents import patterns
from evarness.io.exporters import ExportFormatError, ExportRefusedError, export_bundle

FLAGSHIP = "governed_email_assistant"


def make_bundle(include_events: bool = True) -> dict:
    graph = GraphModel.model_validate(patterns.load_pattern(FLAGSHIP))
    scenarios = [
        (n, patterns.fixture_text(FLAGSHIP, n) or "") for n in patterns.fixture_names(FLAGSHIP)
    ]
    return prove(
        graph,
        scenarios,
        pattern_id=FLAGSHIP,
        invariant_defs=patterns.invariant_defs(FLAGSHIP) or None,
        include_events=include_events,
    )


def test_export_produces_the_interchange_set(tmp_path):
    manifest = export_bundle(make_bundle(), tmp_path / "out")
    out = tmp_path / "out"
    kinds = {f["kind"] for f in manifest["files"]}
    assert kinds == {"junit", "sarif", "trace:jsonl", "trace:otlp"}
    assert (out / "manifest.json").is_file()
    assert (out / "verdicts.junit.xml").is_file()
    assert (out / "verdicts.sarif.json").is_file()
    # two scenarios x two trace formats
    assert len([f for f in manifest["files"] if f["kind"].startswith("trace:")]) == 4
    # every receipt matches the bytes on disk
    for f in manifest["files"]:
        assert hashlib.sha256((out / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    assert manifest["verified"]["ok"] is True
    assert manifest["not_proven"]  # the honesty section travels with the export


def test_jsonl_is_the_digest_input(tmp_path):
    manifest = export_bundle(make_bundle(), tmp_path / "out")
    for f in manifest["files"]:
        if f["kind"] != "trace:jsonl":
            continue
        events = [
            json.loads(line)
            for line in (tmp_path / "out" / f["path"]).read_text().splitlines()
            if line.strip()
        ]
        assert trace_digest(events) == f["trace_digest"]  # reproduced from the file alone


def test_otlp_carries_the_digest_inside(tmp_path):
    manifest = export_bundle(make_bundle(), tmp_path / "out")
    otlp_files = [f for f in manifest["files"] if f["kind"] == "trace:otlp"]
    assert otlp_files
    for f in otlp_files:
        doc = json.loads((tmp_path / "out" / f["path"]).read_text())
        assert f["trace_digest"] in json.dumps(doc)


def test_tampered_bundle_is_refused_with_nothing_written(tmp_path):
    bundle = make_bundle()
    bundle["scenarios"][0]["trace_digest"] = "c1:sha256:" + "0" * 64
    out = tmp_path / "out"
    with pytest.raises(ExportRefusedError, match="nothing written"):
        export_bundle(bundle, out)
    assert not out.exists()


def test_no_events_bundle_exports_verdicts_and_honest_manifest(tmp_path):
    manifest = export_bundle(make_bundle(include_events=False), tmp_path / "out")
    assert {f["kind"] for f in manifest["files"]} == {"junit", "sarif"}
    assert all(not s["events_exported"] for s in manifest["scenarios"])
    assert all(s["trace_digest"] for s in manifest["scenarios"])  # named by digest


def test_unknown_format_is_loud_before_writing(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ExportFormatError, match="jsonl"):
        export_bundle(make_bundle(), out, trace_formats=("nope",))
    assert not out.exists()


def test_cli_export_round_trip(tmp_path, capsys):
    from evarness.cli import main

    bundle_path = tmp_path / "proof.json"
    bundle_path.write_text(json.dumps(make_bundle()))
    out = tmp_path / "interchange"
    assert main(["export", str(bundle_path), "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "manifest.json" in printed and "verified bundle" in printed
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["export_version"] == "x1"


def test_cli_export_refuses_tampered(tmp_path, capsys):
    from evarness.cli import main

    bundle = make_bundle()
    bundle["scenarios"][0]["trace_digest"] = "c1:sha256:" + "0" * 64
    bundle_path = tmp_path / "proof.json"
    bundle_path.write_text(json.dumps(bundle))
    assert main(["export", str(bundle_path), "-o", str(tmp_path / "x")]) == 1
    assert "export refused" in capsys.readouterr().err
