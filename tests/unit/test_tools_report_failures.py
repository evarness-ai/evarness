"""A tool that fails says so with a ``FailedResult``, not with ``Error:`` text (#301).

Plain ``"Error: ..."`` text is recorded as a success: the call's rows carry no
``tool_error``, a write settles ``completed``, and only the loop's text-prefix check counts it
toward the failure streak. ``FailedResult`` (``evarness.sdk.tools``) is the one way a tool
reports a call that ran and did not do what was asked, without raising.

This scan holds the source to it: no function returns a string starting ``Error:`` unless it
is wrapped. It finds the sites the migration converted and any a later change adds.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve()
while not (_ROOT / "src" / "evarness").is_dir():
    if _ROOT == _ROOT.parent:
        raise RuntimeError("could not locate repo root (src/evarness)")
    _ROOT = _ROOT.parent

_TREES = ("src/evarness", "src/evarness_personal")


def _first_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _first_text(node.left)
    return None


def _bare_error_returns(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            text = _first_text(node.value)
            if text is not None and text.startswith("Error:"):
                lines.append(node.lineno)
    return lines


def test_no_tool_returns_error_text_without_failed_result() -> None:
    offenders = [
        f"{path.relative_to(_ROOT)}:{line}"
        for tree in _TREES
        for path in sorted((_ROOT / tree).rglob("*.py"))
        for line in _bare_error_returns(path)
    ]

    assert offenders == [], (
        "return FailedResult('Error: ...') (evarness.sdk.tools) so the call is recorded "
        "as failed:\n" + "\n".join(offenders)
    )


def test_the_scan_sees_a_bare_error_return(tmp_path: Path) -> None:
    sample = tmp_path / "tool.py"
    sample.write_text(
        'def a():\n    return "Error: nope"\n'
        'def b():\n    return f"Error: {x}"\n'
        'def c():\n    return ("Error: a " + ", ".join(y))\n'
        'def d():\n    return FailedResult("Error: fine")\n'
        'def e():\n    return "all good"\n',
        encoding="utf-8",
    )

    assert _bare_error_returns(sample) == [2, 4, 6]


@pytest.mark.parametrize(
    "tool",
    [
        "stock_quote",
        "evarness_doc",
    ],
)
def test_a_built_in_tool_given_no_arguments_returns_a_failed_result(tool: str) -> None:
    from evarness.runtime.react_tools import builtin_react_tools
    from evarness.sdk.tools import FailedResult

    specs = {
        spec.name: spec
        for spec in builtin_react_tools(semantic_index=None, wiki=None, repo_root=None)
    }

    result = specs[tool].call({})

    assert isinstance(result, FailedResult)
    assert result.startswith("Error:")  # the model still reads the same text


# --- failures in other words (#309) ---------------------------------------------------------


def _unwrapped_failure_returns_in_handlers(path: Path) -> list[int]:
    """Returns inside an ``except`` block whose text reads ``... failed: ...`` or
    ``... unavailable ...`` and are not wrapped in ``FailedResult``.

    A tool that catches an exception and hands its message back as the result has failed:
    recording that as a success hides it from the ledger and the failure streak.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        for node in ast.walk(handler):
            if isinstance(node, ast.Return) and node.value is not None:
                text = _full_text(node.value)
                if text is not None and ("failed" in text or "unavailable" in text):
                    lines.append(node.lineno)
    return lines


def _full_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{}" for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _full_text(node.left)
    return None


def test_a_tool_that_catches_an_exception_and_returns_its_message_reports_a_failure() -> None:
    offenders = [
        f"{path.relative_to(_ROOT)}:{line}"
        for tree in _TREES
        for path in sorted((_ROOT / tree).rglob("*.py"))
        for line in _unwrapped_failure_returns_in_handlers(path)
    ]

    assert offenders == [], (
        "return FailedResult(...) from the except block so the call is recorded as failed:\n"
        + "\n".join(offenders)
    )


def test_the_handler_scan_sees_an_unwrapped_failure(tmp_path: Path) -> None:
    sample = tmp_path / "tool.py"
    sample.write_text(
        "def a():\n    try:\n        x()\n    except Exception as exc:\n"
        '        return f"a failed: {exc}"\n'
        "def b():\n    try:\n        x()\n    except Exception as exc:\n"
        '        return FailedResult(f"b failed: {exc}")\n'
        "def c():\n    try:\n        x()\n    except KeyError:\n"
        '        return "not there"\n',
        encoding="utf-8",
    )

    assert _unwrapped_failure_returns_in_handlers(sample) == [5]


def _react_tools(**kwargs: Any) -> dict[str, Any]:
    from evarness.runtime.react_tools import builtin_react_tools

    base: dict[str, Any] = {"semantic_index": None, "wiki": None, "repo_root": None}
    return {spec.name: spec for spec in builtin_react_tools(**{**base, **kwargs})}


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("memory_forget", {"key": "location"}),  # no memory store configured
        ("memory_correct", {"key": "location", "value": "Paris"}),
        ("memory_restore", {"key": "location"}),
        ("recall_conversation", {"query": "dinner"}),
        ("wiki_search", {"query": "tiers"}),  # no wiki
        ("learning_intelligence", {}),  # no learning store
        ("learning_recommendations", {}),
        ("promote_recommendation", {"index": 1}),
        ("propose_skill_from_sandbox", {"script": "x", "intent": "y", "narrative": "z"}),
        ("invoke_pending_action", {}),  # no id
        ("record_feedback", {}),  # neither a ref nor a sender
    ],
)
def test_a_tool_that_cannot_run_or_was_given_nothing_to_act_on_returns_a_failed_result(
    tool: str, args: dict[str, Any]
) -> None:
    from evarness.sdk.tools import FailedResult

    result = _react_tools()[tool].call(args)

    assert isinstance(result, FailedResult), result


def test_an_empty_result_is_not_a_failure() -> None:
    """Nothing stored that matches is an answer, not a failed call."""
    from evarness.sdk.tools import FailedResult

    class _Store:
        def search_turns(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

    result = _react_tools(memory_store=_Store())["recall_conversation"].call({"query": "dinner"})

    assert not isinstance(result, FailedResult)
