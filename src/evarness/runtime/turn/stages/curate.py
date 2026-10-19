"""Stage 7 — the Response Curator (judges, escalation, post-processing) → ChatResult."""

from __future__ import annotations

from collections.abc import Iterator

from evarness.foundation.observability.session_log import log_timeline_event
from evarness.foundation.observability.tracer import set_span_attributes
from evarness.runtime.turn.host import TurnHost
from evarness.runtime.turn.stages import stage_span
from evarness.runtime.turn.state import TurnState
from evarness.runtime.types import StreamEvent


def run(runtime: TurnHost, state: TurnState) -> Iterator[StreamEvent]:
    session_id = state.session_id
    assert state.intent_result is not None and state.plan is not None
    start_payload = {
        "name": "response_curator.start",
        "result_count": len(state.results),
        "agents": [r.agent_type for r in state.results],
    }
    log_timeline_event(
        "response_curator.start",
        phase="response_curator.start",
        payload=start_payload,
        session_id=session_id,
    )
    yield StreamEvent(kind="trace", text="response curator start", payload=start_payload)
    with stage_span(runtime, state, "evarness.stage.response_curator") as span:
        # Escalation (ADR-0068 L3) is allowed on both paths now; it is flag-gated
        # and off by default, so this only closes the old chat/chat_stream drift.
        final = runtime._finalize_chat(
            message=state.message,
            session_id=session_id,
            intent_result=state.intent_result,
            plan=state.plan,
            results=state.results,
            preferred_model=state.request.preferred_model,
            provider_profile=state.request.provider_profile,
            router_model=state.request.router_model,
            strict=state.request.strict,
            span=state.span,
            memory_ctx=state.memory_ctx,
            allow_escalation=True,
        )
        set_span_attributes(
            span,
            {
                "evarness.sources": ",".join(final.sources),
                "evarness.has_errors": final.has_errors,
                "evarness.response_chars": len(final.response),
            },
        )
    end_payload = {
        "name": "response_curator.end",
        "sources": list(final.sources),
        "has_errors": final.has_errors,
        "response_chars": len(final.response),
        "metadata": final.metadata,
    }
    log_timeline_event(
        "response_curator.end",
        phase="response_curator.end",
        payload=end_payload,
        session_id=session_id,
    )
    yield StreamEvent(kind="trace", text="response curator end", payload=end_payload)
    state.result = final
