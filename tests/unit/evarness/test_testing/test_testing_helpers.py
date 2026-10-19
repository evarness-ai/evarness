"""``evarness.testing``: the stable-tier helpers a plugin's tests use (OSS plan R16)."""

from __future__ import annotations

import asyncio
import os
import socket

import pytest

from evarness.llm.fake import FAKE_PROVIDER
from evarness.llm.tier_router import FORCED_PROVIDER_ENV
from evarness.testing import NetworkBlockedError, no_network, transcript, use_fake_model


def test_use_fake_model_selects_the_fake_and_restores_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evarness.llm.client import CodingLLMClient, CodingLLMConfig

    monkeypatch.setenv(FORCED_PROVIDER_ENV, "ollama")
    with use_fake_model({"default": {"content": "scripted"}}):
        assert os.environ[FORCED_PROVIDER_ENV] == FAKE_PROVIDER
        client = CodingLLMClient(CodingLLMConfig(provider="fake", model="m"))
        assert client.invoke(system_prompt="", user_prompt="hi") == "scripted"
        assert [c.rule for c in transcript()] == ["default"]
    assert os.environ[FORCED_PROVIDER_ENV] == "ollama"


def test_no_network_refuses_outbound_connections_and_restores_them() -> None:
    real = socket.socket.connect
    with no_network() as attempted:
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("192.0.2.1", 80), timeout=1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(NetworkBlockedError):
                sock.connect(("192.0.2.1", 80))
        finally:
            sock.close()
    assert len(attempted) == 2
    assert socket.socket.connect is real


def test_no_network_lets_an_event_loop_make_its_self_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On Windows socket.socketpair() connects over loopback, and every new event loop
    # makes one; refusing it failed every async turn under no_network() there. Where the OS
    # has a native pair, use CPython's loopback fallback, so this runs Windows' path anywhere.
    fallback = getattr(socket, "_fallback_socketpair", None)
    if fallback is not None:
        monkeypatch.setattr(socket, "socketpair", fallback)
    real = socket.socketpair
    with no_network() as attempted:
        left, right = socket.socketpair()
        left.close()
        right.close()
        assert asyncio.run(asyncio.sleep(0, result="ran")) == "ran"
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("192.0.2.1", 80), timeout=1)
    assert attempted == [("192.0.2.1", 80)]
    assert socket.socketpair is real
