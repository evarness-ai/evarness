"""evarness — prove an AI agent harness before it touches real data.

``import evarness`` gives you the batteries-included surface: the kernel plus
the agents domain, with plugins discovered. For the bare kernel (e.g. when
building your own domain), import from ``evarness.core`` directly — it never
loads a domain behind your back.
"""

from evarness.core.errors import (
    EvarnessError,
    GraphValidationError,
    NodeBlocked,
    RegistryError,
    RunPaused,
)
from evarness.core.executor import Emitter, RunContext, RunResult, execute
from evarness.core.graph import GraphModel, lint, migrate, topological_order
from evarness.core.invariants import check_invariants, load_invariant_defs
from evarness.core.prove import prove, verify_proof
from evarness.core.registry import NODE_TYPES, load_entry_point_plugins
from evarness.core.trace import canonical_trace, trace_digest
from evarness.domains import agents as agents  # registers the agents domain
from evarness.domains.agents.sim import Fixture, load_fixture

__version__ = "0.1.0"

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

load_entry_point_plugins()
