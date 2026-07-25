"""The exception hierarchy — every error Evarness raises descends from
:class:`EvarnessError`, so a consumer can catch the family or the leaf.

Two of these are control flow, not failure: :class:`NodeBlocked` is a
governance decision (the run records it as ``policy_violation`` and stops
honestly) and :class:`RunPaused` is a request for a human decision (the run
records ``run_paused`` and can resume). They live in core because *any*
domain's nodes block and pause; what they block on is the domain's business.
"""

from __future__ import annotations


class EvarnessError(Exception):
    """Base class for every Evarness error."""


class GraphValidationError(EvarnessError, ValueError):
    """The graph failed lint with errors; ``issues`` carries the findings."""

    def __init__(self, issues: list[dict]):
        super().__init__("; ".join(i["message"] for i in issues))
        self.issues = issues


class NodeBlocked(EvarnessError, RuntimeError):
    """A node refused to proceed — a governance block, traced, never silent."""

    def __init__(self, node_id: str, reason: str):
        super().__init__(reason)
        self.node_id = node_id
        self.reason = reason


class RunPaused(EvarnessError, RuntimeError):
    """A node needs a human decision; the run pauses and can be resumed by
    re-executing with the decision supplied."""

    def __init__(self, node_id: str, prompt: str, preview: str = ""):
        super().__init__(prompt)
        self.node_id = node_id
        self.prompt = prompt
        self.preview = preview


class RegistryError(EvarnessError, KeyError):
    """An unknown name was requested from a registry — loud, with candidates."""
