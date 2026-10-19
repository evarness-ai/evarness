"""Evarness governance layer — unified, mandatory-passage enforcement plane.

Design: docs/architecture/unified-governance-layer.md
Decision: project-evarness-prd/13-decisions-and-rationale.md ADR-020

Phase 1 (foundations) ships:
- Hook framework types (HookPoint, HookContext, HookDecision, Hook)
- GovernanceKernel with init-lock enforcement (no runtime hook registration)
- Phase 1 plugins (DataClassifier, EgressGate, ToolPolicyHook) and default kernel wiring
- CI gate tests asserting the §5.3 trust boundary
"""

from evarness.kernel.governance.hooks.types import (
    DataClassification,
    Hook,
    HookContext,
    HookDecision,
    HookOutcome,
    HookPoint,
    HookSeverity,
    LLMTier,
)
from evarness.kernel.governance.kernel import (
    GovernanceKernel,
    HookRegistrationLockedError,
    KernelNotInitializedError,
)
from evarness.kernel.governance.wiring import (
    ENV_FLAG,
    build_default_kernel,
    kernel_from_env,
)

__all__ = [
    "ENV_FLAG",
    "DataClassification",
    "GovernanceKernel",
    "Hook",
    "HookContext",
    "HookDecision",
    "HookOutcome",
    "HookPoint",
    "HookRegistrationLockedError",
    "HookSeverity",
    "KernelNotInitializedError",
    "LLMTier",
    "build_default_kernel",
    "kernel_from_env",
]
