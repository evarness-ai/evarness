"""``hooks/result_text.py``: the text of a tool's result, and a copy with it replaced (#112 M11)."""

from __future__ import annotations

import pytest

from evarness.kernel.governance.hooks.result_text import (
    result_text,
    text_parts,
    with_text_parts,
)

IMAGE = {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}
MCP = {
    "content": [{"type": "text", "text": "a"}, IMAGE, {"type": "text", "text": "b"}],
    "isError": False,
}


def test_an_mcp_result_is_its_text_blocks_in_order() -> None:
    assert text_parts(MCP) == ["a", "b"]
    assert result_text(MCP) == "a\nb"


@pytest.mark.parametrize(
    "result",
    [
        {"content": [IMAGE], "isError": False},
        {"content": [], "isError": True},
        {"content": [{"type": "text"}, {"type": "text", "text": 3}, "loose"]},
        {"content": [{"text": "no type is not a text block"}]},
        None,
        42,
        ["a", "b"],
    ],
)
def test_a_result_with_no_text_has_no_parts_and_is_left_as_it_is(result: object) -> None:
    assert text_parts(result) == []
    assert result_text(result) == ""
    assert with_text_parts(result, []) is result


def test_a_string_and_a_string_under_a_known_key_are_one_part() -> None:
    assert text_parts("plain") == ["plain"]
    assert text_parts({"output": "out", "text": "later"}) == ["out"]
    assert with_text_parts("plain", ["X"]) == "X"
    assert with_text_parts({"output": "out", "n": 1}, ["X"]) == {"output": "X", "n": 1}


def test_replacing_the_text_keeps_the_shape_and_does_not_mutate_the_result() -> None:
    before = {
        "content": [dict(b) for b in MCP["content"]],  # type: ignore[attr-defined]
        "isError": False,
    }
    out = with_text_parts(MCP, ["A", "B"])
    assert out == {
        "content": [{"type": "text", "text": "A"}, IMAGE, {"type": "text", "text": "B"}],
        "isError": False,
    }
    assert MCP == before


def test_the_parts_must_line_up_with_the_result() -> None:
    with pytest.raises(ValueError, match="expected 2 text part"):
        with_text_parts(MCP, ["only one"])
