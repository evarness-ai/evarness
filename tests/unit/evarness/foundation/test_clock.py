"""foundation.clock: aware-UTC "now", and a timestamp parser that never raises."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from evarness.foundation.clock import as_utc, parse_iso, utc_now, utc_now_iso


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is UTC
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


def test_utc_now_iso_is_the_stores_format() -> None:
    text = utc_now_iso()
    assert text.endswith("+00:00")
    assert datetime.fromisoformat(text).tzinfo is not None


def test_as_utc_reads_naive_as_utc_and_converts_aware() -> None:
    assert as_utc(datetime(2026, 9, 27, 12, 0)) == datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    chicago = timezone(timedelta(hours=-5))
    converted = as_utc(datetime(2026, 9, 27, 7, 0, tzinfo=chicago))
    assert converted.tzinfo is UTC and converted.hour == 12


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-27T12:00:00+00:00", datetime(2026, 9, 27, 12, 0, tzinfo=UTC)),
        ("2026-09-27T12:00:00Z", datetime(2026, 9, 27, 12, 0, tzinfo=UTC)),
        ("2026-09-27T12:00:00", datetime(2026, 9, 27, 12, 0, tzinfo=UTC)),
        ("2026-09-27T07:00:00-05:00", datetime(2026, 9, 27, 12, 0, tzinfo=UTC)),
        ("  2026-09-27  ", datetime(2026, 9, 27, tzinfo=UTC)),
    ],
)
def test_parse_iso_returns_aware_utc(value: str, expected: datetime) -> None:
    parsed = parse_iso(value)
    assert parsed == expected and parsed is not None and parsed.tzinfo is UTC


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", 20260927, b"2026-09-27"])
def test_parse_iso_never_raises(value: object) -> None:
    assert parse_iso(value) is None
    fallback = datetime(2000, 1, 1, tzinfo=UTC)
    assert parse_iso(value, default=fallback) is fallback


# --- the owner's wall clock (local_now / local_today) ---


def test_local_now_follows_evarness_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    from evarness.foundation import clock

    monkeypatch.setenv("EVARNESS_TZ", "Asia/Kolkata")
    now = clock.local_now()
    assert str(now.tzinfo) == "Asia/Kolkata"
    assert clock.local_today() == now.date()


def test_local_now_without_evarness_tz_is_the_machines_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not UTC: a run without EVARNESS_TZ always meant the machine's clock for "now"/"today"."""
    from datetime import datetime

    from evarness.foundation import clock

    monkeypatch.delenv("EVARNESS_TZ", raising=False)
    assert clock.local_now().utcoffset() == datetime.now().astimezone().utcoffset()


def test_a_bad_evarness_tz_falls_back_to_utc_like_the_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evarness.foundation import clock

    monkeypatch.setenv("EVARNESS_TZ", "Not/AZone")
    assert str(clock.local_now().tzinfo) == "UTC"


def test_the_digest_reads_the_same_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    from evarness.foundation import clock
    from evarness.services.digest import settings

    assert settings.evarness_timezone is clock.evarness_timezone
