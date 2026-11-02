"""BotStatusHook and its wiring (ADR-0138, invariant B1).

The hook alone (no bot allows; active allows and stamps; any other status denies; an
unregistered bot, a bad ``EVARNESS_BOT`` and a store error deny; no ``bots.db`` is made),
its place first at ``pre_tool_use`` and ``pre_llm_call`` in the default kernel, and the
``bot_id`` it puts on every audit row of a call, taken from its decision only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evarness.kernel.governance import (
    GovernanceKernel,
    HookContext,
    HookDecision,
    HookPoint,
)
from evarness.kernel.governance.audit.log import AuditLog
from evarness.kernel.governance.bots import (
    BOT_ENV,
    BotIdentityStore,
    default_bots_db_path,
)
from evarness.kernel.governance.hooks.tool_payload import BOT_ID, BOT_NAME
from evarness.kernel.governance.plugins.bot_status import BotStatusHook
from evarness.kernel.governance.wiring import build_default_kernel

SHA = "c" * 64


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EVARNESS_HOME", str(tmp_path))
    monkeypatch.delenv(BOT_ENV, raising=False)
    return tmp_path


def _ctx(point: HookPoint = HookPoint.PRE_TOOL_USE, **metadata: Any) -> HookContext:
    return HookContext(
        hook_point=point,
        run_id="run-1",
        agent_type="test",
        payload={"tool_name": "lookup"},
        metadata=metadata,
    )


def _register(home: Path, name: str = "helper", status: str = "active") -> Any:
    store = BotIdentityStore()
    row = store.register(name, str(home / "bots" / name), SHA)
    if status != "active":
        row = store.set_status(name, status)
    return row


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# ------------------------------------------------------------------------- the hook
async def test_no_bot_allows_stamps_nothing_and_opens_nothing(home: Path) -> None:
    opened: list[int] = []

    def opener() -> BotIdentityStore | None:
        opened.append(1)
        return None

    hook = BotStatusHook(opener)
    decision = await hook(_ctx())
    assert decision.outcome == "allow" and decision.audit_metadata == {}
    assert opened == [] and hook.bot_name is None
    assert not default_bots_db_path().exists()


async def test_no_bot_allows_even_when_the_home_holds_bots(home: Path) -> None:
    _register(home, "helper", "disabled")
    decision = await BotStatusHook()(_ctx())
    assert decision.outcome == "allow" and BOT_ID not in decision.audit_metadata


async def test_an_active_bot_is_allowed_and_stamped(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _register(home)
    monkeypatch.setenv(BOT_ENV, "helper")
    for point in (HookPoint.PRE_TOOL_USE, HookPoint.PRE_LLM_CALL):
        decision = await BotStatusHook()(_ctx(point))
        assert decision.outcome == "allow"
        assert decision.audit_metadata == {BOT_ID: row.bot_id, BOT_NAME: "helper"}


@pytest.mark.parametrize("status", ["disabled", "revoked"])
async def test_any_other_status_denies_naming_the_bot(
    home: Path, monkeypatch: pytest.MonkeyPatch, status: str, caplog: pytest.LogCaptureFixture
) -> None:
    row = _register(home, status=status)
    caplog.set_level("WARNING", logger="evarness.kernel.governance.plugins.bot_status")
    hook = BotStatusHook(bot_name="helper")
    for point in (HookPoint.PRE_TOOL_USE, HookPoint.PRE_LLM_CALL):
        decision = await hook(_ctx(point))
        assert decision.outcome == "deny"
        assert "'helper'" in decision.reason and status in decision.reason
        assert decision.audit_metadata[BOT_ID] == row.bot_id
        assert decision.audit_metadata["status"] == status
    assert any(r.getMessage().startswith("bot.deny") for r in caplog.records)


async def test_an_unregistered_bot_is_denied_and_no_file_is_created(home: Path) -> None:
    decision = await BotStatusHook(bot_name="helper")(_ctx())
    assert decision.outcome == "deny" and "no identity row" in decision.reason
    assert decision.audit_metadata["status"] == "unregistered"
    assert not default_bots_db_path().exists()


async def test_an_invalid_evarness_bot_denies_every_call(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(BOT_ENV, "../Not A Name")
    decision = await BotStatusHook()(_ctx())
    assert decision.outcome == "deny" and "EVARNESS_BOT" in decision.reason
    assert decision.severity == "error"


async def test_a_store_error_denies(home: Path) -> None:
    class _Broken:
        def get(self, name: str) -> None:
            raise OSError("disk gone")

    hook = BotStatusHook(lambda: _Broken(), bot_name="helper")  # type: ignore[arg-type,return-value]
    decision = await hook(_ctx())
    assert decision.outcome == "deny" and "OSError" in decision.reason
    assert decision.severity == "error"
    assert "disk gone" not in decision.reason  # the class, never the message


async def test_evarness_bot_is_read_once_when_the_hook_is_built(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(home, "helper", "disabled")
    hook = BotStatusHook()  # built as the owner's assistant
    monkeypatch.setenv(BOT_ENV, "helper")  # set later, in-process
    assert (await hook(_ctx())).outcome == "allow"


async def test_the_row_is_cached_for_the_ttl_then_reread(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(home)
    clock = _Clock()
    hook = BotStatusHook(bot_name="helper", ttl=2.0, clock=clock)
    assert (await hook(_ctx())).outcome == "allow"
    BotIdentityStore().set_status("helper", "disabled")
    clock.now += 1.0
    assert (await hook(_ctx())).outcome == "allow"  # still the cached row
    clock.now += 1.5
    assert (await hook(_ctx())).outcome == "deny"  # mid-run: the next read sees it
    BotIdentityStore().set_status("helper", "active")
    hook.invalidate()
    assert (await hook(_ctx())).outcome == "allow"


async def test_a_bot_registered_after_the_first_call_is_found(home: Path) -> None:
    clock = _Clock()
    hook = BotStatusHook(bot_name="helper", ttl=2.0, clock=clock)
    assert (await hook(_ctx())).outcome == "deny"
    _register(home)
    clock.now += 3.0
    assert (await hook(_ctx())).outcome == "allow"


# ------------------------------------------------------------------------- the wiring
@pytest.mark.parametrize("value", ["helper", "../Not A Name"])
def test_a_bot_process_runs_it_first_at_both_points(
    home: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(BOT_ENV, value)
    kernel = build_default_kernel(audit_log=AuditLog(db_path=home / "audit.db"))
    for point in (HookPoint.PRE_TOOL_USE, HookPoint.PRE_LLM_CALL):
        names = kernel.hook_names(point)
        assert names[0] == "bot_status", (point, names)
        assert names.count("bot_status") == 1
    assert kernel.hook_count(HookPoint.PRE_TOOL_USE) == 11
    # Nowhere else: the status of a bot gates the call, not what came back from it.
    for point in HookPoint:
        if point not in (HookPoint.PRE_TOOL_USE, HookPoint.PRE_LLM_CALL):
            assert "bot_status" not in kernel.hook_names(point)
    assert not default_bots_db_path().exists()


def test_the_owners_assistant_gets_no_bot_hook(home: Path) -> None:
    kernel = build_default_kernel(audit_log=AuditLog(db_path=home / "audit.db"))
    for point in HookPoint:
        assert "bot_status" not in kernel.hook_names(point)
    assert kernel.hook_count(HookPoint.PRE_TOOL_USE) == 10


# ------------------------------------------------------------- the audit rows of a call
class _Forger:
    """A hook at every point that tries to name a bot of its own. Its name is the status
    hook's own: the kernel trusts the instance it was built with, never a name."""

    name = "bot_status"
    hook_point = HookPoint.PRE_TOOL_USE
    priority = 50

    async def __call__(self, ctx: HookContext) -> HookDecision:
        return HookDecision(outcome="allow", reason="ok", audit_metadata={BOT_ID: "forged"})


#: Every point, each fired with a FRESH context, as the runtime does: nothing travels in
#: the metadata from one point to the next, so nothing there can be what names the bot.
_POINTS = tuple(HookPoint)


def _kernel(home: Path, hook: BotStatusHook | None) -> tuple[GovernanceKernel, AuditLog]:
    audit = AuditLog(db_path=home / "audit.db")
    kernel = GovernanceKernel(audit_log=audit)
    if hook is not None:
        kernel.register(hook)
        kernel.register(hook, at=HookPoint.PRE_LLM_CALL)
    forger = _Forger()
    for point in _POINTS:
        kernel.register(forger, at=point)
    kernel.init_lock()
    return kernel, audit


async def _fire_everywhere(kernel: GovernanceKernel, **metadata: Any) -> None:
    for point in _POINTS:
        await kernel.fire(point, _ctx(point, **metadata))


async def test_every_row_at_every_point_carries_the_bot_id(home: Path) -> None:
    row = _register(home)
    kernel, audit = _kernel(home, BotStatusHook(bot_name="helper"))
    await _fire_everywhere(kernel, **{BOT_ID: "claimed"})
    rows = audit.query()
    assert {r.hook_point for r in rows} == {p.value for p in _POINTS}
    assert {r.bot_id for r in rows} == {row.bot_id}  # neither "forged" nor "claimed"


async def test_a_point_fired_before_any_status_check_still_carries_the_bot_id(
    home: Path,
) -> None:
    """``pre_turn`` runs before the first ``pre_llm_call``: the kernel reads the id itself."""
    row = _register(home)
    kernel, audit = _kernel(home, BotStatusHook(bot_name="helper"))
    await kernel.fire(HookPoint.PRE_TURN, _ctx(HookPoint.PRE_TURN))
    assert [r.bot_id for r in audit.query()] == [row.bot_id]


async def test_a_denied_call_of_a_disabled_bot_carries_its_bot_id(home: Path) -> None:
    row = _register(home, status="disabled")
    kernel, audit = _kernel(home, BotStatusHook(bot_name="helper"))
    decision, _ = await kernel.fire(HookPoint.PRE_LLM_CALL, _ctx(HookPoint.PRE_LLM_CALL))
    assert decision.outcome == "deny" and decision.decided_by == "bot_status"
    (only,) = audit.query()
    assert only.decision == "deny" and only.bot_id == row.bot_id


async def test_an_unregistered_bot_writes_no_bot_id(home: Path) -> None:
    kernel, audit = _kernel(home, BotStatusHook(bot_name="helper"))
    await _fire_everywhere(kernel, **{BOT_ID: "claimed"})
    assert {r.bot_id for r in audit.query()} == {None}


async def test_the_owners_process_never_writes_a_bot_id_even_a_forged_one(home: Path) -> None:
    """No status hook (the owner's process): a ``bot_id`` in the metadata or in a hook's
    audit metadata never lands, at any point, whatever bots the home holds."""
    _register(home)
    kernel, audit = _kernel(home, None)
    await _fire_everywhere(kernel, **{BOT_ID: "claimed"})
    decision, ctx = await kernel.fire(HookPoint.PRE_TOOL_USE, _ctx(**{BOT_ID: "claimed"}))
    assert BOT_ID not in ctx.metadata
    rows = audit.query()
    assert len(rows) == len(_POINTS) + 1 and {r.bot_id for r in rows} == {None}
    assert not any(BOT_ID in json.loads(r.payload_json) for r in rows)


async def test_the_default_owner_kernel_never_writes_a_bot_id(home: Path) -> None:
    _register(home)
    kernel = build_default_kernel(audit_log=AuditLog(db_path=home / "audit.db"))
    await kernel.fire(HookPoint.PRE_TURN, _ctx(HookPoint.PRE_TURN, **{BOT_ID: "claimed"}))
    await kernel.fire(HookPoint.PRE_LLM_CALL, _ctx(HookPoint.PRE_LLM_CALL, **{BOT_ID: "x"}))
    rows = AuditLog(db_path=home / "audit.db").query()
    assert rows and {r.bot_id for r in rows} == {None}


def test_a_kernel_guards_one_bot(home: Path) -> None:
    kernel = GovernanceKernel(audit_log=None)
    kernel.register(BotStatusHook(bot_name="helper"))
    with pytest.raises(ValueError, match="one bot"):
        kernel.register(BotStatusHook(bot_name="scout"))


async def test_the_status_read_does_not_run_on_the_event_loop_thread(home: Path) -> None:
    import threading

    _register(home)
    seen: list[bool] = []
    real = BotIdentityStore()

    class _Spy:
        def get(self, name: str) -> Any:
            seen.append(threading.current_thread() is threading.main_thread())
            return real.get(name)

    hook = BotStatusHook(lambda: _Spy(), bot_name="helper")  # type: ignore[arg-type,return-value]
    assert (await hook(_ctx())).outcome == "allow"
    assert seen == [False]
