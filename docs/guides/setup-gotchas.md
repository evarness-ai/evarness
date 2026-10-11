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

**The wrong Python.** IRIS needs Python 3.12 or 3.13. On 3.14 or later, Poetry says
"The currently activated Python version is not supported by the project" and looks for a
compatible interpreter; `uv tool install` fetches one for you. From a clone, install 3.12
or 3.13 first (`.python-version` pins 3.12).

**Windows.** Native Windows is not supported; `iris doctor` reports "Not ready". Use
WSL2.

## Reading `iris doctor`

**"Ready for the demo only." and exit code 1 is normal right after installing.** The demo
runs on a scripted model, so it needs none of what real use needs: Ollama, the starter
model, a vault master key and 16 GB of RAM. Only "Not ready." (exit 2) means the machine
itself must change first.

**The vault master key is missing.** Every governed tool call needs it, so without it IRIS
refuses them. `iris doctor --fix` creates one, and never overwrites a stored key. On macOS
it goes in the Keychain. On Linux with no keyring (WSL2, a headless server) it prints an
`export IRIS_VAULT_MASTER_KEY=...` line: put it in your shell profile and keep a copy,
because the vault cannot be read without the key.

**`--fix` did not pull the email judge's model.** It pulls only the general starter model.
The email judge is pinned to a smaller one that `iris doctor` does not check. Without it,
the "Classify" step of `iris email setup` stops with "no local email_judge model tier is
configured". Run `ollama pull qwen3.5:4b-q4_K_M`. The demo is unaffected.

## Connecting a mailbox

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
