# Common setup gotchas

The snags people hit between installing IRIS and a working inbox assistant, each with its
cause and the fix. Run `iris doctor` first: it names most of these and prints the fix for
each one.

## Installing

**`iris: command not found` after the install.** `uv` and `pipx` put `iris` in
`~/.local/bin`, which a fresh machine often does not have on its PATH. Run
`uv tool update-shell` (or `pipx ensurepath`) and open a new terminal; the change applies
only to shells started afterwards. See [Install](../getting-started/install.md#put-iris-on-your-path).

**`uv` or `pipx` is not installed.** Neither ships with Python. If you have `pip`,
`pip install uv` is the shortest route.

**The wrong Python.** IRIS needs Python 3.12 or 3.13. A plain `uv tool install` fetches
one for you, so it works whatever Python the machine has. If it stops with
`No solution found ... iris-harness depends on Python>=3.12,<3.14`, uv is pinned to another
Python: a `--python` flag, the `UV_PYTHON` variable, or a `python` setting in your uv
config. Pass a supported one: `uv tool install --python 3.13 "iris-harness[email]"`, or
`pipx install --python python3.13 "iris-harness[email]"` (pipx uses the Python it runs
under, so it fails on 3.11 until you pass one). On 3.14 or later from a clone, Poetry says
"The currently activated Python version is not supported by the project" and looks for a
compatible interpreter; install 3.12 or 3.13 first (`.python-version` pins 3.12).

**`uv sync` installed nothing.** `uv sync` is for projects that declare themselves under
`[project]`. IRIS declares itself under `[tool.poetry]`, so in a clone uv finds no
dependencies and no `requires-python`, guesses `>=3.13` and builds an empty environment.
To run IRIS, install the package: `uv tool install "iris-harness[email]"`. To work on the
code, use `poetry install --with dev --extras email`.

**Windows.** Run IRIS with Docker Desktop, or inside WSL2. Native Windows is for
development: `iris doctor` warns on the platform row but does not block. See
[Windows](windows.md).

## Reading `iris doctor`

**"Ready for the demo only." and exit code 1 is normal right after installing.** The demo
runs on a scripted model, so it needs none of what real use needs: Ollama, the starter
models, a vault master key and 8 GB of RAM (16 GB for the standard model set).
Only "Not ready." (exit 2) means the machine itself must change first.

**The vault master key is missing.** Every governed tool call needs it, so without it IRIS
refuses them. `iris doctor --fix` creates one, and never overwrites a stored key. On macOS
it goes in the Keychain. On Linux with no keyring (WSL2, a headless server) it prints an
`export IRIS_VAULT_MASTER_KEY=...` line: put it in your shell profile and keep a copy,
because the vault cannot be read without the key.

**`--fix` did not pull the email judge's model.** It pulls only the general starter model.
The email judge is pinned to a smaller one that `iris doctor` does not check. Without it,
the "Classify" step of `iris email setup` stops with
`the email_judge model is unavailable (model 'qwen3.5:4b-q4_K_M' is not pulled: run
'ollama pull ...')`. Run `ollama pull qwen3.5:4b-q4_K_M` and run `iris email setup` again.
The demo is unaffected.

**Setup on 0.1.0 to 0.1.2 marked every email Unsure.** Those releases did not stop at a
missing judge model. Ollama answered `404 model not found` for each email, setup printed a
traceback for each, saved every email as an Unsure verdict that carried the 404, and still
asked to change the mailbox. Nothing was written to the mailbox, and answering no at that
prompt keeps it that way. Pull the model, upgrade with
`uv tool install --refresh --force "iris-harness[email]"` (0.1.3 or later), then run
`iris email setup --restart`. It judges those emails again. `iris email judge
--retry-failed` does the same outside setup, for every email whose last verdict was an
error.

## Connecting a mailbox

**`iris email setup` says "No mailbox is connected yet".** Setup never asks for a password
and runs no login itself; it takes an account that is already connected. Connect one first
with `iris auth imap login --user you@example.com` (an app password) or
`iris auth gmail login --user you@gmail.com`, then run `iris email setup` again. To see the
flow without an account, run `iris email demo`.

**Outlook.com and Microsoft 365 cannot be connected.** IRIS logs in to IMAP with an app
password and does not do OAuth over IMAP, and both require OAuth. Provider notes, with the
server and port for each, are in
[Connect your mailbox](../getting-started/connect-your-mailbox.md#provider-notes).

**There is no app-password option for Gmail.** App passwords need 2-Step Verification, and
are not offered for work or school accounts, Advanced Protection, or security-key-only
2SV. Use Gmail with OAuth instead, which takes a one-time Google Cloud client of your own.

**Setup stopped and exited 3.** That means it is waiting, for your decision or for
something outside setup, not that it failed. Run `iris email setup` again and it picks up
where it stopped. Exit 2 is bad input.

**Nothing changed in my mailbox.** That is by design: every write is refused until you
approve it once per account. `iris email writes status` shows where you stand and
`iris email writes approve --account <id>` gives the approval.

## Running the services

**Every request is refused.** The IRIS API, the Governor and the channel gateway refuse
every request, rather than run open, when `IRIS_AUTH_SECRET` is unset or blank. Set it
before `iris serve`, and use the same value in every shell that talks to that API
(`iris device pair` and `iris status` included). A fresh `openssl rand` in a second shell
is a different secret.

**The web console is blank from a clone.** A bare `git clone` and `poetry install` has no
console: `iris serve` answers `/chat`, but `http://localhost:8003` shows nothing until you
build it. See [the email assistant guide](email.md#run-the-iris-api).

**A "Governor not reachable" banner.** Expected if you started only `iris serve`. The
Governor is an optional, separate front for the governance kernel that `iris serve`
already runs in-process.

## Working on IRIS

**Tests fail at import or write into your real profile.** The suite needs
`IRIS_AUTH_SECRET` and `IRIS_DISABLE_WARMUP=1` set, and relocates `IRIS_HOME` before
anything imports. User state resolves `IRIS_HOME` through `iris_harness.foundation.paths`,
never `Path.home()`.

```bash
export IRIS_AUTH_SECRET="test-secret-for-testing" IRIS_DISABLE_WARMUP=1
poetry run pytest tests/unit/<the path you changed>
```

**The demo does nothing the second time.** It remembers: run it again and nothing is
fetched or judged twice. `iris email demo --reset` deletes the demo home and starts over.
The demo ignores your own `IRIS_*` settings on purpose, and never touches your profile.
