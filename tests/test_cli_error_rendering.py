"""E9 — the CLI renders the EvarnessError family as messages, not tracebacks.

Regressions for the pre-release finding where a real-provider refusal and an
unknown-export-format error escaped `main()` as raw stack traces while the
mode:real tool refusal rendered cleanly.
"""

import json

import pytest

from evarness.cli import main
from evarness.core.errors import EvarnessError
from evarness.domains.agents import patterns
from evarness.domains.agents.providers import ProviderError
from evarness.io.exporters import ExportFormatError, export_trace

FLAGSHIP = "governed_email_assistant"


def _graph_file(tmp_path, **param_overrides):
    doc = patterns.load_pattern(FLAGSHIP)
    doc["params"].update(param_overrides)
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(doc))
    return str(path)


def _fixture():
    return str(patterns.fixture_path(FLAGSHIP, "happy"))


def test_error_hierarchy_membership():
    assert issubclass(ProviderError, EvarnessError)
    assert issubclass(ProviderError, RuntimeError)  # existing callers unbroken
    assert issubclass(ExportFormatError, EvarnessError)
    assert issubclass(ExportFormatError, ValueError)  # existing callers unbroken


def test_unknown_export_format_is_still_a_valueerror():
    with pytest.raises(ValueError, match="unknown trace format"):
        export_trace("xyz", [])


def test_real_provider_refusal_is_a_message_not_a_traceback(tmp_path, capsys):
    graph = _graph_file(tmp_path, provider="anthropic:claude-sonnet-5")
    assert main(["run", graph, "--fixture", _fixture()]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "real model provider" in err
    assert "sim:helpful-v1" in err  # the remediation survives
    assert "Traceback" not in err


def test_unknown_trace_format_is_a_message_not_a_traceback(tmp_path, capsys):
    graph = _graph_file(tmp_path)
    out_path = str(tmp_path / "trace.xyz")
    rc = main(
        ["run", graph, "--fixture", _fixture(), "--trace-out", out_path, "--trace-format", "xyz"]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err and "unknown trace format 'xyz'" in err
    assert "jsonl" in err and "otlp" in err  # alternatives named
    assert "Traceback" not in err


def test_invalid_graph_via_run_is_a_message_not_a_traceback(tmp_path, capsys):
    # pre-existing behavior, pinned here for completeness: cmd_run renders
    # GraphValidationError itself ("Graph invalid: …", exit 2) before the
    # E9 boundary is ever reached
    doc = patterns.load_pattern(FLAGSHIP)
    doc["nodes"][1]["type"] = "quantum_oracle"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc))
    assert main(["run", str(path), "--fixture", _fixture()]) == 2
    captured = capsys.readouterr()
    assert "Graph invalid:" in captured.out and "quantum_oracle" in captured.out
    assert "Traceback" not in captured.out + captured.err


def test_non_family_exceptions_still_raise(tmp_path):
    # anything outside EvarnessError is a genuine bug and must stay loud
    with pytest.raises(json.JSONDecodeError):
        bad = tmp_path / "not-json.json"
        bad.write_text("{nope")
        main(["run", str(bad), "--fixture", _fixture()])
