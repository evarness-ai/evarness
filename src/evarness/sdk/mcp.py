"""Signing an MCP server's launch spec and checking the signature (trust: mcp).

An MCP server runs outside the harness, so what a plugin author can vouch for is the launch
spec: the command, its arguments, the names of the environment it is given and a hash of the
binary and of the script it runs. :func:`build_server_spec` makes that spec from the same
entry ``evarness mcp sign`` signs, so a signature made here is the one the bridge checks. Sign it
with :func:`sign_server_spec`, verify it with :func:`verify_server_signature` against a
:class:`TrustStore`; a changed command, argument, entry script or binary reads as ``invalid``.

This is the signature only. A tool call still passes the governance kernel, which decides
whether it runs; nothing here lets a plugin skip that.
"""

from __future__ import annotations

from evarness.kernel.governance.mcp_signing import (
    ServerSpec,
    SignatureVerdict,
    TrustedKey,
    TrustStore,
    file_sha256,
    generate_keypair,
    public_key_for,
    sign,
    verify_server_signature,
)
from evarness.tools.mcp_bridge import MCPServerConfig, build_server_spec


def sign_server_spec(private_key_b64: str, spec: ServerSpec) -> str:
    """Sign ``spec`` with a base64 Ed25519 private key; return the base64 signature."""
    return sign(private_key_b64, spec.canonical_bytes())


__all__ = [
    "MCPServerConfig",
    "ServerSpec",
    "SignatureVerdict",
    "TrustStore",
    "TrustedKey",
    "build_server_spec",
    "file_sha256",
    "generate_keypair",
    "public_key_for",
    "sign_server_spec",
    "verify_server_signature",
]
