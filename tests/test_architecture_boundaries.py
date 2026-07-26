"""The architecture rules as tests.

ARCHITECTURE.md states the boundary: ``core`` imports nothing from
``domains`` or ``io``. A rule that only lives in prose regresses silently —
these tests make the import direction and the kernel's domain-independence
mechanical, the same way the golden digests pin the canonical form.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "evarness" / "core"

FORBIDDEN_PREFIXES = ("evarness.domains", "evarness.io", "evarness.cli")


def _imports(path: Path) -> list[str]:
    """Return every module name that ``path`` imports.

    Handles:
    * ``import X`` → records ``X``
    * ``from X import Y`` → records ``X`` and ``X.Y``
    * relative imports (``from ..domains import agents``) → normalised to
      absolute names before recording
    """
    # Derive the dotted package for this file so relative imports can be
    # resolved.  ``path`` is inside the repo root; strip the suffix and
    # reconstruct the package hierarchy from there.
    repo_root = CORE.parent.parent
    pkg_parts = list(path.relative_to(repo_root).with_suffix("").parts[:-1])

    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            if level > 0:
                # Resolve relative import: strip (level-1) tail segments from
                # the package to find the anchor package.
                anchor = pkg_parts[: len(pkg_parts) - (level - 1)]
                base = ".".join(anchor)
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if not module:
                continue
            found.append(module)
            for alias in node.names:
                found.append(f"{module}.{alias.name}")
    return found


def test_core_never_imports_domains_io_or_cli():
    violations = []
    for path in sorted(CORE.glob("*.py")):
        for module in _imports(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.name} imports {module}")
    assert not violations, "core must stay domain-agnostic:\n" + "\n".join(violations)


def test_core_graph_surface_works_without_any_domain():
    """Parsing, linting, and ordering a graph must not pull in a domain.

    Runs in a subprocess so this test's own environment (where conftest
    imports the agents domain) cannot mask a hidden dependency.
    """
    code = """
import sys
from pydantic import BaseModel
from evarness.core.graph import GraphModel, lint, topological_order

class _Cfg(BaseModel):
    pass

class _Spec:
    Config = _Cfg

graph = GraphModel.model_validate({
    "ir_version": 1,
    "id": "boundary-check",
    "name": "boundary check",
    "nodes": [
        {"id": "a", "type": "thing", "config": {}},
        {"id": "b", "type": "thing", "config": {}},
    ],
    "edges": [{"from": "a", "to": "b"}],
})
issues = lint(graph, {"thing": _Spec})
assert not [i for i in issues if i["level"] == "error"], issues
assert topological_order(graph) == ["a", "b"]
loaded = [m for m in sys.modules if m.startswith("evarness.domains")]
assert not loaded, f"core pulled in domain modules: {loaded}"
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_top_level_surface_is_lazy_but_batteries_included():
    """`import evarness` loads no domain; first attribute access loads it all.

    The package docstring promises both halves; this holds them.
    """
    code = """
import sys
import evarness
assert not [m for m in sys.modules if m.startswith("evarness.domains")], "import alone loaded a domain"
execute = evarness.execute
assert callable(execute)
assert "evarness.domains.agents" in sys.modules, "attribute access must load the batteries"
assert len(evarness.NODE_TYPES) > 0, "agents node types must be registered"
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_unguarded_llm_rule_is_domain_registered():
    """The policy lint arrives with the agents domain, not the kernel."""
    import evarness.domains.agents  # noqa: F401  (registers the rule)
    from evarness.core.registry import GRAPH_LINT_RULES

    names = [fn.__name__ for fn in GRAPH_LINT_RULES]
    assert "unguarded_llm" in names
