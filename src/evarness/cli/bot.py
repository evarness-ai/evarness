"""``evarness bot``: list, show, register and switch the bots of this home (ADR-0138).

On the store directly, the way ``evarness approvals`` is, not through the API the way
``evarness device`` is: the owner must be able to disable or revoke a bot from the host's
shell with no API running. Every change goes through ``BotService``, so it leaves the same
audit row the API and the SDK leave. A read, or a change refused (an unknown bot), never
creates ``bots.db``. In a bot's own process (``EVARNESS_BOT`` set) every change is refused.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

if TYPE_CHECKING:
    from evarness.kernel.governance.bots import BotIdentity, BotService

console = Console()
logger = logging.getLogger(__name__)

bot_app = typer.Typer(
    name="bot",
    help="List, register and switch the bots of this Evarness home.",
    no_args_is_help=True,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Print the raw JSON")]


def _actor() -> str:
    return f"cli:{os.environ.get('USER', 'unknown')}"


def _service() -> BotService:
    from evarness.kernel.governance.bots import BotService as _BotService

    return _BotService()


def _rows() -> list[BotIdentity]:
    return _service().list()


def _get(name: str) -> BotIdentity | None:
    return _service().get(name)


def _as_dict(row: BotIdentity) -> dict[str, str | None]:
    return row.to_dict()


def _fail(message: str) -> typer.Exit:
    console.print(f"[bold red]Error:[/bold red] {escape(message)}")
    return typer.Exit(1)


def _print_row(row: BotIdentity) -> None:
    for key, value in _as_dict(row).items():
        console.print(f"[bold]{key}[/bold]: {escape(str(value) if value is not None else '-')}")


@bot_app.command("list")
def cmd_list(as_json: JsonOption = False) -> None:
    """Every bot of this home, revoked ones included."""
    rows = _rows()
    logger.info("bot.cli.list count=%d", len(rows))
    if as_json:
        typer.echo(json.dumps({"bots": [_as_dict(r) for r in rows]}, indent=2))
        return
    if not rows:
        console.print("[dim]No bots registered in this home.[/dim]")
        return
    table = Table(title="Bots", show_lines=False)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Bot ID", style="dim", no_wrap=True)
    table.add_column("Workspace", max_width=48)
    table.add_column("Updated", style="dim")
    for r in rows:
        table.add_row(r.name, r.status, r.bot_id, r.workspace_path, r.updated_at[:19])
    console.print(table)


@bot_app.command("show")
def cmd_show(
    name: Annotated[str, typer.Argument(help="The bot's name")],
    as_json: JsonOption = False,
) -> None:
    """One bot by name."""
    row = _get(name)
    logger.info("bot.cli.show name=%r found=%s", name, row is not None)
    if row is None:
        raise _fail(f"no bot named {name!r}")
    if as_json:
        typer.echo(json.dumps({"bot": _as_dict(row)}, indent=2))
        return
    _print_row(row)


@bot_app.command("register")
def cmd_register(
    name: Annotated[str, typer.Argument(help="The bot's name, as its definition names it")],
    definition_sha256: Annotated[
        str,
        typer.Option("--definition-sha256", help="SHA-256 of the validated definition"),
    ],
    workspace: Annotated[
        str | None,
        typer.Option(
            "--workspace", help="The bot's folder; must be <home>/bots/<name> (the default)"
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Register a bot (status active), or record a new definition hash for it."""
    from evarness.kernel.governance.bots import BotRevokedError, bot_workspace

    actor = _actor()
    try:
        path = workspace if workspace is not None else str(bot_workspace(name))
        row = _service().register(name, path, definition_sha256, actor=actor)
    except (BotRevokedError, PermissionError) as exc:
        raise _fail(str(exc)) from exc
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    logger.info("bot.cli.register name=%s bot_id=%s actor=%s", row.name, row.bot_id, actor)
    if as_json:
        typer.echo(json.dumps({"bot": _as_dict(row)}, indent=2))
        return
    console.print(f"[green]Registered[/green] {escape(row.name)} ({row.bot_id}), {row.status}")


@bot_app.command("status")
def cmd_status(
    name: Annotated[str, typer.Argument(help="The bot's name")],
    status: Annotated[str, typer.Argument(help="active, disabled or revoked")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm a revoke (it cannot be undone)")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Switch a bot: ``active`` and ``disabled`` both ways, ``revoked`` for good."""
    from evarness.kernel.governance.bots import BotNotFoundError, BotRevokedError

    if status == "revoked" and not yes:
        raise _fail("revoked is terminal; pass --yes to revoke")
    actor = _actor()
    try:
        row = _service().set_status(name, status, actor=actor)
    except BotNotFoundError as exc:
        raise _fail(f"no bot named {name!r}") from exc
    except (BotRevokedError, PermissionError) as exc:
        raise _fail(str(exc)) from exc
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    logger.info(
        "bot.cli.status name=%s bot_id=%s status=%s actor=%s",
        row.name,
        row.bot_id,
        row.status,
        actor,
    )
    if as_json:
        typer.echo(json.dumps({"bot": _as_dict(row)}, indent=2))
        return
    console.print(f"{escape(row.name)} is now [bold]{row.status}[/bold]")
