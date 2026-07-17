"""This release is simulation-only: sim providers work, real providers refuse
loudly (never a silent fallback that would make the trace lie about what ran)."""

import pytest

from evarness.providers import ProviderError, list_providers, make_provider
from evarness.sim import SimLLMProvider


def test_sim_provider_resolves():
    p = make_provider("sim:helpful-v1")
    assert isinstance(p, SimLLMProvider)
    assert p.deterministic is True


def test_default_spec_is_sim():
    assert isinstance(make_provider(""), SimLLMProvider)


@pytest.mark.parametrize("spec", ["anthropic:claude-sonnet-5", "ollama:llama3.2"])
def test_real_providers_refuse_with_actionable_error(spec):
    with pytest.raises(ProviderError) as exc:
        make_provider(spec)
    msg = str(exc.value)
    assert "not part of this release" in msg
    assert "sim:helpful-v1" in msg  # the remediation is named


def test_unknown_kind_is_a_distinct_error():
    with pytest.raises(ValueError, match="unknown provider kind"):
        make_provider("banana:split")


def test_list_providers_is_sim_only_and_says_so():
    ids = list_providers()
    assert [p["id"] for p in ids] == ["sim:helpful-v1"]
    assert ids[0]["deterministic"] is True
    assert "later capability" in ids[0]["note"]


def test_graph_naming_real_provider_fails_loudly_at_execution():
    from evarness.engine import execute
    from evarness.schema import GraphModel
    from evarness.sim import load_fixture

    graph = GraphModel.model_validate(
        {
            "id": "wants-real",
            "params": {"provider": "anthropic:claude-sonnet-5"},
            "nodes": [
                {"id": "i", "type": "input"},
                {"id": "l", "type": "llm"},
                {"id": "o", "type": "output"},
            ],
            "edges": [{"from": "i", "to": "l"}, {"from": "l", "to": "o"}],
        }
    )
    with pytest.raises(ProviderError, match="not part of this release"):
        execute(graph, load_fixture(None), user_input="hi")


def test_tool_mode_real_refuses_as_governance_block():
    """Real tool execution hasn't graduated: mode:real must be a traced
    governance block with remediation — never an ImportError."""
    from evarness.engine import execute
    from evarness.schema import GraphModel
    from evarness.sim import load_fixture

    graph = GraphModel.model_validate(
        {
            "id": "wants-real-tool",
            "nodes": [
                {"id": "i", "type": "input"},
                {"id": "t", "type": "tool", "config": {"tool": "email.search", "mode": "real"}},
                {"id": "o", "type": "output"},
            ],
            "edges": [{"from": "i", "to": "t"}, {"from": "t", "to": "o"}],
        }
    )
    run = execute(graph, load_fixture(None), user_input="find mail")
    assert run.status == "blocked"
    assert "not part of this release" in (run.reason or "")
    assert "mode: sim" in (run.reason or "")
    types = [e["type"] for e in run.events]
    assert "policy_violation" in types
