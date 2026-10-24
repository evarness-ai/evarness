"""``evarness skills`` -- the skill packs this install discovered, and whether each loaded.

The REPL's ``/skills`` is the interactive view; this is the scriptable one. Every pack is a
row with where it came from (``config/skills`` or ``plugin:<name>``), what it is still
missing, and how many tools it registered, so a check can assert a pack *loaded*, not
only that it was found: a pack with a missing prerequisite is listed too (ADR-0136).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from evarness.cli.render import console
from evarness.foundation.paths import config_root

skills_app = typer.Typer(
    name="skills",
    help="The skill packs this install discovered: list.",
    no_args_is_help=True,
)


def skill_rows(repo_root: Path) -> list[dict[str, Any]]:
    """One row per discovered pack, loaded or not, sorted by source then name."""
    from evarness.tools.skills.registry import SkillRegistry

    registry = SkillRegistry(repo_root=repo_root)
    rows: list[dict[str, Any]] = []
    for package in registry.discover():
        rows.append(
            {
                "name": package.manifest.name,
                "source": registry.source_of(package.skill_dir),
                "kind": package.manifest.kind,
                "loadable": package.is_loadable,
                "missing_prerequisites": list(package.missing_prerequisites),
                "tool_count": len(package.tool_classes),
                "location": str(package.skill_dir),
            }
        )
    for skill_dir, reason in registry.load_failures.items():
        rows.append(
            {
                "name": skill_dir.name,
                "source": registry.source_of(skill_dir),
                "kind": "unknown",
                "loadable": False,
                "missing_prerequisites": [],
                "tool_count": 0,
                "failed": reason,
                "location": str(skill_dir),
            }
        )
    return sorted(rows, key=lambda row: (row["source"], row["name"]))


@skills_app.command("list")
def cmd_list(
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the rows as JSON, for a script to assert on.")
    ] = False,
) -> None:
    """List every discovered pack: source, what it is missing, how many tools it registered."""
    rows = skill_rows(config_root())
    if as_json:
        console.print_json(json.dumps(rows))
        return
    table = Table(show_header=True, header_style="bold")
    for column in ("name", "source", "status", "tools"):
        table.add_column(column)
    for row in rows:
        if "failed" in row:
            status = f"failed: {row['failed']}"
        elif row["missing_prerequisites"]:
            status = "blocked: " + ", ".join(row["missing_prerequisites"])
        else:
            status = "loadable" if row["loadable"] else "no tools"
        table.add_row(row["name"], row["source"], status, str(row["tool_count"]))
    console.print(table)
