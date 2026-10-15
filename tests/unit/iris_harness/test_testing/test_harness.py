"""``iris_harness.testing.harness``: a real governed IRIS in a throwaway home (R16, L2).

The harness builds the runtime through the composition root, so these drive real turns
and read the real audit ledger; only the model (scripted), the home, the keyring and the
network are replaced.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from iris_harness.sdk import PluginAPI
from iris_harness.sdk.audit import AuditLog
from iris_harness.testing import (
    NetworkBlockedError,
    ScriptedChatModel,
    TurnAuditRow,
    TurnEvent,
    harness,
    plugin,
)

_SCRIPT = {"default": {"content": "Scripted answer."}}


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under ``root``: relative path -> (size, mtime_ns)."""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_a_turn_runs_the_governed_pipeline_and_is_audited(entry: str) -> None:
    with harness(fake_model=_SCRIPT) as h:
        if entry == "chat":
            result = h.chat("hello there, how are you?")
            assert result.events == ()
        else:
            result = h.chat_stream("hello there, how are you?")
            assert result.events[-1] == TurnEvent(kind="done", text="Scripted answer.")
        assert result.answered and result.error is None
        assert result.text == "Scripted answer."
        session = h.turns[-1].session_id
        assert result.session_id == session
        assert h.turns[-1].result is result
        # The turn's audit refs are exactly the ledger rows written in its session.
        assert result.audit_refs
        assert result.audit_refs == tuple(r.id for r in h.audit_rows(session_id=session))
        assert h.audit_rows(hook_point="pre_turn", session_id=session)
        assert h.audit_rows(hook_point="pre_response", session_id=session)
        calls = h.model_calls()
        assert calls, "the general lane answers with the model"
        # Every call went to the scripted model; a row that names a provider names it.
        assert {c.model for c in calls} and all(c.rule == "default" for c in calls)
        # (The provider is in the ledger's raw payload, which TurnAuditRow leaves out.)
        for raw in AuditLog(db_path=h.audit_db).query():
            if raw.hook_point == "pre_llm_call":
                payload = raw.payload_json
                assert '"provider"' not in payload or '"provider": "fake"' in payload
        assert h.audit_gaps() == []


def test_audit_gaps_names_a_model_call_without_its_row() -> None:
    with harness(fake_model=_SCRIPT) as h:
        h.chat("hello there")
        # A call the ledger never saw: the transport answered it with no client around
        # it, so no hook fired (a bare CodingLLMClient still gets the default kernel).
        from langchain_core.messages import HumanMessage

        ScriptedChatModel(model="m").invoke([HumanMessage(content="off the books")])
        gaps = h.audit_gaps()
    assert len(gaps) == 1
    assert "audited at pre_llm_call" in gaps[0]


def _greeter(api: PluginAPI) -> None:
    services = api.services

    def greet(message: str, *, session_id: str, span: Any = None) -> Any:
        if "ping the greeter" not in message:
            return None
        return services.deterministic_reply(
            message=message, session_id=session_id, response="pong", metadata={}, span=span
        )

    api.register_intercept("greeter_ping", greet)


def test_a_plugin_supplied_in_process_mounts_and_its_answer_is_audited() -> None:
    greeter = plugin(_greeter, manifest={"name": "greeter", "provides": ["intercept"]})
    with harness(plugins=[greeter], fake_model=_SCRIPT) as h:
        assert h.plugin_loaded("greeter")
        assert h.plugin_loaded("system")
        result = h.chat("please ping the greeter")
        assert result.text == "pong"
        # A deterministic answer passes PRE_RESPONSE like a generated one (R15), and its
        # row says so, with the handler's name.
        rows = h.audit_rows(hook_point="pre_response", session_id=h.turns[-1].session_id)
        assert rows and all(isinstance(r, TurnAuditRow) for r in rows)
        marked = [r for r in rows if r.deterministic]
        assert [(r.plugin, r.handler, r.decision) for r in marked] == [
            ("response_safety", "greeter_ping", "allow")
        ]
        assert h.model_calls() == ()
        assert h.audit_gaps() == []


def test_an_in_process_plugin_gets_the_manifest_checks() -> None:
    def registers_an_undeclared_tool(api: PluginAPI) -> None:
        api.register_tool("undeclared", "d", lambda args: "x")

    sneaky = plugin(registers_an_undeclared_tool, name="sneaky")
    with harness(plugins=[sneaky], fake_model=_SCRIPT) as h:
        # Refused at registration (ADR-0110) and recorded against the plugin, which
        # stays mounted for whatever else it registered.
        assert h.plugins()["sneaky"][0] == "degraded"
        assert not h.plugin_loaded("sneaky")


def _noop(api: PluginAPI) -> None:
    pass


def _provides_weather(api: PluginAPI) -> None:
    class _Weather:
        async def forecast(self, location: str, days: int = 3) -> Any:  # pragma: no cover
            raise NotImplementedError

    api.provide("weather.forecast", _Weather())


def test_gap_health_reports_a_missing_optional_capability() -> None:
    consumer = plugin(
        _noop, manifest={"name": "trip-advisory", "capabilities": {"uses": ["weather.forecast"]}}
    )
    with harness(plugins=[consumer], fake_model=_SCRIPT) as h:
        # The stable accessor carries the reason: no `_runtime` needed.
        state = h.plugin_states()["trip-advisory"]
        assert state.status == "loaded"
        assert state.failure_count == 0
        assert state.degraded_reason is not None
        assert "weather.forecast" in state.degraded_reason
        # Health says the same, yellow, and the plugin tuple form is unchanged.
        line = next(
            c
            for c in h._runtime.plugin_registry.health_checks()
            if c.target == "plugin:trip-advisory"
        )
        assert line.state.value == "yellow"
        assert state.degraded_reason in line.detail
        assert h.plugins()["trip-advisory"] == ("loaded", None)
        assert h.plugin_states()["system"].degraded_reason is None

    provider = plugin(
        _provides_weather,
        manifest={"name": "weather", "capabilities": {"provides": ["weather.forecast"]}},
    )
    with harness(plugins=[consumer, provider], fake_model=_SCRIPT) as h:
        assert h.plugin_states()["trip-advisory"].degraded_reason is None


def test_an_in_process_plugin_never_stands_in_for_a_profile_one() -> None:
    with pytest.raises(ValueError, match="already in profile"):
        with harness(plugins=[plugin(_greeter, name="system")]):
            pass


def test_a_plugin_needs_a_name() -> None:
    with pytest.raises(ValueError, match="needs a name"):
        plugin(_greeter)
    with pytest.raises(ValueError, match="disagrees"):
        plugin(_greeter, name="a", manifest={"name": "b"})


def test_the_harness_is_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # The suite's own relocated home and data dir stand in for the owner's.
    outer = [Path(os.environ["IRIS_HOME"]), Path(os.environ["IRIS_DATA_DIR"])]
    before = [_tree(root) for root in outer]
    env_before = dict(os.environ)
    keyring_before = keyring.get_keyring()
    home = tmp_path / "home"

    with harness(fake_model=_SCRIPT, home=home) as h:
        h.chat("hello there")
        h.chat_stream("and again")
        assert h.home == home.resolve()
        assert os.environ["IRIS_HOME"] == str(home.resolve())
        # A throwaway master key in the environment; the keyring refuses every call.
        assert os.environ["IRIS_VAULT_MASTER_KEY"] != env_before.get("IRIS_VAULT_MASTER_KEY")
        assert type(keyring.get_keyring()).__module__ == "keyring.backends.fail"
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("192.0.2.1", 80), timeout=1)
        assert h.audit_db.is_file()

    assert [_tree(root) for root in outer] == before
    assert list(cwd.iterdir()) == []
    assert dict(os.environ) == env_before
    assert keyring.get_keyring() is keyring_before
    assert _tree(home), "the harness wrote its stores into its own home"


def test_the_owner_settings_do_not_reach_the_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_PROFILE", "default")
    monkeypatch.setenv("GITHUB_TOKEN", "owner-token")
    with harness(fake_model=_SCRIPT, env={"IRIS_EMAIL_JUDGE": "1"}) as h:
        assert os.environ["IRIS_PROFILE"] == "minimal"
        assert "GITHUB_TOKEN" not in os.environ
        assert os.environ["IRIS_EMAIL_JUDGE"] == "1"
        assert set(h.plugins()) == {"system"}
    assert os.environ["GITHUB_TOKEN"] == "owner-token"


def test_without_a_script_a_model_call_fails_instead_of_reaching_a_model() -> None:
    with harness() as h:
        result = h.chat_stream("hello there")
    assert result.events[-1].kind in {"done", "error"}
    assert result.answered == (result.events[-1].kind == "done")
    assert h.model_calls() == ()


def _echo(api: PluginAPI) -> None:
    api.register_tool(
        "echo_back",
        'Repeat the given word back. Args: {"word": str}.',
        lambda args: f"echo: {args.get('word', '')}",
    )


_ECHO_SCRIPT = {
    "rules": [
        {
            "name": "answer from the tool",
            "match": {"user": r"(?s)Observation:.*echo: banana"},
            "reply": {"content": "Thought: Done.\nFinal Answer: The tool said banana."},
        },
        {
            "name": "call the tool",
            "match": {"user": r"User: Please echo the word banana"},
            "reply": {
                "content": "Thought: Use the tool.\nAction: echo_back\n"
                'Action Input: {"word": "banana"}'
            },
        },
    ]
}


@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_each_loop_step_is_its_own_audited_model_call(entry: str) -> None:
    echo = plugin(
        _echo,
        manifest={"name": "echo", "provides": ["tool"], "tools": {"echo_back": {"effect": "read"}}},
    )
    with harness(plugins=[echo], fake_model=_ECHO_SCRIPT) as h:
        if entry == "chat":
            result = h.chat("Please echo the word banana")
        else:
            result = h.chat_stream("Please echo the word banana")
            assert result.answered, result.error
        assert result.text == "The tool said banana."
        assert [c.rule for c in h.model_calls()] == ["call the tool", "answer from the tool"]
        # Two model calls of one loop run: one key per step (they used to share one).
        # (The router's call is audited too; the script has no rule for it, so it fails
        # and the router falls back to keywords -- an audited attempt, never answered.)
        rows = h.audit_rows(hook_point="pre_llm_call")
        loop_run = {r.run_id for r in rows if r.step_id is not None}
        assert len(loop_run) == 1
        assert {r.step_id for r in rows if r.run_id in loop_run} == {0, 1}
        assert h.audit_gaps() == []


_SHRED_SCRIPT = {
    "rules": [
        {
            "name": "route",
            "match": {"system": "request router"},
            "reply": {"json": {"intent": "general"}},
        },
        {
            "name": "done",
            "match": {"user": r"(?s)Observation: The owner approved.*shredded memo"},
            "reply": {"content": "Thought: Done.\nFinal Answer: The memo is shredded."},
        },
        {
            "name": "declined",
            "match": {"user": r"(?s)Observation: The owner rejected this"},
            "reply": {"content": "Thought: Fine.\nFinal Answer: The memo stays."},
        },
        {
            "name": "shred",
            "match": {"user": r"User: Shred the memo"},
            "reply": {
                "content": 'Thought: Shred it.\nAction: shred_doc\nAction Input: {"doc": "memo"}'
            },
        },
    ]
}


@pytest.mark.parametrize("approve", [True, False])
def test_respond_to_approval_resumes_the_halted_turn(approve: bool) -> None:
    from iris_harness.sdk.approvals import ApprovalQueue

    shredded: list[str] = []

    def shredder(api: PluginAPI) -> None:
        def shred(args: dict[str, Any]) -> str:
            shredded.append(str(args.get("doc", "")))
            return f"shredded {args.get('doc')}"

        api.register_tool("shred_doc", 'Shred a document. Args: {"doc": str}.', shred)

    manifest = {
        "name": "shredder",
        "provides": ["tool"],
        "tools": {"shred_doc": {"effect": "destructive"}},
    }
    with harness(plugins=[plugin(shredder, manifest=manifest)], fake_model=_SHRED_SCRIPT) as h:
        result = h.chat("Shred the memo")
        assert "needs your approval" in result.text
        assert shredded == []
        [pending] = ApprovalQueue().list_pending()

        told = h.respond_to_approval(pending.approval_id, approve=approve)

        # Approved: the pinned call ran and the resumed run answered. Rejected: nothing
        # ran, and the resumed run said so.
        assert shredded == (["memo"] if approve else [])
        assert told == ("The memo is shredded." if approve else "The memo stays.")
        assert ApprovalQueue().list_pending() == []


def test_the_health_watcher_installed_before_the_harness_is_put_back() -> None:
    """The system plugin installs a process-wide health watcher whose store lives in the
    harness's temp home. Left installed after the home is deleted, it sent a later
    `/health/connectors` read to a database that no longer exists."""
    from iris_harness.services.health.watch import current_watcher, install_watcher

    sentinel = object()
    install_watcher(sentinel)  # type: ignore[arg-type]
    try:
        with harness(fake_model=_SCRIPT):
            assert current_watcher() is not sentinel  # the harness's runtime installed its own
        assert current_watcher() is sentinel
    finally:
        install_watcher(None)


_LEAK_TOPIC = "harness.leak_probe"


def _leaky(api: PluginAPI) -> None:
    """Fills process-wide seams a second harness run must not inherit."""
    api.register_api_router("leaky", lambda: None)
    api.register_credential_check("leaky", lambda net_probe: [])
    api.register_footer_line("leaky", lambda start, end: "leaky line")
    api.subscribe(_LEAK_TOPIC, lambda payload: None, scope="process")


def _what_leaks() -> dict[str, bool]:
    from iris_harness.foundation.eventbus import bus as eventbus
    from iris_harness.runtime.api_routes import registered_api_routers
    from iris_harness.services.digest import footer
    from iris_harness.services.health import credentials

    return {
        "api_router": "leaky" in registered_api_routers(),
        "credential_check": "leaky" in credentials._registered,
        "footer_line": "leaky" in footer._LINES,
        # Read the bus as it is, never through get_default_bus(): that creates one when
        # none exists yet, and the probe would itself change the state this test compares.
        "process_subscription": bool(
            eventbus._default_bus is not None and eventbus._default_bus._handlers.get(_LEAK_TOPIC)
        ),
    }


def _state_differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(name for name in before if before[name] != after[name])


def test_a_second_run_does_not_inherit_the_first_runs_process_state() -> None:
    from iris_harness.foundation.process_state import snapshot_process_state

    before = snapshot_process_state().values
    leaky = plugin(_leaky, name="leaky")
    with harness(plugins=[leaky], fake_model=_SCRIPT) as h:
        assert h.plugin_loaded("leaky")
        assert all(_what_leaks().values()), _what_leaks()
    assert not any(_what_leaks().values()), _what_leaks()

    with harness(plugins=[plugin(_greeter, name="greeter")], fake_model=_SCRIPT) as h:
        assert set(h.plugins()) == {"system", "greeter"}
        assert not any(_what_leaks().values()), _what_leaks()
        h.chat("hello there")

    # Every declared piece of process state is exactly what it was before either run.
    assert _state_differences(before, snapshot_process_state().values) == []


# --- trust: mcp ------------------------------------------------------------------------

_WEATHER = Path(__file__).resolve().parents[4] / "examples" / "08-mcp" / "weather_server.py"
_MCP_MANIFEST = {
    "name": "weather",
    "tools": {"forecast": {"effect": "read", "description": "Today's forecast for a city."}},
}
_MCP_SCRIPT: dict[str, Any] = {
    "rules": [
        {
            "name": "route",
            "match": {"system": "request router"},
            "reply": {"json": {"intent": "general"}},
        },
        {
            "name": "answer",
            "match": {"user": r"(?s)Observation:.*Light rain"},
            "reply": {"content": "Thought: Got it.\nFinal Answer: Oslo has light rain, 11 C."},
        },
        {
            "name": "call",
            "match": {"user": r"User: Weather in Oslo"},
            "reply": {
                "content": "Thought: Look it up.\nAction: forecast\n"
                'Action Input: {"city": "oslo"}'
            },
        },
    ]
}


def _weather() -> Any:
    return plugin(manifest=_MCP_MANIFEST, server=[sys.executable, str(_WEATHER)])


def test_a_trust_mcp_plugin_mounts_without_a_setup() -> None:
    with harness(plugins=[_weather()], fake_model=_MCP_SCRIPT) as h:
        assert h.plugin_loaded("weather"), h.plugins()["weather"]


@pytest.mark.parametrize("stream", [False, True])
def test_a_scripted_turn_calls_the_mcp_tool_with_one_governed_set_of_rows(stream: bool) -> None:
    with harness(plugins=[_weather()], fake_model=_MCP_SCRIPT) as h:
        result = (h.chat_stream if stream else h.chat)("Weather in Oslo?")

        assert result.text == "Oslo has light rain, 11 C."
        before = h.audit_rows(hook_point="pre_tool_use", session_id=result.session_id)
        after = h.audit_rows(hook_point="post_tool_use", session_id=result.session_id)
        assert [r.tool for r in before if r.tool == "forecast"] and {r.tool for r in before} == {
            "forecast"
        }
        # One governed call: a second pass by the bridge would add rows under mcp/<server>/<tool>.
        assert len({r.call_id for r in before}) == 1
        assert {r.tool_plugin for r in before} == {"weather"}
        assert all(r.caller and r.caller.startswith("model:") for r in before)
        assert all(r.decision == "allow" for r in before)
        assert {r.tool for r in after} == {"forecast"}
        assert h.audit_gaps() == []


def test_the_server_form_needs_the_tools_declared_and_no_setup() -> None:
    with pytest.raises(ValueError, match="takes no setup"):
        plugin(lambda api: None, manifest=_MCP_MANIFEST, server=["x"])
    with pytest.raises(Exception, match="at least one tool"):
        plugin(name="bare", server=["x"])
    with pytest.raises(ValueError, match="already declares"):
        plugin(manifest={**_MCP_MANIFEST, "trust": "mcp", "server": {"command": "x"}}, server=["y"])


def test_an_mcp_tool_the_manifest_does_not_declare_is_not_reachable() -> None:
    script = {
        "rules": [
            {
                "name": "route",
                "match": {"system": "request router"},
                "reply": {"json": {"intent": "general"}},
            },
            {
                "name": "other",
                "match": {"user": r"User: Weather in Oslo"},
                "reply": {"content": "Thought: x.\nAction: delete_everything\nAction Input: {}"},
            },
        ]
    }
    with harness(plugins=[_weather()], fake_model=script) as h:
        h.chat("Weather in Oslo?")
        rows = h.audit_rows(hook_point="pre_tool_use")
        assert "delete_everything" not in {r.tool for r in rows if r.decision == "allow"}


def _ask(question: str, action: str, observed: str, city: str = "") -> dict[str, Any]:
    """A script that routes, asks for ``action``, then answers once it observes ``observed``."""
    return {
        "rules": [
            {
                "name": "route",
                "match": {"system": "request router"},
                "reply": {"json": {"intent": "general"}},
            },
            {
                "name": "answer",
                "match": {"user": rf"(?s)Observation:.*{observed}"},
                "reply": {"content": "Thought: Done.\nFinal Answer: done."},
            },
            {
                "name": "call",
                "match": {"user": f"User: {question}"},
                "reply": {
                    "content": f"Thought: Look it up.\nAction: {action}\n"
                    f'Action Input: {{"city": "{city}"}}'
                },
            },
        ]
    }


def test_an_mcp_tool_that_answers_is_error_is_recorded_as_a_failed_call() -> None:
    """The server answers ``isError: true`` (no forecast for the city): the model still
    reads its text, and the call's ``post_tool_use`` rows say it failed (#112 M9b)."""
    script = _ask("Weather in Atlantis", "forecast", "no forecast for", city="atlantis")
    with harness(plugins=[_weather()], fake_model=script) as h:
        result = h.chat("Weather in Atlantis?")

        after = h.audit_rows(hook_point="post_tool_use", session_id=result.session_id)
        assert after and {r.tool for r in after} == {"forecast"}
        assert {r.tool_error for r in after} == {"ToolReportedError"}
        observed = [c for c in h.model_calls() if c.rule == "answer"]
        assert observed and "no forecast for" in observed[-1].user


def test_an_mcp_tool_that_answers_is_recorded_without_a_tool_error() -> None:
    with harness(plugins=[_weather()], fake_model=_MCP_SCRIPT) as h:
        result = h.chat("Weather in Oslo?")

        after = h.audit_rows(hook_point="post_tool_use", session_id=result.session_id)
        assert after and all(r.tool_error is None for r in after)


_FAILING_SERVER = """\
import json, sys

TOOLS = [{"name": "forecast", "description": "f", "inputSchema": {"type": "object"}}]
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    if req.get("id") is None:
        continue
    method = req.get("method")
    reply = {"jsonrpc": "2.0", "id": req["id"]}
    if method == "tools/call":
        city = (req["params"].get("arguments") or {}).get("city")
        if city == "boom":
            reply["error"] = {"code": -32603, "message": "the server broke"}
        else:
            text = "Sunny" if city == "lisbon" else "no forecast"
            reply["result"] = {"content": [{"type": "text", "text": text}],
                               "isError": city != "lisbon"}
    elif method == "tools/list":
        reply["result"] = {"tools": TOOLS}
    else:
        reply["result"] = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "failing", "version": "0"}}
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
"""


@pytest.mark.parametrize(
    ("city", "observed", "expected"),
    [
        ("boom", "the server broke", "ValueError"),  # a JSON-RPC error raises
        ("atlantis", "no forecast", "ToolReportedError"),
        ("lisbon", "Sunny", None),
    ],
)
def test_turn_audit_row_tool_error_names_how_a_tool_call_failed(
    tmp_path: Path, city: str, observed: str, expected: str | None
) -> None:
    """``TurnAuditRow.tool_error`` (#297): on the ``post_tool_use`` rows of a call that
    failed, the class of what the call raised or ``ToolReportedError`` for an ``isError``
    result; ``None`` on a call that succeeded and on every row that is not ``post_tool_use``."""
    server = tmp_path / "server.py"
    server.write_text(_FAILING_SERVER, encoding="utf-8")
    weather = plugin(manifest=_MCP_MANIFEST, server=[sys.executable, str(server)])
    script = _ask("Weather here", "forecast", observed, city=city)
    with harness(plugins=[weather], fake_model=script) as h:
        result = h.chat("Weather here?")

        rows = h.audit_rows(session_id=result.session_id)
        after = [r for r in rows if r.hook_point == "post_tool_use"]
        assert after and {r.tool for r in after} == {"forecast"}
        assert {r.tool_error for r in after} == {expected}
        assert all(r.tool_error is None for r in rows if r.hook_point != "post_tool_use")


_RECORDING_SERVER = """\
import json, sys
from pathlib import Path

LOG = Path(sys.argv[1])
TOOLS = [{"name": n, "description": n, "inputSchema": {"type": "object"}}
         for n in ("forecast", "delete_everything")]
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    if req.get("id") is None:
        continue
    method = req.get("method")
    if method == "tools/call":
        with LOG.open("a") as f:
            f.write(req["params"]["name"] + "\\n")
        result = {"content": [{"type": "text", "text": "done"}], "isError": False}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    else:
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "rec", "version": "0"}}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n")
    sys.stdout.flush()
"""


def test_a_tool_the_server_offers_but_the_manifest_does_not_declare_never_reaches_it(
    tmp_path: Path,
) -> None:
    """The manifest's ``tools:`` is the declared list: a tool the server offers outside it
    is never registered, so a model that asks for it reaches no server and runs no tool."""
    script_path = tmp_path / "server.py"
    script_path.write_text(_RECORDING_SERVER, encoding="utf-8")
    log = tmp_path / "calls.log"
    weather = plugin(manifest=_MCP_MANIFEST, server=[sys.executable, str(script_path), str(log)])
    script = _ask("Wipe it", "delete_everything", "unknown tool")
    with harness(plugins=[weather], fake_model=script) as h:
        result = h.chat("Wipe it")

        # The model did ask for it, and was told the tool does not exist.
        assert [c for c in h.model_calls() if c.rule == "call"]
        assert [c for c in h.model_calls() if c.rule == "answer"]
        # No governed tool call was made: no tool-use row at all. (A post_step row may name
        # the action the model wrote; that is the loop's record of the step, not a call.)
        rows = h.audit_rows(session_id=result.session_id)
        assert not [r for r in rows if r.hook_point in ("pre_tool_use", "post_tool_use")]
    assert not log.exists()  # the server never received a tools/call


def test_seed_writes_files_under_the_home_and_refuses_a_path_that_leaves_it() -> None:
    with harness(seed={"plugins/notes.txt": "hello", "a/b.bin": b"\x00"}) as h:
        assert (h.home / "plugins" / "notes.txt").read_text() == "hello"
        assert (h.home / "a" / "b.bin").read_bytes() == b"\x00"
    with pytest.raises(ValueError, match="outside the harness home"):
        with harness(seed={"../escape.txt": "x"}):
            pass


def test_a_declared_mcp_tool_is_described_by_the_tool_service() -> None:
    with harness(plugins=[_weather()], fake_model=_MCP_SCRIPT) as h:
        [info] = h._runtime.tool_service.describe("forecast")

        assert (info.name, info.effect) == ("forecast", "read")
        assert "Today's forecast" in info.description
        assert h._runtime.tool_service.describe("delete_everything") == []
