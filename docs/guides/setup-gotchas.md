# Common setup gotchas

The snags people hit between installing Evarness and a working inbox assistant, each with its
cause and the fix. Run `evarness doctor` first: it names most of these and prints the fix for
each one.

## Installing

**`evarness: command not found` after the install.** `uv` and `pipx` put `evarness` in
`~/.local/bin`, which a fresh machine often does not have on its PATH. Run
`uv tool update-shell` (or `pipx ensurepath`) and open a new terminal; the change applies
only to shells started afterwards. See [Install](../getting-started/install.md#put-evarness-on-your-path).

**`uv` or `pipx` is not installed.** Neither ships with Python. If you have `pip`,
`pip install uv` is the shortest route.

**`evarness` prints `usage: evarness [-h] {validate,run,render,prove,verify,export,patterns}`.**
That is not the harness. The verifier shipped as `evarness` 0.1.0a1 on PyPI in July 2026,
before the harness took the name (it is `evarness-verify` now), and a machine that
installed it then keeps it: a later `uv tool install "evarness[email]"` answers
"already installed" and leaves it in place. Run
`uv tool install "evarness[email]" --upgrade` (or `pipx install --force "evarness[email]"`);
`evarness --version` then says `Evarness 0.2.x`.

**A Python version mismatch in a clone.** Evarness needs Python 3.12 or 3.13. To try
Evarness, do not clone: `uv tool install "evarness[email]" --upgrade` fetches a supported
Python itself. In a clone, let one tool make the environment: `uv sync` (it reads
`.python-version` and fetches 3.12 if needed) or `./install.sh`, which finds a 3.12 or
3.13 on the machine and points Poetry at it. A `venv` made by hand on Homebrew's newest
Python, then handed to `uv` or Poetry, is what "mismatch" errors describe: delete it and
let the tool create its own.

**The wrong Python.** A plain `uv tool install` fetches
one for you, so it works whatever Python the machine has. If it stops with
`No solution found ... evarness depends on Python>=3.12,<3.14`, uv is pinned to another
Python: a `--python` flag, the `UV_PYTHON` variable, or a `python` setting in your uv
config. Pass a supported one: `uv tool install --python 3.13 "evarness[email]"`, or
`pipx install --python python3.13 "evarness[email]"` (pipx uses the Python it runs
under, so it fails on 3.11 until you pass one). On 3.14 or later from a clone, Poetry says
"The currently activated Python version is not supported by the project" and looks for a
compatible interpreter; install 3.12 or 3.13 first (`.python-version` pins 3.12).

**`uv sync` installed nothing.** On 0.2.2 and earlier the project declared itself only
under `[tool.poetry]`, so in a clone uv found no project, resolved an empty workspace and
built an empty environment. Since 0.2.3 the project is declared under `[project]` as well:
`uv sync` installs the package and the `evarness` command into `.venv`
(`uv run evarness doctor`). The development tools, the gate's ruff, black, mypy and
pytest, are Poetry's dev group: to work on the code, use
`poetry install --with dev --extras email`.

**Windows.** Run Evarness with Docker Desktop, or inside WSL2. Native Windows is for
development: `evarness doctor` warns on the platform row but does not block. See
[Windows](windows.md).

## Reading `evarness doctor`

**"Ready for the demo only." and exit code 1 is normal right after installing.** The demo
runs on a scripted model, so it needs none of what real use needs: Ollama, the starter
models, a vault master key and 8 GB of RAM (16 GB for the standard model set).
Only "Not ready." (exit 2) means the machine itself must change first.

**The vault master key is missing.** Every governed tool call needs it, so without it Evarness
refuses them. `evarness doctor --fix` creates one, and never overwrites a stored key. On macOS
it goes in the Keychain. On Linux with no keyring (WSL2, a headless server) it prints an
`export EVARNESS_VAULT_MASTER_KEY=...` line: put it in your shell profile and keep a copy,
because the vault cannot be read without the key.

**The email judge's model is missing.** The "Classify" step of `evarness email setup` stops
with `the email_judge model is unavailable (model 'qwen3.5:4b-q4_K_M' is not pulled: run
'ollama pull ...')`. Run `evarness doctor --fix` and `evarness email setup` again: the judge's
model is in every model set's starters, so `--fix` pulls it (before 0.1.6 it pulled only
the general starter model, and `ollama pull qwen3.5:4b-q4_K_M` was the fix). The demo is
unaffected.

**"Model set" says `lite` on a 16 GB machine, or `standard` on an 8 GB one.** Doctor sizes
by total RAM in whole GiB; a 16 GB Linux box that reports 15.6 GiB counts as 16. If it
chose wrong for you, `evarness doctor --model-set <name> --fix` forces a set; the choice is
saved, and a later plain `evarness doctor` warns when the set it would pick differs from the
one applied, with the command to keep or move.

**Setup on 0.1.0 to 0.1.2 marked every email Unsure.** Those releases did not stop at a
missing judge model. Ollama answered `404 model not found` for each email, setup printed a
traceback for each, saved every email as an Unsure verdict that carried the 404, and still
asked to change the mailbox. Nothing was written to the mailbox, and answering no at that
prompt keeps it that way. Pull the model, upgrade with
`uv tool install --refresh --force "evarness[email]"` (0.1.3 or later), then run
`evarness email setup --restart`. It judges those emails again. `evarness email judge
--retry-failed` does the same outside setup, for every email whose last verdict was an
error.

## Connecting a mailbox

**`evarness email setup` says "No mailbox is connected yet".** Setup never asks for a password
and runs no login itself; it takes an account that is already connected. Connect one first
with `evarness auth imap login --user you@example.com` (an app password) or
`evarness auth gmail login --user you@gmail.com`, then run `evarness email setup` again. To see the
flow without an account, run `evarness email demo`.

**Outlook.com and Microsoft 365 cannot be connected.** Evarness logs in to IMAP with an app
password and does not do OAuth over IMAP, and both require OAuth. Provider notes, with the
server and port for each, are in
[Connect your mailbox](../getting-started/connect-your-mailbox.md#provider-notes).

**There is no app-password option for Gmail.** App passwords need 2-Step Verification, and
are not offered for work or school accounts, Advanced Protection, or security-key-only
2SV. Use Gmail with OAuth instead, which takes a one-time Google Cloud client of your own:
[Gmail: the OAuth client](../getting-started/gmail-oauth-client.md).

**`evarness auth gmail login` says `OAuth client secrets not found at ...`.** The one-time
Google Cloud client has not been created, or its JSON is not at the path the message
prints (`workspace/credentials/google_oauth_client.json` under your Evarness home).
[Gmail: the OAuth client](../getting-started/gmail-oauth-client.md) has the steps;
`--client-secrets PATH` points the login at a file kept elsewhere.

**Setup stopped and exited 3.** That means it is waiting, for your decision or for
something outside setup, not that it failed. Run `evarness email setup` again and it picks up
where it stopped. Exit 2 is bad input.

**Nothing changed in my mailbox.** That is by design: every write is refused until you
approve it once per account. `evarness email writes status` shows where you stand and
`evarness email writes approve --account <id>` gives the approval.

## Settings and the `.env` file

**A setting in `.env` is ignored, or `evarness setup` wrote one I cannot find.** Every
`evarness` command reads the Evarness home's `.env` (`~/.evarness/.env` unless
`EVARNESS_HOME` points elsewhere), and in a source checkout the checkout's `.env` first;
a variable the shell exports wins over both. An installed `evarness` does not read a
`.env` in the directory you happen to run it from. `evarness setup` writes to the file
that is read, and its closing lines name the home, the data directory and that file. See
[Where Evarness keeps its files](../getting-started/install.md#where-evarness-keeps-its-files).

**Where is the data, and how do I move it?** Under the home (`data/`), or where
`EVARNESS_DATA_DIR` points. `evarness setup` asks; `evarness setup --reset` asks again.
Moving the home or the data after setup means moving the directory and changing the
variable; nothing else records the path.

## Running the services

**Every request is refused.** The Evarness API, the Governor and the channel gateway refuse
every request, rather than run open, when `EVARNESS_AUTH_SECRET` is unset or blank. Set it
before `evarness serve`, and use the same value in every shell that talks to that API
(`evarness device pair` and `evarness status` included). A fresh `openssl rand` in a second shell
is a different secret.

**The web console is blank from a clone.** A bare `git clone` and `poetry install` has no
console: `evarness serve` answers `/chat`, but `http://localhost:8003` shows nothing until you
build it. See [the email assistant guide](email.md#run-the-evarness-api).

**A "Governor not reachable" banner.** Expected if you started only `evarness serve`. The
Governor is an optional, separate front for the governance kernel that `evarness serve`
already runs in-process.

## Working on Evarness

**Tests fail at import or write into your real profile.** The suite needs
`EVARNESS_AUTH_SECRET` and `EVARNESS_DISABLE_WARMUP=1` set, and relocates `EVARNESS_HOME` before
anything imports. User state resolves `EVARNESS_HOME` through `evarness.foundation.paths`,
never `Path.home()`.

```bash
export EVARNESS_AUTH_SECRET="test-secret-for-testing" EVARNESS_DISABLE_WARMUP=1
poetry run pytest tests/unit/<the path you changed>
```

**The demo does nothing the second time.** It remembers: run it again and nothing is
fetched or judged twice. `evarness email demo --reset` deletes the demo home and starts over.
The demo ignores your own `EVARNESS_*` settings on purpose, and never touches your profile.
