# Install

IRIS installs as one command-line tool, `iris`, with the email assistant included.
These three pages take you from nothing to your own inbox: install and check the
machine (this page), [try the demo](demo.md), then
[connect your mailbox](connect-your-mailbox.md). Every command on them runs in CI
on a fresh machine, on every change.

## What you need

- **macOS on Apple Silicon, or Linux** (x86_64 or arm64). On Windows, run IRIS with Docker
  Desktop, or WSL2; see [Windows](../guides/windows.md).
- **16 GB of RAM** for real use with the standard models. With 8 GB you can run the
  demo (it uses a scripted model) or run the `lite` model set, which `iris doctor` picks
  for you on such a machine: one 4B model does the chat and the email judging.
- **[uv](https://docs.astral.sh/uv/getting-started/installation/) or
  [pipx](https://pipx.pypa.io/stable/installation/)** to install the tool. Neither
  ships with Python. If you have `pip`, `pip install uv` is the shortest route.
- **Python 3.12 or 3.13.** `uv` fetches one for you if you have neither.
- **[Ollama](https://ollama.com/download)** for the local model. The demo does not
  need it, so you can install it later.

## Install the tool

With [uv](https://docs.astral.sh/uv/):

<!-- ci: install -->
```bash
uv tool install "iris-harness[email]"
```

Or with [pipx](https://pipx.pypa.io/):

<!-- ci: install -->
```bash
pipx install "iris-harness[email]"
```

The `[email]` extra adds the Gmail client libraries. Without it IRIS is still the email
assistant over IMAP. Either way you get one command, `iris`, in an isolated
environment of its own. To upgrade later, run `uv tool upgrade iris-harness` (or
`pipx upgrade iris-harness`).

### Put `iris` on your PATH

Both tools put `iris` in `~/.local/bin`, which a fresh machine often does not have on
its PATH. If the install ends with a warning that `~/.local/bin` is not on your PATH,
or `iris` is reported as not found, add it:

<!-- ci: skip edits the shell startup files of the machine it runs on -->
```bash
uv tool update-shell        # or: pipx ensurepath
```

Then open a new terminal. The change only applies to shells started afterwards. To use
`iris` in the current shell right away, run `export PATH="$HOME/.local/bin:$PATH"`.

## Check the machine: `iris doctor`

<!-- ci: exit 0,1 -->
```bash
iris doctor
```

`iris doctor` checks what IRIS needs here: the Python version, the platform, memory,
disk space, a writable `IRIS_HOME` (default `~/.iris`), Ollama, the starter models and
the vault master key. Each check prints a one-line fix when it fails. The report ends
with a verdict, which is also the exit code:

| Verdict | Exit code | What it means |
|---|---|---|
| Ready. | 0 | Everything real use needs is in place. |
| Ready for the demo only. | 1 | The demo runs; real use still needs Ollama, the starter models, a vault master key, or more RAM. |
| Not ready. | 2 | Fix the failing checks first (the Python version, under 8 GB, or an unwritable `IRIS_HOME`). |

Right after installing, "demo only" is normal. The next page runs the demo.

The "Model set" line says which set of models the report is sized to. Doctor picks the
largest set in `config/doctor.yaml` your machine fits: `standard` (the shipped models,
16 GB) on most machines, `lite` (8 GB, about 5 GB of models resident) on smaller
laptops. `--fix` then pulls the set's models and points the tiers at them.
`iris doctor --model-set <name>` forces a set instead. The choice is saved with your
settings, so an upgrade keeps it, and `iris doctor --model-set standard --fix` moves
back.

### The safe fixes: `iris doctor --fix`

<!-- ci: skip downloads the starter models (4.5 to 11.4 GB) from Ollama -->
```bash
iris doctor --fix
```

`--fix` applies only three kinds of fix, and asks before each: it points the tiers at
the model set's models when that set is not the one applied, it pulls the set's missing
starter models through Ollama (every model a first chat calls, the router and tiers 1
and 2, and the email judge's), and it creates
a vault master key if there is none. It never overwrites a stored key, since your
secrets are encrypted under it. Add `--yes` to skip the questions. `--json` prints the
report as JSON for scripts, and applies no fixes.

**The vault master key.** Every governed tool call needs it, so without it IRIS
refuses them. On macOS, `--fix` stores the key in the Keychain. On Linux with no
keyring (WSL2, a headless server), it prints an `export IRIS_VAULT_MASTER_KEY=...`
line instead. Put that line in your shell profile, and keep a copy somewhere safe,
because the vault cannot be read without the key.

**The email judge's model.** The email assistant's judge is pinned to one small model
(`config/llm_tiers.yaml`'s `email_judge` tier, `qwen3.5:4b-q4_K_M`), and every model
set lists it among its starters, so `--fix` pulls it. On an install from before 0.1.6,
or if `iris email setup`'s "Classify" step stops with "model 'qwen3.5:4b-q4_K_M' is not
pulled", pull it yourself:

<!-- ci: skip downloads the email judge's model (about 2.5 GB) from Ollama -->
```bash
ollama pull qwen3.5:4b-q4_K_M
```

The demo is unaffected either way, since it runs on a scripted model.

## Guided setup: `iris setup`

The steps above can also run as one guided wizard: `iris setup` walks through the
preflight, the auth secret, and the optional add-ons (the background services, a
Telegram pairing, email) in order, resuming where you left off if you stop partway.
`iris setup --status` shows progress; `iris setup --reset` starts over.

## Next

[Try the demo](demo.md): a synthetic mailbox, end to end, in a few seconds.
