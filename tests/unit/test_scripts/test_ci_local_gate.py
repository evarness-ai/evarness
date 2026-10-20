"""What scripts/ci_local.sh promises about the developer's own profile."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (ROOT / "scripts" / "ci_local.sh").read_text(encoding="utf-8")


def test_the_gates_playground_smoke_runs_in_a_scratch_home() -> None:
    """The playground run goes through the real runtime and writes session logs wherever
    EVARNESS_HOME points. Run with the developer's environment it filled ~/.evarness/logs
    with playground sessions, which then failed the owner's proof bundle (#363). The gate
    gives it a scratch home and removes it afterwards."""
    body = SCRIPT.split("run_playground_smoke() {", 1)[1].split("\n}\n", 1)[0]
    assert "mktemp -d" in body
    assert 'EVARNESS_HOME="$scratch/home"' in body
    assert 'EVARNESS_DATA_DIR="$scratch/home/data"' in body
    assert "evarness playground run core-deterministic" in body
    assert 'rm -rf "$scratch"' in body
    # The bare command appears once, inside the function; both steps call the function.
    bare = [
        line
        for line in SCRIPT.splitlines()
        if "evarness playground run" in line and not line.lstrip().startswith("#")
    ]
    assert len(bare) == 1 and bare[0].strip() in body
    assert SCRIPT.count("run_playground_smoke") == 3  # the definition and the two steps
