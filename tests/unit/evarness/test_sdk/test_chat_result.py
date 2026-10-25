"""``ChatResult`` is the stable tier's name for what a chat intercept returns (E2)."""

from __future__ import annotations

from evarness.runtime.types import ChatResult as RuntimeChatResult
from evarness.sdk import types as sdk_types
from evarness.sdk.types import ChatResult


def test_the_sdk_exports_the_one_chat_result_the_runtime_runs_on() -> None:
    # A re-export, not a copy: an intercept's result is the very type the pipeline reads.
    assert ChatResult is RuntimeChatResult
    assert "ChatResult" in sdk_types.__all__


def test_a_plugin_can_build_the_documented_intercept_return() -> None:
    result = ChatResult(
        response="3 files",
        intent="file_query",
        agent_type="files",
        sources=("file_organizer",),
        has_errors=False,
        error_summary=None,
        metadata={"intercept": "folder_files"},
    )
    assert result.response == "3 files"
    assert result.metadata == {"intercept": "folder_files"}
