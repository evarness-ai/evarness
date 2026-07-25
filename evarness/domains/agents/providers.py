"""LLM providers behind one interface.

Provider spec = "<kind>:<model>". This release ships **simulation only**:
`sim:<persona>` resolves to the fixture-scripted `SimLLMProvider`, which is what
makes runs deterministic and proofs reproducible.

Real providers (anthropic:, ollama:) are recognized but refused with a clear
error — they have not graduated into this package yet. Refusing loudly beats
pretending: a graph that names a real provider gets an actionable message, not a
silent fallback to sim (which would make the trace lie about what ran).
"""

from __future__ import annotations

from evarness.domains.agents.sim import Completion, Fixture, SimLLMProvider

__all__ = ["Completion", "ProviderError", "make_provider", "list_providers"]

_REAL_KINDS = ("anthropic", "ollama")


class ProviderError(RuntimeError):
    """A provider could not be constructed or called."""


def make_provider(spec: str, fixture: Fixture | None = None):
    kind, _, _model = (spec or "sim:helpful-v1").partition(":")
    if kind == "sim":
        return SimLLMProvider(spec, fixture or Fixture())
    if kind in _REAL_KINDS:
        raise ProviderError(
            f"provider '{spec}' is a real model provider, and real providers are "
            "not part of this release — every run here is simulation "
            "(fixture-scripted, deterministic). Switch the graph provider to "
            "sim:helpful-v1, or wait for the release that introduces real "
            "providers."
        )
    raise ValueError(f"unknown provider kind '{kind}' in '{spec}' (expected sim:)")


def list_providers() -> list[dict]:
    """What can this install run right now? Sim only, and it says so."""
    return [
        {
            "id": "sim:helpful-v1",
            "kind": "sim",
            "available": True,
            "deterministic": True,
            "note": "fixture-scripted, reproducible — the only provider in "
            "this release; real providers arrive in a later capability",
        }
    ]
