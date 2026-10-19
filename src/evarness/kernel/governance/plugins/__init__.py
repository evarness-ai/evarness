"""Governance plugins.

Each plugin implements the ``Hook`` protocol (``name``, ``hook_point``,
``priority``, ``__call__``) and is registered with the
``GovernanceKernel`` at process start. Plugins ship in this package; v1
forbids runtime registration and skill-side hook registration (design
§5.3 trust boundary).
"""

from evarness.kernel.governance.plugins.classifier import (
    ClassificationResult,
    DataClassifier,
    DataClassifierHook,
)
from evarness.kernel.governance.plugins.command_sandbox import CommandSandbox
from evarness.kernel.governance.plugins.cost_limiter import CostLimiter
from evarness.kernel.governance.plugins.credential_broker import CredentialBroker
from evarness.kernel.governance.plugins.destructive_approval import DestructiveApprovalHook
from evarness.kernel.governance.plugins.egress_gate import EgressGate
from evarness.kernel.governance.plugins.fs_jail import FSJail
from evarness.kernel.governance.plugins.mcp_allowlist import (
    MCPAllowlistHook,
    MCPPersonaGrant,
    MCPServerGovernance,
)
from evarness.kernel.governance.plugins.network_egress import NetworkEgress
from evarness.kernel.governance.plugins.output_classifier import OutputClassifierHook
from evarness.kernel.governance.plugins.persona_surface import (
    PersonaPolicy,
    PersonaPolicyDocument,
    PersonaSurface,
)
from evarness.kernel.governance.plugins.post_tool_use_ledger import PostToolUseLedgerHook
from evarness.kernel.governance.plugins.pre_tool_use_ledger import PreToolUseLedgerHook
from evarness.kernel.governance.plugins.redaction import RedactionFilterHook
from evarness.kernel.governance.plugins.tool_policy import ToolPolicyHook

__all__ = [
    "ClassificationResult",
    "CommandSandbox",
    "CostLimiter",
    "CredentialBroker",
    "DataClassifier",
    "DataClassifierHook",
    "DestructiveApprovalHook",
    "EgressGate",
    "FSJail",
    "MCPAllowlistHook",
    "MCPPersonaGrant",
    "MCPServerGovernance",
    "NetworkEgress",
    "PersonaPolicy",
    "PersonaPolicyDocument",
    "PersonaSurface",
    "OutputClassifierHook",
    "PostToolUseLedgerHook",
    "PreToolUseLedgerHook",
    "RedactionFilterHook",
    "ToolPolicyHook",
]
