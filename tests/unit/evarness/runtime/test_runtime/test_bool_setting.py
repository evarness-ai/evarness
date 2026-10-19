"""``bool_setting``: an on/off setting read with its declared default (ADR-0120)."""

from __future__ import annotations

import pytest

from evarness.foundation.settings.catalog import (
    SettingDeclaration,
    build_catalog,
    load_core_catalog,
)
from evarness.runtime.settings_catalog import bool_setting


def _decl(kind: str, default: object) -> SettingDeclaration:
    return SettingDeclaration(
        kind=kind,  # type: ignore[arg-type]
        default=default,
        applies="next_run",
        label="A test switch",
        description="Switches a test job.",
        tab="features",
    )


_CATALOG = build_catalog(
    load_core_catalog(),
    [
        (
            "plugin:test",
            {
                "EVARNESS_TEST_ON_SWITCH": _decl("bool", True),
                "EVARNESS_TEST_OFF_SWITCH": _decl("bool", False),
                "EVARNESS_TEST_COUNT": _decl("int", 3),
            },
        )
    ],
)


@pytest.mark.parametrize(
    ("name", "env", "expected"),
    [
        ("EVARNESS_TEST_ON_SWITCH", None, True),  # unset: the declared default (on)
        ("EVARNESS_TEST_ON_SWITCH", "  ", True),  # blank counts as unset
        ("EVARNESS_TEST_ON_SWITCH", "0", False),
        ("EVARNESS_TEST_ON_SWITCH", "off", False),
        ("EVARNESS_TEST_OFF_SWITCH", None, False),
        ("EVARNESS_TEST_OFF_SWITCH", "yes", True),
        ("EVARNESS_TEST_OFF_SWITCH", "1", True),
        ("EVARNESS_TEST_UNDECLARED", None, False),  # nobody declares it: off
        ("EVARNESS_TEST_UNDECLARED", "on", True),  # but a set value is read as set
        ("EVARNESS_TEST_COUNT", None, False),  # not an on/off setting
    ],
)
def test_bool_setting(
    monkeypatch: pytest.MonkeyPatch, name: str, env: str | None, expected: bool
) -> None:
    if env is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, env)
    assert bool_setting(name, _CATALOG) is expected
