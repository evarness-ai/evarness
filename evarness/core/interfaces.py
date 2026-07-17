"""The typed seams — what an extension implements, stated as Protocols.

These are structural (duck) types: no inheritance required, mypy checks
conformance. A domain's environment, provider, or event consumer implements
the protocol; the kernel programs against it and nothing more.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Environment(Protocol):
    """The scripted world a graph executes against. The kernel needs only the
    scenario's identity and default input; everything else (scripted tools,
    model responses, memory stores) is vocabulary between the domain's nodes
    and its own environment implementation."""

    scenario: str
    user_input: str


@runtime_checkable
class Provider(Protocol):
    """A model behind one interface. ``deterministic`` is load-bearing: the
    run's determinism claim ANDs this with every registered inspector."""

    name: str
    deterministic: bool

    def complete(self, prompt: str, temperature: float = ..., max_tokens: int = ...) -> Any: ...


@runtime_checkable
class EventSink(Protocol):
    """Anything that consumes live events (UIs, recorders, bridges)."""

    def __call__(self, event: dict) -> None: ...
