"""``evarness setup`` -- the progressive first-run wizard.

A thin orchestrator, not a new framework: each step calls the module that already
owns that behavior (``evarness doctor``'s ``run_doctor``/``apply_fixes``, the existing
``scripts/start_evarness.sh`` for services, ``evarness email setup`` as a subprocess for
email — core may not import ``evarness_personal`` directly, see ``src/CLAUDE.md``).
Progress is recorded in ``$EVARNESS_HOME/setup.json`` (``services/system/setup_state.py``)
so re-running ``evarness setup`` resumes at the first step with no record; ``--reset``
clears it, ``--status`` reports it without running anything.

Steps run in a fixed order: two mandatory (preflight; home & secret, which asks where
the home and the data go and writes the answers to the settings ``.env``), then three
optional, skippable ones (services, Telegram pairing, email) — each offered with
its own yes/skip prompt, never a menu. Services is optional because it isn't
actually needed for core CLI use: `evarness` (the REPL)/`evarness email setup`/`evarness doctor` all
call `build_runtime()` in-process (no HTTP hop); the four servers only matter for
the web UI, the REST API, or Telegram's poller. The closing screen also marks the
first-chat welcome (``runtime/welcome.py``) as already shown, so the user doesn't
see two different "you're all set" messages back to back.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from evarness.cli.render import console
from evarness.foundation.docs_site import docs_url
from evarness.services.system.telegram_pairing import (
    get_telegram_bot_username,
    poll_telegram_chat_id,
)

if TYPE_CHECKING:
    from evarness.services.system.setup_state import SetupState, StepStatus

_STEP_LABELS: dict[str, str] = {
    "preflight": "Preflight",
    "home_secret": "Home & secret",
    "services": "Services",
    "telegram": "Telegram",
    "email": "Email",
}

# Where to generate an IMAP app password, by address domain -- presentational only,
# a deliberately small local copy rather than an import: core may not import
# evarness_personal (the imap plugin's own HOST_PRESETS lives there). Matches the
# provider table in docs/getting-started/connect-your-mailbox.md; keep both in sync.
_IMAP_APP_PASSWORD_HINTS: dict[str, str] = {
    "gmail.com": "myaccount.google.com (needs 2-Step Verification on first)",
    "googlemail.com": "myaccount.google.com (needs 2-Step Verification on first)",
    "icloud.com": "appleid.apple.com -> Sign-In and Security -> App-Specific Passwords",
    "me.com": "appleid.apple.com -> Sign-In and Security -> App-Specific Passwords",
    "mac.com": "appleid.apple.com -> Sign-In and Security -> App-Specific Passwords",
    "yahoo.com": "Yahoo Account Security -> App passwords",
    "fastmail.com": "Fastmail Settings -> Privacy & Security -> App passwords",
}


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _env_path() -> Path:
    """The ``.env`` file ``evarness setup`` writes to: the one every ``evarness`` command
    reads back (``foundation/env_files.py``): the checkout's in a checkout, else the
    Evarness home's. Used to be whatever ``.env`` sat in the current directory, which an
    installed ``evarness`` run from anywhere else never read."""
    from evarness.foundation.env_files import settings_env_path

    return settings_env_path()


def _evarness_argv() -> list[str]:
    """How to invoke ``evarness`` as a child process: the current interpreter with
    ``-m``, not the installed console script. ``sys.executable`` is guaranteed
    executable -- we're running on it right now -- where a venv's ``bin/evarness``
    shim is a separate file whose own exec permission can be blocked by the OS
    (seen in practice: macOS's ``com.apple.provenance`` attribute denying exec
    on an otherwise `rwxr-xr-x` script) for reasons entirely outside our control.
    """
    return [sys.executable, "-m", "evarness.main"]


def _step_preflight(*, interactive: bool) -> bool:
    """Reuses ``evarness doctor`` verbatim: its report, its verdict, its ``--fix``."""
    from evarness.cli.doctor import apply_fixes, render_report
    from evarness.services.system import doctor as dr

    report = dr.run_doctor(read_keyring=interactive)
    render_report(report)
    if report.fixable and interactive:
        console.print()
        if typer.confirm("Fix automatically?", default=True):
            apply_fixes(report, confirm=lambda q: typer.confirm(q, default=True))
            report = dr.run_doctor(read_keyring=True)
            console.print()
            render_report(report)
    return bool(report.verdict is not dr.Verdict.NOT_READY)


def _ensure_auth_secret() -> None:
    """Generate ``EVARNESS_AUTH_SECRET`` and write it to ``.env`` if nothing set it.

    Every Evarness HTTP service fails closed without this secret (``foundation/auth.py``);
    nothing has generated one before now -- ``scripts/start_evarness.sh`` only checks for
    it and errors out. Written to the gitignored ``.env`` and printed once, since
    there is no other surface that shows it back to the owner later.
    """
    from dotenv import set_key

    if os.environ.get("EVARNESS_AUTH_SECRET", "").strip():
        console.print("  [green]EVARNESS_AUTH_SECRET already set.[/green]")
        return
    secret = secrets.token_urlsafe(32)
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    set_key(str(env_path), "EVARNESS_AUTH_SECRET", secret)
    os.environ["EVARNESS_AUTH_SECRET"] = secret
    console.print(f"  [green]generated EVARNESS_AUTH_SECRET, wrote it to {env_path}[/green]")
    console.print("  [yellow]shown once -- save it to reach the web UI or any API client:[/yellow]")
    console.print(f"    {secret}")


def _usable_directory(path: Path) -> str | None:
    """Create ``path`` if needed and say why it cannot be used, or ``None`` when it can."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"cannot create {path}: {exc.strerror or exc}"
    if not os.access(path, os.W_OK):
        return f"{path} is not writable"
    return None


def _write_setting(name: str, value: str, env_path: Path) -> None:
    """Persist one setting where every later ``evarness`` command reads it, and apply it
    to this process so the steps that follow see it."""
    from dotenv import set_key

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    set_key(str(env_path), name, value)
    os.environ[name] = value
    console.print(f"  [green]wrote {name}={value} to {env_path}[/green]")


def _choose_home(*, interactive: bool) -> None:
    """Ask where the Evarness home goes, and make the answer stick.

    The home is where everything Evarness stores lives: the workspace (identity files,
    credentials), the data (mail stores, indexes), the audit ledger, the logs. A shell
    that exports ``EVARNESS_HOME`` has decided already; otherwise the owner is asked
    once, with the current resolution as the default. A different answer is written to
    the settings file read *before* the change (the default home's ``.env``, or the
    checkout's), which is how the next ``evarness`` command finds the chosen home again
    without a shell profile edit; the ``export`` line is printed too for shells that
    should see it directly.
    """
    from evarness.foundation.env_files import HOME_ENV, home_is_from_shell
    from evarness.foundation.paths import evarness_home

    current = evarness_home()
    if home_is_from_shell():
        console.print(f"  {HOME_ENV} is set by your shell: {current}")
        return
    if not interactive:
        console.print(f"  Evarness home: {current}")
        return
    console.print(
        "  Evarness keeps everything it stores under one directory, its home: the\n"
        "  workspace (identity files, credentials), the data (mail stores, indexes), the\n"
        "  audit ledger and the logs."
    )
    while True:
        answer = typer.prompt("  Where should the Evarness home be", default=str(current)).strip()
        chosen = Path(answer).expanduser()
        if not chosen.is_absolute():
            chosen = Path.cwd() / chosen
        chosen = chosen.resolve()
        if chosen == current.expanduser().resolve():
            console.print(f"  Evarness home: {current}")
            return
        problem = _usable_directory(chosen)
        if problem is None:
            break
        console.print(f"  [yellow]{problem}[/yellow]")
    pointer = _env_path()  # resolved before the change: where the next run looks first
    _write_setting(HOME_ENV, str(chosen), pointer)
    console.print(
        "  [dim]Every evarness command reads that file. For a shell that should see the\n"
        f"  home directly, add this line to your shell profile:[/dim]\n"
        f"    export {HOME_ENV}={chosen}"
    )


def _choose_data_dir(*, interactive: bool) -> None:
    """Where the data goes: under the home unless ``EVARNESS_DATA_DIR`` says otherwise.
    Offered, not asked for: the default is right for nearly everyone, and a bigger
    disk is the one reason to move only the data."""
    from evarness.foundation.paths import DATA_DIR_ENV, data_dir

    current = data_dir()
    if os.environ.get(DATA_DIR_ENV):
        console.print(f"  data ({DATA_DIR_ENV}): {current}")
        return
    console.print(f"  data (mail stores, indexes, the memory graph): {current}")
    if not interactive:
        return
    if not typer.confirm(
        "  Keep the data somewhere else (a bigger disk, for example)?", default=False
    ):
        return
    while True:
        answer = typer.prompt("  Data directory", default=str(current)).strip()
        chosen = Path(answer).expanduser()
        if not chosen.is_absolute():
            chosen = Path.cwd() / chosen
        chosen = chosen.resolve()
        if chosen == current.expanduser().resolve():
            return
        problem = _usable_directory(chosen)
        if problem is None:
            break
        console.print(f"  [yellow]{problem}[/yellow]")
    _write_setting(DATA_DIR_ENV, str(chosen), _env_path())


def _step_home_secret(*, interactive: bool = False) -> None:
    from evarness.foundation.paths import evarness_home

    _choose_home(interactive=interactive)
    _choose_data_dir(interactive=interactive)
    _ensure_auth_secret()
    console.print(
        f"  [dim]settings file: {_env_path()} (read by every evarness command; a variable\n"
        "  your shell exports wins over it). The workspace, data and identity files are\n"
        f"  created under {evarness_home()} on first use.[/dim]"
    )


def _step_services(*, interactive: bool) -> tuple[StepStatus, str]:
    """Optional: only the web UI, the REST API, or Telegram need these running.

    Wraps ``scripts/start_evarness.sh --services-only`` rather than re-implementing
    process management -- it already daemonizes, health-polls and is idempotent
    against already-running services. Dev-only script: a packaged (``uv tool`` /
    ``pipx``) install has no ``scripts/`` at all, so this is skipped, not failed,
    when it's missing.
    """
    if not interactive:
        return "skipped", "not a terminal"
    if not typer.confirm(
        "Start the background services now? (needed for the web UI, the API, "
        "or Telegram -- not for `evarness` (the REPL)/`evarness email setup`)",
        default=False,
    ):
        return "skipped", "declined"
    script = Path.cwd() / "scripts" / "start_evarness.sh"
    if not script.exists():
        console.print(
            "  [yellow]scripts/start_evarness.sh not found -- this is a packaged install, "
            "or you're not in the repo checkout.[/yellow]"
        )
        return "skipped", "start_evarness.sh not found"
    console.print("  starting the Evarness stack in the background...")
    try:
        result = subprocess.run([str(script), "--services-only"])  # noqa: S603 -- fixed path
    except OSError as exc:
        console.print(f"  [yellow]couldn't start {script.name}: {exc}[/yellow]")
        return "failed", f"couldn't start start_evarness.sh: {exc}"
    if result.returncode == 0:
        return "done", "services started"
    return "failed", f"start_evarness.sh exited {result.returncode}"


def _step_telegram(*, interactive: bool) -> tuple[StepStatus, str]:
    from dotenv import set_key

    if not interactive:
        return "skipped", "not a terminal"
    if not typer.confirm(
        "Pair Telegram so you can chat with Evarness from your phone?", default=False
    ):
        return "skipped", "declined"
    token = typer.prompt("Bot token (from @BotFather)", default="", show_default=False).strip()
    if not token:
        return "skipped", "no token entered"
    username = get_telegram_bot_username(token)
    if username:
        console.print(
            f"  open this link and tap Start: [cyan]https://t.me/{username}?start=setup[/cyan]"
        )
    else:
        console.print(
            "  [yellow]couldn't look up the bot's username -- open Telegram and message "
            "your bot directly.[/yellow]"
        )
    console.print("  waiting up to 60s for your message...")
    chat_id = poll_telegram_chat_id(token)
    if chat_id is None:
        console.print(
            "  [yellow]no message received -- re-run `evarness setup` to try again.[/yellow]"
        )
        return "failed", "no message received"
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    set_key(str(env_path), "TELEGRAM_BOT_TOKEN", token)
    set_key(str(env_path), "TELEGRAM_ALLOWED_CHAT_IDS", chat_id)
    console.print(f"  [green]paired -- chat_id={chat_id}[/green]")
    return "done", f"chat_id={chat_id}"


def _run_evarness_subcommand(args: list[str]) -> tuple[bool, str]:
    """Run ``evarness <args>`` as a child process (never the venv's ``bin/evarness`` shim --
    see ``_evarness_argv``). Returns ``(succeeded, detail)``; never raises."""
    label = "evarness " + " ".join(args)
    try:
        result = subprocess.run([*_evarness_argv(), *args])  # noqa: S603 -- fixed argv
    except OSError as exc:
        return False, f"couldn't start {label}: {exc}"
    if result.returncode == 0:
        return True, f"{label} completed"
    return False, f"{label} exited {result.returncode}"


def _step_email(*, interactive: bool) -> tuple[StepStatus, str]:
    """``evarness email setup`` only walks through a mailbox that's already
    connected -- it exits 0 either way, printing connect-first guidance instead
    of erroring when none is. So the wizard offers the same three on-ramps that
    guidance names (demo / Gmail / IMAP) before ever calling it, rather than
    delegating blind and misreading "printed guidance and exited 0" as done.
    """
    if not interactive:
        return "skipped", "not a terminal"
    if not typer.confirm("Set up email now?", default=False):
        return "skipped", "declined"

    console.print("  Connect a mailbox:")
    console.print("    1) Demo mailbox (sample data, no real account)")
    console.print("    2) Gmail (OAuth)")
    console.print("    3) IMAP (app password)")
    choice = typer.prompt(
        "  Pick one, or press Enter to skip", default="", show_default=False
    ).strip()
    if not choice:
        return "skipped", "declined"
    if choice not in ("1", "2", "3"):
        console.print(f"  [yellow]not a valid choice: {choice!r}[/yellow]")
        return "skipped", "declined"

    if choice == "1":
        console.print("  running `evarness email demo`...")
        ok, detail = _run_evarness_subcommand(["email", "demo"])
        if not ok:
            console.print(f"  [yellow]{detail}[/yellow]")
        return ("done", detail) if ok else ("failed", detail)

    provider = "gmail" if choice == "2" else "imap"
    if provider == "gmail":
        from evarness.foundation.paths import workspace_dir

        client_secrets = workspace_dir() / "credentials" / "google_oauth_client.json"
        if not client_secrets.exists():
            console.print(
                "  [dim]Gmail needs a one-time Google Cloud OAuth client first "
                "(~10 min): a project, the Gmail API enabled, a consent screen "
                "with you as the test user, and a Desktop app client saved to:[/dim]"
            )
            console.print(f"    [dim]{client_secrets}[/dim]")
            console.print(
                f"  [dim]Full walkthrough:[/dim] [cyan]{docs_url('getting-started/gmail-oauth-client')}[/cyan]"
            )
            if not typer.confirm("  Continue anyway?", default=False):
                return "skipped", "no Google OAuth client configured yet"
    else:
        console.print(
            "  [dim]IMAP needs an app password (not your regular password) from "
            "your provider's security settings -- Gmail, iCloud, Yahoo and "
            "Fastmail all offer one; Outlook.com and Microsoft 365 don't support "
            "IMAP app passwords at all.[/dim]"
        )
        console.print(
            "  [dim]Provider-by-provider steps:[/dim] "
            f"[cyan]{docs_url('getting-started/connect-your-mailbox')}[/cyan]"
        )

    address = typer.prompt("  Email address", default="", show_default=False).strip()
    if not address:
        return "skipped", "no address entered"
    if provider == "imap":
        hint = _IMAP_APP_PASSWORD_HINTS.get(address.rsplit("@", 1)[-1].strip().lower())
        if hint:
            console.print(f"  [dim]Get an app password: {hint}[/dim]")
        else:
            console.print(
                "  [dim]Get an app password from your provider's account security "
                "settings.[/dim]"
            )
    console.print(f"  running `evarness auth {provider} login --user {address}`...")
    ok, detail = _run_evarness_subcommand(["auth", provider, "login", "--user", address])
    if not ok:
        console.print(f"  [yellow]{detail}[/yellow]")
        return "failed", f"{provider} login failed: {detail}"
    console.print("  handing off to `evarness email setup`...")
    ok, detail = _run_evarness_subcommand(["email", "setup"])
    if not ok:
        console.print(f"  [yellow]{detail}[/yellow]")
    return ("done", detail) if ok else ("failed", detail)


def _render_progress(state: SetupState) -> None:
    from evarness.services.system import setup_state

    for name in setup_state.STEP_ORDER:
        record = state.steps.get(name)
        if record is None:
            console.print(f"  [dim]○ {_STEP_LABELS[name]}[/dim]")
            continue
        if record.status == "done":
            mark = "[green]✔[/green]"
        elif record.status == "failed":
            mark = "[red]✗[/red]"
        else:
            mark = "[dim]–[/dim]"
        detail = f"  [dim]{record.detail}[/dim]" if record.detail else ""
        console.print(f"  {mark} {_STEP_LABELS[name]:<16}{detail}")


def _render_summary(state: SetupState) -> None:
    from evarness.runtime import welcome as welcome_mod

    console.print()
    console.print("[bold green]Setup complete[/bold green]")
    _render_progress(state)
    console.print()
    console.print("What's next:")
    console.print("  -> chat now:            evarness")
    webui_host = os.environ.get("EVARNESS_API_HOST", "127.0.0.1")
    webui_port = os.environ.get("EVARNESS_WEBUI_PORT", "5181")
    console.print(f"  -> open the web UI:     http://{webui_host}:{webui_port}")
    console.print("  -> re-run / add more:   evarness setup")
    console.print()
    from evarness.foundation.paths import data_dir, evarness_home
    from evarness.services.system import setup_state

    console.print(f"[dim]home {evarness_home()}, data {data_dir()}, settings {_env_path()}[/dim]")

    console.print(f"[dim]saved to {setup_state.setup_marker_path()}[/dim]")
    welcome_mod.mark_shown_by_setup()


def cmd_setup(
    status: Annotated[
        bool,
        typer.Option("--status", help="Print setup progress without running anything."),
    ] = False,
    reset: Annotated[
        bool,
        typer.Option("--reset", help="Clear saved progress so every step runs again."),
    ] = False,
) -> None:
    """Progressive first-run setup: preflight, home & secret, services, Telegram, email."""
    from evarness.services.system import setup_state

    if reset:
        setup_state.clear_state()
        console.print("[dim]setup progress cleared.[/dim]")

    state = setup_state.load_state()
    if status:
        if not state.steps:
            console.print("No setup progress recorded yet. Run `evarness setup` to begin.")
        else:
            console.print("[bold]evarness setup[/bold] progress:")
            _render_progress(state)
        raise typer.Exit(0)

    interactive = _interactive()
    total = len(setup_state.STEP_ORDER)
    for index, name in enumerate(setup_state.STEP_ORDER, start=1):
        if not state.needs_run(name):
            continue
        console.print()
        console.rule(f"step {index} of {total} - {_STEP_LABELS[name]}")

        if name == "preflight":
            if not _step_preflight(interactive=interactive):
                console.print(
                    "[bold red]Preflight is not ready[/bold red] -- fix the issues above, "
                    "then re-run `evarness setup`."
                )
                raise typer.Exit(1)
            state = setup_state.record_step(name, "done")
        elif name == "home_secret":
            _step_home_secret(interactive=interactive)
            state = setup_state.record_step(name, "done")
        elif name == "services":
            services_status, detail = _step_services(interactive=interactive)
            state = setup_state.record_step(name, services_status, detail=detail)
        elif name == "telegram":
            telegram_status, detail = _step_telegram(interactive=interactive)
            state = setup_state.record_step(name, telegram_status, detail=detail)
        elif name == "email":
            email_status, detail = _step_email(interactive=interactive)
            state = setup_state.record_step(name, email_status, detail=detail)

    _render_summary(state)


__all__ = ["cmd_setup", "get_telegram_bot_username", "poll_telegram_chat_id"]
