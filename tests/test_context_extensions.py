"""E10 — namespaced per-run domain state; the kernel constructs nothing
domain-shaped.

The behavioral proof that the seam carries real traffic is the existing
classification/egress/tier suite (test_core.py) — every one of those tests now
flows through RunContext.ext. These tests pin the seam's own contract.
"""

from evarness.core.executor import RunContext
from evarness.core.registry import (
    CONTEXT_EXTENSIONS,
    build_context_extensions,
    register_context_extension,
)
from evarness.domains.agents.state import AgentsRunState


def test_kernel_context_carries_no_agent_fields():
    # the honesty-ledger debt, retired: agent vocabulary lives in the agents
    # domain's state object, not on the kernel dataclass
    fields = set(RunContext.__dataclass_fields__)
    assert {"classification", "egress_mode", "tier", "tier_locality"}.isdisjoint(fields)
    assert "ext" in fields


def test_agents_domain_registers_its_slot():
    assert "agents" in CONTEXT_EXTENSIONS
    ext = build_context_extensions()
    assert isinstance(ext["agents"], AgentsRunState)
    st = ext["agents"]
    # inert defaults — graphs without classifier/tier nodes are unaffected
    assert (st.classification, st.egress_mode, st.tier, st.tier_locality) == (
        "public",
        "off",
        None,
        None,
    )


def test_extensions_are_fresh_per_build_never_shared():
    a, b = build_context_extensions(), build_context_extensions()
    assert a["agents"] is not b["agents"]  # no state leaks between runs
    a["agents"].classification = "secret"
    assert b["agents"].classification == "public"


def test_third_party_domain_gets_its_own_slot():
    class MlState:
        def __init__(self):
            self.eval_passed = None

    register_context_extension("mlpipe_test", MlState)
    try:
        ext = build_context_extensions()
        assert isinstance(ext["mlpipe_test"], MlState)
        assert isinstance(ext["agents"], AgentsRunState)  # domains coexist
    finally:
        CONTEXT_EXTENSIONS._table.pop("mlpipe_test", None)
