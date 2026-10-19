import pytest

from evarness.foundation.observability import session_log
from evarness.foundation.observability.trace_builder import get_trace, session_messages


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setattr(session_log, "LOG_DIR", tmp_path)
    return tmp_path


def test_ordinary_ids_resolve_inside_the_log_dir(tmp_path) -> None:
    path = session_log.session_log_path("telegram:42", windows=False)
    assert path == tmp_path / "session-telegram:42.jsonl"


def test_on_windows_what_a_file_name_cannot_hold_is_encoded(tmp_path) -> None:
    """NTFS reads ``session-telegram:42.jsonl`` as a stream of a file named
    ``session-telegram``: every Telegram session's log vanished from the listings."""
    path = session_log.session_log_path("telegram:42", windows=True)
    assert path == tmp_path / "session-telegram%3A42.jsonl"


@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize(
    "session_id", ["telegram:42", "eval:case-7", 'a<b>c"d|e?f*g', "50%off", "web-1a2b3c4d"]
)
def test_a_log_file_names_its_session_back(session_id: str, windows: bool) -> None:
    path = session_log.session_log_path(session_id, windows=windows)
    assert session_log.session_id_from_log_path(path, windows=windows) == session_id


def test_decoding_leaves_what_it_did_not_encode() -> None:
    from pathlib import Path

    for stem in ("session-100%zz", "session-%41", "session-50%"):
        name = Path(f"{stem}.jsonl")
        assert session_log.session_id_from_log_path(name, windows=True) == stem[len("session-") :]


@pytest.mark.parametrize("bad", ["../escape", "a/../../b", "x/y", "\x00z"])
def test_ids_that_leave_the_log_dir_are_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        session_log.session_log_path(bad)


def test_readers_treat_an_escaping_id_as_unknown() -> None:
    assert session_messages("../../etc/passwd") == []
    assert get_trace("../../etc/passwd~0") is None
