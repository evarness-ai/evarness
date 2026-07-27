"""evarness — prove an AI agent harness before it touches real data.

``import evarness`` gives you the batteries-included surface: the kernel plus
the agents domain, with plugins discovered — loaded lazily, on first
attribute access. For the bare kernel (e.g. when building your own domain),
import from ``evarness.core`` directly — it never loads a domain behind your
back, and ``tests/test_architecture_boundaries.py`` holds that promise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # static types for the lazy surface
    from evarness.core.errors import (
        EvarnessError as EvarnessError,
        GraphValidationError as GraphValidationError,
        NodeBlocked as NodeBlocked,
        RegistryError as RegistryError,
        RunPaused as RunPaused,
    )
    from evarness.core.executor import (
        Emitter as Emitter,
        RunContext as RunContext,
        RunResult as RunResult,
        execute as execute,
    )
    from evarness.core.graph import (
        GraphModel as GraphModel,
        lint as lint,
        migrate as migrate,
        topological_order as topological_order,
    )
    from evarness.core.invariants import (
        check_invariants as check_invariants,
        load_invariant_defs as load_invariant_defs,
    )
    from evarness.core.prove import prove as prove, verify_proof as verify_proof
    from evarness.core.registry import (
        NODE_TYPES as NODE_TYPES,
        load_entry_point_plugins as load_entry_point_plugins,
    )
    from evarness.core.trace import canonical_trace as canonical_trace, trace_digest as trace_digest
    from evarness.domains import agents as agents
    from evarness.domains.agents.sim import Fixture as Fixture, load_fixture as load_fixture

__version__ = "0.1.0a1"

__all__ = [
    "EvarnessError",
    "GraphValidationError",
    "NodeBlocked",
    "RegistryError",
    "RunPaused",
    "Emitter",
    "RunContext",
    "RunResult",
    "execute",
    "GraphModel",
    "lint",
    "migrate",
    "topological_order",
    "check_invariants",
    "load_invariant_defs",
    "prove",
    "verify_proof",
    "NODE_TYPES",
    "load_entry_point_plugins",
    "canonical_trace",
    "trace_digest",
    "Fixture",
    "load_fixture",
    "agents",
    "__version__",
]

_surface: dict[str, Any] = {}


def _load_surface() -> dict[str, Any]:
    from evarness.core import errors, executor, graph, invariants, prove, registry, trace
    from evarness.domains import agents
    from evarness.domains.agents import sim

    registry.load_entry_point_plugins()
    return {
        "EvarnessError": errors.EvarnessError,
        "GraphValidationError": errors.GraphValidationError,
        "NodeBlocked": errors.NodeBlocked,
        "RegistryError": errors.RegistryError,
        "RunPaused": errors.RunPaused,
        "Emitter": executor.Emitter,
        "RunContext": executor.RunContext,
        "RunResult": executor.RunResult,
        "execute": executor.execute,
        "GraphModel": graph.GraphModel,
        "lint": graph.lint,
        "migrate": graph.migrate,
        "topological_order": graph.topological_order,
        "check_invariants": invariants.check_invariants,
        "load_invariant_defs": invariants.load_invariant_defs,
        "prove": prove.prove,
        "verify_proof": prove.verify_proof,
        "NODE_TYPES": registry.NODE_TYPES,
        "load_entry_point_plugins": registry.load_entry_point_plugins,
        "canonical_trace": trace.canonical_trace,
        "trace_digest": trace.trace_digest,
        "Fixture": sim.Fixture,
        "load_fixture": sim.load_fixture,
        "agents": agents,
    }


def __getattr__(name: str) -> Any:
    if name in __all__:
        if not _surface:
            _surface.update(_load_surface())
        return _surface[name]
    raise AttributeError(f"module 'evarness' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
