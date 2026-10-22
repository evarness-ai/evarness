"""Loader tests — YAML parsing, discovery, and error surfacing."""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.playground.loader import SuiteError, discover_suites, load_suite

_SUITE_YAML = """
name: sample
description: a couple of cases
env:
  EVARNESS_DUES_INTERCEPT: "1"
scenarios:
  - name: dues
    message: "any insurance dues this week?"
    tags: [finance, intercept]
    expect:
      handler: dues_request
      intent: finance
      sources_exclude: [research]
      no_pii_leak: true
  - name: greeting
    message: "hello"
    expect:
      handler: ""
"""


def test_load_suite_parses_scenarios(tmp_path: Path) -> None:
    path = tmp_path / "sample.yaml"
    path.write_text(_SUITE_YAML, encoding="utf-8")
    suite = load_suite(path)
    assert suite.name == "sample"
    assert suite.env == {"EVARNESS_DUES_INTERCEPT": "1"}
    assert len(suite.scenarios) == 2
    dues = suite.scenarios[0]
    assert dues.expect.handler == "dues_request"
    assert dues.expect.sources_exclude == ("research",)
    assert dues.expect.no_pii_leak is True
    assert suite.scenarios[1].expect.handler == ""


def test_load_suite_defaults_name_to_stem(tmp_path: Path) -> None:
    path = tmp_path / "my_suite.yaml"
    path.write_text("scenarios:\n  - name: a\n    message: hi\n", encoding="utf-8")
    assert load_suite(path).name == "my_suite"


def test_load_suite_rejects_bad_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: ok\nscenarios: @bad\n", encoding="utf-8")
    with pytest.raises(SuiteError, match="bad.yaml") as info:
        load_suite(path)
    assert info.value.message == f"{path}: invalid YAML at line 2"


def test_load_suite_rejects_unknown_field(tmp_path: Path) -> None:
    """The message names the file and each refused field in the loader's own words; the
    validator's text (which echoes the input back and links its docs) stays out of it."""
    path = tmp_path / "typo.yaml"
    path.write_text(
        "scenarios:\n  - name: a\n    message: hi\n    expect:\n      bogus_field: 1\n"
        "  - name: b\n",
        encoding="utf-8",
    )
    with pytest.raises(SuiteError, match="typo.yaml") as info:
        load_suite(path)
    assert info.value.message == (
        f"{path}: scenarios[0].expect.bogus_field: Extra inputs are not permitted; "
        "scenarios[1].message: Field required"
    )
    assert "pydantic" not in str(info.value)


def test_discover_suites_finds_yaml(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("scenarios: []\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yaml").write_text("scenarios: []\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("ignore me", encoding="utf-8")
    found = discover_suites(tmp_path)
    assert [p.name for p in found] == ["a.yaml", "b.yaml"]


def test_discover_suites_empty_when_missing(tmp_path: Path) -> None:
    assert discover_suites(tmp_path / "nope") == []
