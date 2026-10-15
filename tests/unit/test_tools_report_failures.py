"""A tool that fails says so with a ``FailedResult``, not with ``Error:`` text (#301).

Plain ``"Error: ..."`` text is recorded as a success: the call's rows carry no
``tool_error``, a write settles ``completed``, and only the loop's text-prefix check counts it
toward the failure streak. ``FailedResult`` (``iris_harness.sdk.tools``) is the one way a tool
reports a call that ran and did not do what was asked, without raising.

This scan holds the source to it: no function returns a string starting ``Error:`` unless it
is wrapped. It finds the sites the migration converted and any a later change adds.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve()
while not (_ROOT / "src" / "iris_harness").is_dir():
    if _ROOT == _ROOT.parent:
        raise RuntimeError("could not locate repo root (src/iris_harness)")
    _ROOT = _ROOT.parent

_TREES = ("src/iris_harness", "src/iris_personal")


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
        "return FailedResult('Error: ...') (iris_harness.sdk.tools) so the call is recorded "
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
        "iris_doc",
    ],
)
def test_a_built_in_tool_given_no_arguments_returns_a_failed_result(tool: str) -> None:
    from iris_harness.runtime.react_tools import builtin_react_tools
    from iris_harness.sdk.tools import FailedResult

    specs = {
        spec.name: spec
        for spec in builtin_react_tools(semantic_index=None, wiki=None, repo_root=None)
    }

    result = specs[tool].call({})

    assert isinstance(result, FailedResult)
    assert result.startswith("Error:")  # the model still reads the same text
