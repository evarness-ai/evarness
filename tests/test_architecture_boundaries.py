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


def test_execute_runs_a_from_scratch_domain_without_agents():
    """A minimal domain — nodes, provider factory, environment — built from
    nothing, executed twice, digest-stable, with the agents domain never
    imported. This is E7's claim at its smallest: the kernel executes any
    domain that fills the seams."""
    code = """
import sys
from pydantic import BaseModel
from evarness.core.executor import execute
from evarness.core.graph import GraphModel
from evarness.core.registry import NODE_TYPES, set_provider_factory
from evarness.core.trace import canonical_trace, trace_digest


class _Provider:
    name = "toy:fixed"
    deterministic = True

    def complete(self, prompt, temperature=0.0, max_tokens=256):
        return {"text": "fixed"}


set_provider_factory(lambda spec, env: _Provider())


class _Env:
    scenario = "toy-happy"
    user_input = "ping"


class SourceNode:
    type_name = "toy_source"
    inputs = {}
    outputs = {"out": "text"}

    class Config(BaseModel):
        pass

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        ctx.emit("toy_sourced", node_id, value=ctx.user_input)
        return ctx.user_input


class SinkNode:
    type_name = "toy_sink"
    inputs = {"in": "text"}
    outputs = {}

    class Config(BaseModel):
        pass

    @classmethod
    def run(cls, node_id, inputs, cfg, ctx):
        ctx.output = str(inputs.get("in", "")).upper()
        return ctx.output


NODE_TYPES.register("toy_source", SourceNode)
NODE_TYPES.register("toy_sink", SinkNode)

graph = GraphModel.model_validate({
    "ir_version": 1,
    "id": "toy-domain",
    "name": "toy domain",
    "nodes": [
        {"id": "a", "type": "toy_source", "config": {}},
        {"id": "b", "type": "toy_sink", "config": {}},
    ],
    "edges": [{"from": "a", "to": "b"}],
})

first = execute(graph, _Env())
second = execute(graph, _Env())
assert first.status == "completed", first.reason
assert first.output == "PING"
d1 = trace_digest(canonical_trace(first.events))
d2 = trace_digest(canonical_trace(second.events))
assert d1 == d2, f"digest not stable: {d1} != {d2}"
names = [e["type"] for e in first.events]
assert "toy_sourced" in names, names
loaded = [m for m in sys.modules if m.startswith("evarness.domains")]
assert not loaded, f"execute pulled in domain modules: {loaded}"
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_cli_loads_entry_point_plugins(monkeypatch):
    """The CLI is the batteries-included surface: installed plugins must be
    registered before any command parses a graph. The lazy top-level import
    no longer does this as a side effect, so `main()` must do it itself —
    this pins that."""
    import evarness.cli as cli

    calls = []
    monkeypatch.setattr(cli, "load_entry_point_plugins", lambda: calls.append(True))
    cli.main(["patterns"])
    assert calls, "main() must call load_entry_point_plugins() before dispatching"


def test_unguarded_llm_rule_is_domain_registered():
    """The policy lint arrives with the agents domain, not the kernel."""
    import evarness.domains.agents  # noqa: F401  (registers the rule)
    from evarness.core.registry import GRAPH_LINT_RULES

    names = [fn.__name__ for fn in GRAPH_LINT_RULES]
    assert "unguarded_llm" in names
