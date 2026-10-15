"""The stdio transport against servers that misbehave in the ways an ordinary one does (#112)."""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from iris_harness.kernel.governance.mcp_signing import MCPSigningConfig, TrustStore
from iris_harness.tools.mcp_bridge import MCPServerConfig, MCPTransport, MCPTransportError

_SERVER = textwrap.dedent(r"""
    import json, sys, time

    mode = sys.argv[1]


    def line(obj):
        return json.dumps(obj) + "\n"


    def send(text):
        sys.stdout.write(text)
        sys.stdout.flush()


    requests = []
    for raw in sys.stdin:
        message = json.loads(raw)
        if "id" in message:
            requests.append(message)
        if len(requests) == 2:
            break
    init, call = requests
    init_reply = line({"jsonrpc": "2.0", "id": init["id"], "result": {"capabilities": {}}})
    call_reply = line({"jsonrpc": "2.0", "id": call["id"], "result": {"tools": [{"name": "t"}]}})

    if mode == "coalesced":
        send(init_reply + call_reply)
    elif mode == "half":
        send(init_reply)
        send(call_reply[:20])
        time.sleep(30)
    elif mode == "banner":
        send("Example server 1.0 starting\n{not json at all\n")
        send(init_reply + call_reply)
    elif mode == "chatty_stderr":
        sys.stderr.write("x" * 400_000)
        sys.stderr.flush()
        send(init_reply + call_reply)
    elif mode == "wrong_id":
        send(init_reply)
        send(line({"jsonrpc": "2.0", "id": "someone-else", "result": {}}))
        time.sleep(30)
    elif mode == "ping":
        send(line({"jsonrpc": "2.0", "id": 99, "method": "ping"}))
        send(line({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}))
        send(init_reply + call_reply)
    elif mode == "crash":
        sys.stderr.write("ModuleNotFoundError: No module named 'stale_registration'\n")
        sys.stderr.flush()
        sys.exit(3)
    elif mode == "framed":
        for reply in (init_reply, call_reply):
            body = reply.strip().encode()
            send("Content-Length: %d\r\n\r\n%s" % (len(body), body.decode()))
    elif mode == "big":
        text = "x" * int(sys.argv[2])
        big = line({"jsonrpc": "2.0", "id": call["id"], "result": {"text": text}})
        send(init_reply + big)
    elif mode == "silent":
        time.sleep(30)
    """)


def _transport(tmp_path: Path) -> MCPTransport:
    return MCPTransport(
        tmp_path, signing_config=MCPSigningConfig.disabled(), trust_store=TrustStore.empty()
    )


def _server(tmp_path: Path, mode: str, *, timeout: float = 5.0) -> MCPServerConfig:
    script = tmp_path / "server.py"
    script.write_text(_SERVER, encoding="utf-8")
    return MCPServerConfig(
        name="fake",
        enabled=True,
        transport="stdio",
        command=sys.executable,
        args=(str(script), mode),
        timeout_seconds=timeout,
    )


def _call(tmp_path: Path, mode: str, *, timeout: float = 5.0) -> object:
    return _transport(tmp_path).invoke(
        _server(tmp_path, mode, timeout=timeout), method="tools/list", params={}
    )


def _big_server(tmp_path: Path, size: int, **overrides: object) -> MCPServerConfig:
    server = _server(tmp_path, "big", timeout=10)
    return server.model_copy(update={"args": (*server.args, str(size)), **overrides})


def test_a_server_that_answers_both_requests_in_one_write_is_read_every_time(
    tmp_path: Path,
) -> None:
    for _ in range(20):
        assert _call(tmp_path, "coalesced") == {"tools": [{"name": "t"}]}


def test_a_half_written_response_ends_at_the_deadline(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(MCPTransportError, match="no answer within 1s"):
        _call(tmp_path, "half", timeout=1)
    assert time.monotonic() - started < 4


def test_a_silent_server_ends_at_the_deadline(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(MCPTransportError, match="no answer within 1s"):
        _call(tmp_path, "silent", timeout=1)
    assert time.monotonic() - started < 4


def test_a_banner_and_a_broken_json_line_on_stdout_are_skipped(tmp_path: Path) -> None:
    started = time.monotonic()

    assert _call(tmp_path, "banner", timeout=3) == {"tools": [{"name": "t"}]}
    assert time.monotonic() - started < 3


def test_a_server_writing_400kb_to_stderr_does_not_stall_the_call(tmp_path: Path) -> None:
    started = time.monotonic()

    assert _call(tmp_path, "chatty_stderr", timeout=3) == {"tools": [{"name": "t"}]}
    assert time.monotonic() - started < 3


def test_a_response_to_a_request_that_was_never_sent_is_a_transport_error_naming_it(
    tmp_path: Path,
) -> None:
    with pytest.raises(MCPTransportError, match="'someone-else'"):
        _call(tmp_path, "wrong_id")


def test_a_server_ping_and_notification_do_not_stand_in_for_the_answer(tmp_path: Path) -> None:
    assert _call(tmp_path, "ping") == {"tools": [{"name": "t"}]}


def test_a_server_that_dies_at_start_says_its_exit_code_and_why(tmp_path: Path) -> None:
    with pytest.raises(MCPTransportError) as raised:
        _call(tmp_path, "crash")

    message = str(raised.value)
    assert "exit code 3" in message and "No module named 'stale_registration'" in message


def test_a_timeout_carries_what_the_server_wrote_to_stderr(tmp_path: Path) -> None:
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys, time\nsys.stderr.write('warming up\\n'); sys.stderr.flush()\ntime.sleep(30)\n"
    )
    server = MCPServerConfig(
        name="noisy",
        enabled=True,
        transport="stdio",
        command=sys.executable,
        args=(str(script),),
        timeout_seconds=1,
    )

    with pytest.raises(MCPTransportError, match="warming up"):
        _transport(tmp_path).invoke(server, method="tools/list", params={})


def test_a_content_length_framed_server_is_still_read(tmp_path: Path) -> None:
    assert _call(tmp_path, "framed") == {"tools": [{"name": "t"}]}


def test_a_transport_error_is_a_value_error_so_existing_callers_still_catch_it() -> None:
    assert issubclass(MCPTransportError, ValueError)


# --- a result's size is capped (#298) ---------------------------------------------------------


def test_a_result_under_the_cap_is_returned(tmp_path: Path) -> None:
    server = _big_server(tmp_path, 100_000, max_result_bytes=200_000)

    result = _transport(tmp_path).invoke(server, method="tools/list", params={})

    assert len(result["text"]) == 100_000  # type: ignore[index]


def test_a_three_megabyte_result_is_refused_under_the_default_cap(tmp_path: Path) -> None:
    server = _big_server(tmp_path, 3_000_000)

    with pytest.raises(MCPTransportError, match="max_result_bytes"):
        _transport(tmp_path).invoke(server, method="tools/list", params={})


def test_a_stdio_server_is_cut_off_at_the_cap_not_read_to_the_end(tmp_path: Path) -> None:
    # 30 MB is under the absolute buffer limit, so only the per-server cap can stop it early.
    server = _big_server(tmp_path, 30_000_000, max_result_bytes=100_000)

    with pytest.raises(MCPTransportError, match="without a complete message"):
        _transport(tmp_path).invoke(server, method="tools/list", params={})


def test_the_cap_applies_to_an_http_result_too(tmp_path: Path) -> None:
    http = MCPServerConfig(
        name="remote",
        enabled=True,
        transport="http",
        url="http://127.0.0.1:1/mcp",
        max_result_bytes=1000,
    )
    transport = MCPTransport(
        tmp_path,
        signing_config=MCPSigningConfig.disabled(),
        trust_store=TrustStore.empty(),
        http_requester=lambda _s, _p: {"result": {"text": "y" * 5000}},
    )

    with pytest.raises(MCPTransportError, match="returned"):
        transport.invoke(http, method="tools/list", params={})
