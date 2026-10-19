"""Which stdio launchers leave the server's code outside the signature (#287)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evarness.kernel.governance.mcp_signing.launcher import python_module, unpinned_launcher
from evarness.kernel.governance.mcp_signing.spec import tree_sha256

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("npx", ("-y", "@modelcontextprotocol/server-github@1.2.3")),
        ("npx", ("pkg@1.2.3",)),
        ("bunx", ("pkg@2.0.0-rc.1",)),
        ("uvx", ("mcp-server-git==0.6.2",)),
        ("uvx", ("--from", "mcp-server-git==0.6.2", "mcp-server-git")),
        ("uvx", ("pkg@1.2.3",)),
        ("pipx", ("run", "pkg==1.2.3")),
        ("docker", ("run", "-i", "--rm", f"ghcr.io/org/server@{DIGEST}")),
        ("podman", ("run", f"img@{DIGEST}")),
        ("/usr/local/bin/my-server", ()),  # a plain binary is covered by its own hash
        ("node", ("-e", "1")),  # not a launcher this rule knows
    ],
)
def test_a_pinned_or_unknown_launcher_has_no_gap(command: str, args: tuple[str, ...]) -> None:
    assert unpinned_launcher(command, args) is None


@pytest.mark.parametrize(
    ("command", "args", "says"),
    [
        ("npx", ("pkg",), "exact version"),
        ("npx", ("-y", "pkg@latest"), "exact version"),
        ("npx", ("-y", "pkg@^1.2.0"), "exact version"),
        ("npx", ("-y", "@scope/pkg"), "exact version"),
        ("npx", (), "exact version"),
        ("uvx", ("pkg",), "exact version"),
        ("uvx", ("pkg>=1.0",), "exact version"),
        ("pipx", ("run", "pkg"), "exact version"),
        ("docker", ("run", "-i", "ghcr.io/org/server:latest"), "digest"),
        ("podman", ("run", "img:1.2.3"), "digest"),  # a tag moves; only a digest pins
        ("python3", ("-m", "installed_pkg"), "-m installed_pkg"),
        ("python3.12", ("-m", "a.b"), "-m a.b"),
    ],
)
def test_an_unpinned_launcher_says_why(command: str, args: tuple[str, ...], says: str) -> None:
    reason = unpinned_launcher(command, args)

    assert reason is not None and says in reason


def test_the_command_is_read_by_name_not_by_path_or_extension() -> None:
    assert unpinned_launcher("/usr/bin/npx", ("pkg",)) is not None
    assert unpinned_launcher("C:\\tools\\npx.cmd", ("pkg",)) is not None


def test_python_module_finds_the_module_only_for_a_python_interpreter() -> None:
    assert python_module("python3", ("-u", "-m", "pkg.sub")) == "pkg.sub"
    assert python_module("python3", ("server.py",)) is None
    assert python_module("node", ("-m", "pkg")) is None
    assert python_module(None, ("-m", "pkg")) is None


def test_tree_hash_changes_with_an_edit_an_added_file_or_a_rename(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__main__.py").write_text("print(1)\n")
    first = tree_sha256(pkg)

    (pkg / "__main__.py").write_text("print(2)\n")
    edited = tree_sha256(pkg)
    (pkg / "extra.py").write_text("x = 1\n")
    added = tree_sha256(pkg)
    (pkg / "extra.py").rename(pkg / "renamed.py")
    renamed = tree_sha256(pkg)

    assert len({first, edited, added, renamed}) == 4


def test_tree_hash_ignores_bytecode_and_other_file_types_and_has_nothing_for_no_python(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "m.py").write_text("x = 1\n")
    before = tree_sha256(pkg)

    (pkg / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\0")
    (pkg / "notes.txt").write_text("hi")

    assert tree_sha256(pkg) == before
    empty = tmp_path / "empty"
    empty.mkdir()
    assert tree_sha256(empty) is None
    assert tree_sha256(tmp_path / "missing") is None
