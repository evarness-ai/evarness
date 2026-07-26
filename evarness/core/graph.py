"""Graph IR — the single source of truth every feature operates on."""

from __future__ import annotations

from collections.abc import Mapping

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

IR_VERSION = 1


class Position(BaseModel):
    x: float = 0
    y: float = 0


class NodeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    position: Position | None = None


class EdgeModel(BaseModel):
    """Edges connect ports. Defaults model the common single-in/single-out case."""

    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    from_port: str = "out"
    to_port: str = "in"


class GroupModel(BaseModel):
    id: str
    label: str = ""
    nodes: list[str] = Field(default_factory=list)
    collapsed: bool = False


class GraphParams(BaseModel):
    context_budget_tokens: int = 8000
    max_loops: int = 5
    seed: int = 42
    provider: str = "sim:helpful-v1"
    # invariant contract ids this graph must uphold; resolved against
    # pattern-local > user-overlay > packaged invariants.yaml at run time
    invariants: list[str] = Field(default_factory=list)


class GraphModel(BaseModel):
    ir_version: int = IR_VERSION
    id: str
    name: str = ""
    description: str = ""
    nodes: list[NodeModel] = Field(default_factory=list)
    edges: list[EdgeModel] = Field(default_factory=list)
    groups: list[GroupModel] = Field(default_factory=list)
    params: GraphParams = Field(default_factory=GraphParams)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def node(self, node_id: str) -> NodeModel | None:
        return next((n for n in self.nodes if n.id == node_id), None)


def migrate(doc: dict) -> dict:
    """Upgrade an IR document to the current version (stepwise v1→v2→...)."""
    version = doc.get("ir_version", 1)
    if version > IR_VERSION:
        raise ValueError(f"Document ir_version {version} is newer than supported {IR_VERSION}")
    # v1 is current: nothing to do yet. Migration steps land here as the IR evolves.
    return doc


# ---------------------------------------------------------------- lint


def lint(graph: GraphModel, registry: Mapping[str, Any]) -> list[dict]:
    """Validate a graph against the node registry. Returns [{level, code, message}]."""
    issues: list[dict] = []

    def err(code, msg):
        issues.append({"level": "error", "code": code, "message": msg})

    def warn(code, msg):
        issues.append({"level": "warning", "code": code, "message": msg})

    ids = [n.id for n in graph.nodes]
    if len(ids) != len(set(ids)):
        err("dup_node_id", "Duplicate node ids in graph")

    for n in graph.nodes:
        spec = registry.get(n.type)
        if spec is None:
            err("unknown_type", f"Node {n.id}: unknown node type '{n.type}'")
            continue
        try:
            spec.Config.model_validate(n.config)
        except Exception as exc:  # pydantic ValidationError
            err("bad_config", f"Node {n.id} ({n.type}): invalid config: {exc}")

    id_set = set(ids)
    for e in graph.edges:
        if e.from_ not in id_set:
            err("bad_edge", f"Edge references missing node '{e.from_}'")
        if e.to not in id_set:
            err("bad_edge", f"Edge references missing node '{e.to}'")

    # cycle detection (Kahn) — iteration lives in the Loop Controller, not free cycles
    indeg = {i: 0 for i in id_set}
    for e in graph.edges:
        if e.to in indeg and e.from_ in id_set:
            indeg[e.to] += 1
    ready = sorted([i for i, d in indeg.items() if d == 0])
    seen = 0
    order = list(ready)
    indeg2 = dict(indeg)
    while order:
        cur = order.pop(0)
        seen += 1
        for e in graph.edges:
            if e.from_ == cur and e.to in indeg2:
                indeg2[e.to] -= 1
                if indeg2[e.to] == 0:
                    order.append(e.to)
        order.sort()
    if seen < len(id_set):
        err("cycle", "Graph contains a cycle — use a loop_controller node for iteration")

    types = {n.id: n.type for n in graph.nodes}
    if "input" not in types.values():
        warn("no_input", "Graph has no input node")
    if "output" not in types.values():
        warn("no_output", "Graph has no output node")

    connected = {e.from_ for e in graph.edges} | {e.to for e in graph.edges}
    for n in graph.nodes:
        if n.id not in connected and len(graph.nodes) > 1:
            warn("unconnected", f"Node {n.id} ({n.type}) has no connections")

    # domain-contributed rules (core.registry.register_lint_rule): the kernel
    # checks structure; policy lints belong to the domain whose vocabulary
    # they speak
    from evarness.core.registry import GRAPH_LINT_RULES

    for rule in GRAPH_LINT_RULES:
        issues.extend(rule(graph, registry))

    return issues


def topological_order(graph: GraphModel) -> list[str]:
    """Deterministic topological order (ties broken by node id) — the determinism contract"""
    id_set = {n.id for n in graph.nodes}
    indeg = {i: 0 for i in id_set}
    for e in graph.edges:
        if e.to in indeg and e.from_ in id_set:
            indeg[e.to] += 1
    ready = sorted([i for i, d in indeg.items() if d == 0])
    out: list[str] = []
    while ready:
        cur = ready.pop(0)
        out.append(cur)
        for e in sorted(graph.edges, key=lambda x: x.to):
            if e.from_ == cur and e.to in indeg:
                indeg[e.to] -= 1
                if indeg[e.to] == 0 and e.to not in ready:
                    ready.append(e.to)
        ready.sort()
    return out
