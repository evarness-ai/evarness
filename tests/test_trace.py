"""Canonical trace normalization (D51) — the published determinism contract."""

import json

from evarness import patterns, store
from evarness.engine import execute
from evarness.schema import GraphModel
from evarness.sim import load_fixture
from evarness.trace import (
    CANONICAL_ENVELOPE_FIELDS,
    CANONICALIZATION_VERSION,
    canonical_event,
    canonical_json,
    trace_digest,
)

FLAGSHIP = "governed_email_assistant"


def load(pattern_id: str, fixture: str = "happy"):
    graph = GraphModel.model_validate(patterns.load_pattern(pattern_id))
    fx = load_fixture(patterns.fixture_path(pattern_id, fixture))
    return graph, fx


def test_canonical_event_drops_exactly_the_wall_clock():
    run = execute(*load(FLAGSHIP))
    raw = run.events[0]
    assert "ts" in raw  # engine stamps wall clock
    ce = canonical_event(raw)
    assert set(ce) == set(CANONICAL_ENVELOPE_FIELDS)
    assert "ts" not in ce
    # everything else is carried whole — payloads are NOT filtered (rule 2)
    assert ce["payload"] == raw["payload"]
    assert ce["seq"] == raw["seq"] and ce["type"] == raw["type"]


def test_two_runs_same_digest_despite_different_timestamps():
    graph, fx = load(FLAGSHIP)
    a, b = execute(graph, fx), execute(graph, fx)
    # raw streams differ (ts is wall clock); canonical form and digest must not
    assert canonical_json(a.events) == canonical_json(b.events)
    assert trace_digest(a.events) == trace_digest(b.events)


def test_digest_is_versioned_and_stable_format():
    run = execute(*load(FLAGSHIP))
    d = trace_digest(run.events)
    assert d.startswith(f"{CANONICALIZATION_VERSION}:sha256:")
    assert len(d.split(":")[2]) == 64


def test_different_fixture_different_digest():
    graph, _ = load(FLAGSHIP)
    happy = execute(graph, load_fixture(patterns.fixture_path(FLAGSHIP, "happy")))
    failure = execute(graph, load_fixture(patterns.fixture_path(FLAGSHIP, "failure")))
    assert trace_digest(happy.events) != trace_digest(failure.events)


def test_canonical_json_is_byte_stable_serialization():
    run = execute(*load(FLAGSHIP))
    s = canonical_json(run.events)
    # ascii-only, compact, key-sorted — re-parsing and re-dumping is a no-op
    assert s == s.encode("ascii").decode("ascii")
    assert json.dumps(json.loads(s), sort_keys=True, separators=(",", ":"), ensure_ascii=True) == s


def test_persisted_events_verify_the_live_digest(tmp_path, monkeypatch):
    # a trace reloaded from the store must reproduce the digest computed at
    # run time — this is what makes stored evidence re-checkable (D51)
    db = str(tmp_path / "t.db")
    store.init_db(db)
    graph, fx = load(FLAGSHIP)
    run = execute(graph, fx)
    live = trace_digest(run.events)
    store.save_run(run, "h-test", fx.scenario, graph.params.seed, db_path=db)
    reloaded = store.list_run_events(run.id, db_path=db)
    assert trace_digest(reloaded) == live
