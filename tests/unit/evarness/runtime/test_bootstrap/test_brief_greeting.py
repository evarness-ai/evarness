"""The briefing greets by the owner's local time (a 15:45 digest said "Good morning").

A brief's ``greetings`` table (vocabulary, in the manifest) picks {greeting}/{daypart}
for literal slots and the subject from the owner's local time.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

from evarness.runtime.handlers.skill_brief import (
    SlotContext,
    _resolve_literal,
    brief_greeting,
    brief_subject,
)
from evarness.tools.skills.models import BriefLiteralSlot, BriefSpec, pick_greeting

_ROOT = Path(__file__).resolve().parents[5]
CHI = ZoneInfo("America/Chicago")


def _morning_briefing() -> BriefSpec:
    raw = yaml.safe_load(
        (_ROOT / "config/skills/builtin/morning-briefing/manifest.yaml").read_text(encoding="utf-8")
    )
    return BriefSpec.model_validate(raw["brief"])


@pytest.mark.parametrize(
    ("hhmm", "subject", "greeting"),
    [
        ("06:55", "Evarness Morning Briefing", "Good morning"),
        ("12:00", "Evarness Afternoon Briefing", "Good afternoon"),
        ("15:45", "Evarness Afternoon Briefing", "Good afternoon"),
        ("19:30", "Evarness Evening Briefing", "Good evening"),
        ("23:10", "Evarness Late Briefing", "Hello"),
        ("01:00", "Evarness Late Briefing", "Hello"),  # before the first start: wraps
    ],
)
def test_the_morning_briefing_greets_by_the_hour(hhmm: str, subject: str, greeting: str) -> None:
    h, m = map(int, hhmm.split(":"))
    now = datetime(2026, 9, 27, h, m, tzinfo=CHI)
    spec = _morning_briefing()
    assert brief_subject(spec, now) == subject
    assert brief_greeting(spec, now)[0] == greeting


def test_a_literal_slot_renders_the_greeting() -> None:
    ctx = SlotContext(
        now=datetime(2026, 9, 27, 15, 45, tzinfo=CHI), tool_index={}, greeting="Good afternoon"
    )
    assert (
        _resolve_literal(BriefLiteralSlot(kind="literal", value="{greeting}."), ctx)
        == "Good afternoon."
    )


def test_a_brief_without_greetings_keeps_its_subject() -> None:
    spec = BriefSpec(
        subject="Weekly report",
        layout="{{date}}",
        slots={"date": {"kind": "literal", "value": "{today}"}},
    )
    assert brief_subject(spec, datetime(2026, 9, 27, 9, tzinfo=CHI)) == "Weekly report"
    assert brief_greeting(spec, datetime(2026, 9, 27, 9, tzinfo=CHI)) == ("", "")


def test_a_subject_that_cannot_be_formatted_is_left_as_written() -> None:
    spec = BriefSpec(subject="Evarness {unknown} Briefing", layout="x")
    assert (
        brief_subject(spec, datetime(2026, 9, 27, 9, tzinfo=CHI)) == "Evarness {unknown} Briefing"
    )


def test_greeting_starts_must_be_hh_mm() -> None:
    with pytest.raises(ValidationError):
        BriefSpec(subject="s", layout="x", greetings=({"starts": "7am", "greeting": "Hi"},))


def test_pick_greeting_empty_table_is_none() -> None:
    assert pick_greeting((), "09:00") is None
