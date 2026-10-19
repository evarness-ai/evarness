"""The text of a tool's result, for the ``POST_TOOL_USE`` hooks that read it.

A tool's result is a string, a dict that holds its text under a known key (``output``,
``text``, ...), or an MCP ``tools/call`` result: ``{"content": [{"type": "text", "text":
...}, ...], "isError": ...}``, whose text is its text content blocks. Every hook that reads
or rewrites a result's text goes through here, so a result shape is understood once: the
MCP shape was read by none of them (issue #112 M11), and a bridged server's output went
past the injection guard and the output classifier unread.

:func:`text_parts` lists the result's pieces of text, in order; :func:`with_text_parts`
returns a copy of the result with those pieces replaced and everything else -- non-text
content blocks, ``isError``, other keys -- as it was, so a hook that redacts the text
hands on a result of the shape its caller expects.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: Keys searched, in order, for the text inside a dict result.
RESULT_TEXT_KEYS: tuple[str, ...] = ("output", "content", "text", "result", "body")

#: Where one piece of text sits: ``(None, None)`` for a bare string result, ``(key, None)``
#: for a string under ``key``, ``(key, i)`` for the text of content block ``i`` of ``key``.
_Slot = tuple[str | None, int | None]


def _is_text_block(block: object) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def _slots(result: object, keys: Sequence[str]) -> list[_Slot]:
    if isinstance(result, str):
        return [(None, None)]
    if not isinstance(result, dict):
        return []
    for key in keys:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return [(key, None)]
        if isinstance(value, list):
            blocks: list[_Slot] = [(key, i) for i, b in enumerate(value) if _is_text_block(b)]
            if blocks:
                return blocks
    return []


def text_parts(result: object, keys: Sequence[str] = RESULT_TEXT_KEYS) -> list[str]:
    """The result's pieces of text, in order; empty when it holds none.

    A string is one piece; a dict gives the first of ``keys`` that holds a non-blank string,
    or a list of content blocks with at least one text block (one piece per text block;
    image, resource and other blocks are not text and are skipped).
    """
    parts: list[str] = []
    for key, index in _slots(result, keys):
        if key is None:
            assert isinstance(result, str)
            parts.append(result)
            continue
        assert isinstance(result, dict)
        value = result[key]
        parts.append(value if index is None else value[index]["text"])
    return parts


def result_text(result: object, keys: Sequence[str] = RESULT_TEXT_KEYS) -> str:
    """The result's text: its pieces joined by newlines (``""`` when it holds none)."""
    return "\n".join(text_parts(result, keys))


def with_text_parts(
    result: object, parts: Sequence[str], keys: Sequence[str] = RESULT_TEXT_KEYS
) -> Any:
    """A copy of ``result`` with its pieces of text replaced by ``parts``, in order.

    ``parts`` lines up with :func:`text_parts` of the same result and keys. The result
    itself is not mutated; a result with no text is returned as it is.
    """
    slots = _slots(result, keys)
    if len(parts) != len(slots):
        raise ValueError(f"expected {len(slots)} text part(s), got {len(parts)}")
    if not slots:
        return result
    if slots[0][0] is None:
        return parts[0]
    assert isinstance(result, dict)
    out: dict[Any, Any] = dict(result)
    for (key, index), text in zip(slots, parts, strict=True):
        assert key is not None
        if index is None:
            out[key] = text
            continue
        if out[key] is result[key]:
            out[key] = list(result[key])
        out[key][index] = {**result[key][index], "text": text}
    return out


__all__ = ["RESULT_TEXT_KEYS", "result_text", "text_parts", "with_text_parts"]
