"""The loop's tool-failure streak reads the call's outcome, not its text (#295).

An MCP server that answers ``isError: true`` hands back a ``FailedResult`` whose text
starts ``error:``; a tool that raises comes back as ``Tool error: ...``. Both are calls the
runner settles with ``ok`` false, and both must count toward ``tool_failure_streak`` on
every loop that fires ``PostStep`` -- not only results that happen to start ``Error:``.
"""

from __future__ import annotations

from typing import Any

import pytest

from evarness.agent.agent_executor import TraceChunk
from evarness.agent.agentic_core import AgenticCore, AgenticCoreConfig, ToolSpec
from evarness.agent.tool_runner import FailedResult
from evarness.kernel.governance import GovernanceKernel, HookContext, HookPoint
from evarness.kernel.governance.evaluator import EvaluatorHook, EvaluatorRegistry
from evarness.kernel.governance.evaluator.signals.tool_failure_streak import (
    ToolFailureStreakSignal,
)
from evarness.kernel.governance.hooks.types import HookDecision


def _reports_is_error(_args: dict[str, Any]) -> str:
    """What ``mcp_host.observation`` returns for a result with ``isError: true``."""
    return FailedResult("error: the upstream service rejected the request")


def _raises(_args: dict[str, Any]) -> str:
    raise RuntimeError("the tool crashed")


def _usage_error(_args: dict[str, Any]) -> str:
    """A tool that reports a usage error as its ordinary result text."""
    return "Error: lookup requires a 'q' argument."


class _ScriptedLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def __call__(self, _prompt: str) -> str:
        if not self._responses:
            return "Thought: done\nFinal Answer: finished"
        return self._responses.pop(0)


def _calls(n: int) -> list[str]:
    # Different arguments each time, so neither the repeat guard nor the stall guard
    # stops the run before the streak can.
    return [
        f'Thought: try again\nAction: lookup\nAction Input: {{"q": "attempt {i}"}}'
        for i in range(n)
    ]


def _streak_kernel(threshold: int = 3) -> GovernanceKernel:
    registry = EvaluatorRegistry()
    registry.register(ToolFailureStreakSignal(threshold=threshold))
    registry.init_lock()
    kernel = GovernanceKernel(audit_log=None)
    kernel.register(EvaluatorHook(registry=registry))
    kernel.init_lock()
    return kernel


def _core(tool: Any, responses: list[str], kernel: GovernanceKernel) -> AgenticCore:
    return AgenticCore(
        config=AgenticCoreConfig(max_iterations=6),
        llm_call=_ScriptedLLM(responses),
        tools=[ToolSpec(name="lookup", description="look something up", call=tool)],
        kernel=kernel,
        agent_type="chat",
    )


@pytest.mark.parametrize("tool", [_reports_is_error, _raises, _usage_error])
def test_consecutive_failed_calls_trip_the_streak(tool: Any) -> None:
    trace = _core(tool, _calls(5), _streak_kernel()).run("look it up")

    assert trace.halted_by == "evaluator"
    assert trace.halt_reason is not None and "tool_failure_streak" in trace.halt_reason
    assert len([s for s in trace.steps if s.action]) == 3


def test_a_call_that_answers_resets_the_streak() -> None:
    answers = iter([True, True, False, True, True, False])

    def flaky(_args: dict[str, Any]) -> str:
        if next(answers):
            return FailedResult("error: try later")
        return "found it"

    trace = _core(flaky, _calls(6), _streak_kernel()).run("look it up")

    assert trace.halted_by is None


class _RecordPostStep:
    """A PostStep hook that keeps the ``tool_error`` each step carried."""

    name = "record_post_step"
    hook_point = HookPoint.POST_STEP
    priority = 10

    def __init__(self) -> None:
        self.tool_errors: list[str | None] = []

    async def __call__(self, ctx: HookContext) -> HookDecision:
        self.tool_errors.append(ctx.payload.get("tool_error"))
        return HookDecision(outcome="allow", reason="recorded")


@pytest.mark.parametrize("stream", [False, True])
def test_both_loops_hand_post_step_the_failure(stream: bool) -> None:
    """``chat`` drains ``run_stream``; the wave executor and resumes use ``run``. The
    evaluator sees the same failed call from either."""
    recorder = _RecordPostStep()
    kernel = GovernanceKernel(audit_log=None)
    kernel.register(recorder)
    kernel.init_lock()
    core = _core(_reports_is_error, _calls(2), kernel)

    if stream:
        list(core.run_stream("look it up"))
    else:
        core.run("look it up")

    tool_steps = recorder.tool_errors[:2]
    assert len(tool_steps) == 2
    assert all(e and e.startswith("error:") for e in tool_steps)


def test_a_call_that_answers_carries_no_tool_error() -> None:
    recorder = _RecordPostStep()
    kernel = GovernanceKernel(audit_log=None)
    kernel.register(recorder)
    kernel.init_lock()

    _core(lambda _a: "found it", _calls(1), kernel).run("look it up")

    assert recorder.tool_errors[:1] == [None]


# --- the streaming loop enforces it (#300) --------------------------------------------------


def _stream(core: AgenticCore, query: str = "look it up") -> tuple[str, list[Any], dict[str, Any]]:
    """Drain ``run_stream``: the final text, the tool-step chunks, and the run's summary."""
    chunks = list(core.run_stream(query))
    text = [c for c in chunks if isinstance(c, str)]
    steps = [c for c in chunks if isinstance(c, TraceChunk) and '"action": "lookup"' in c.text]
    summary = next(c for c in reversed(chunks) if isinstance(c, dict))
    return (text[-1] if text else ""), steps, summary


@pytest.mark.parametrize("tool", [_reports_is_error, _raises, _usage_error])
def test_the_streaming_loop_stops_after_three_failed_calls_with_different_arguments(
    tool: Any,
) -> None:
    final, steps, summary = _stream(_core(tool, _calls(8), _streak_kernel()))

    assert len(steps) == 3  # not the six iterations the config allows
    assert summary["reason"] == "evaluator_tool_failure_streak"
    assert "stopped partway" in final.lower()
    assert "tool_failure_streak" in final


def test_the_streaming_halt_says_what_ran_and_leaves_the_next_step_to_the_user() -> None:
    final, _, _ = _stream(_core(_reports_is_error, _calls(8), _streak_kernel()))

    assert "What I did before stopping:" in final
    assert "lookup" in final
    assert "how you'd like to proceed" in final


def test_the_streaming_loop_does_not_stop_a_run_that_recovers() -> None:
    answers = iter([True, True, False, True, True, False])

    def flaky(_args: dict[str, Any]) -> str:
        if next(answers):
            return FailedResult("error: try later")
        return "found it"

    final, steps, summary = _stream(_core(flaky, _calls(6), _streak_kernel()))

    assert "stopped partway" not in final.lower()
    assert len(steps) == 6


def test_an_identical_repeat_still_stops_at_the_stall_guard_not_the_streak() -> None:
    same = ['Thought: again\nAction: lookup\nAction Input: {"q": "x"}'] * 6

    final, steps, summary = _stream(_core(_reports_is_error, same, _streak_kernel()))

    assert not summary["reason"].startswith("evaluator_")  # one mechanism halted it, not two
    assert "tool_failure_streak" not in final
    assert sum('"observation": null' not in s.text for s in steps) <= 2  # calls that ran
