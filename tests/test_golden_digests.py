"""Golden digests — the published reference traces, pinned byte-for-byte.

These are the digests the README and docs cite as reproducible across
installs and platforms. The determinism test (same seed → same events)
only proves a run agrees with *itself*; it cannot detect a change that is
stable within one process but shifts the canonical bytes. Verified live:
reversing the topological tie-break (``ready.sort(reverse=True)`` in
graph.py) leaves the rest of the suite green while two of these digests
change. Only a pinned expected value catches that class of regression.

If one of these assertions fails, the c1 digest contract has changed.
That is sometimes a legitimate decision — but it is a *contract* decision
(new digest version, DECISIONS.md row, docs updated), never a routine
test update. Do not simply paste in the new value.
"""

import pytest

from evarness.core.executor import execute
from evarness.core.graph import GraphModel
from evarness.core.trace import trace_digest
from evarness.domains.agents import patterns
from evarness.domains.agents.sim import load_fixture

GOLDEN = [
    (
        "governed_email_assistant",
        "happy",
        None,
        "completed",
        "c1:sha256:e0513e746ea1d229cc33ed576624882efb1eef7124830022dd2e6cdaeca98086",
    ),
    (
        "governed_email_assistant",
        "failure",
        None,
        "blocked",
        "c1:sha256:66d4021cf235f88c7b49cf3fa6c8c7b5d0c01ca93844c2e8c7cebcf0f58df658",
    ),
    (
        "approval_gated_send",
        "send",
        None,
        "paused",
        "c1:sha256:8c4b5f50a76a9ca232972a5c4cbadb6ca52b5cbf468d4bdc1d53a23cb1c422a0",
    ),
    (
        "approval_gated_send",
        "send",
        {"n3": "approve"},
        "completed",
        "c1:sha256:60439b6a808ec7bdb8c6beddff68add71e578efbc19695c6cea4afb42e3e7516",
    ),
    (
        "approval_gated_send",
        "send",
        {"n3": "reject"},
        "blocked",
        "c1:sha256:266a05e529570dfd3113030d9538e63ff2b8e3342db7560ca85186415dd190e2",
    ),
]


@pytest.mark.parametrize(
    "pattern_id, fixture, approvals, status, digest",
    GOLDEN,
    ids=[f"{p}-{f}-{(a or {}).get('n3', 'none')}" for p, f, a, _, _ in GOLDEN],
)
def test_golden_digest(pattern_id, fixture, approvals, status, digest):
    graph = GraphModel.model_validate(patterns.load_pattern(pattern_id))
    fx = load_fixture(patterns.fixture_path(pattern_id, fixture))
    run = execute(graph, fx, approvals=approvals) if approvals else execute(graph, fx)
    assert run.status == status
    assert trace_digest(run.events) == digest
