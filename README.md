# IRIS

**The governed agent harness for small local models.**

IRIS runs an agent loop (classify, plan, act with tools, curate the answer) on models
that fit on a laptop. Every model call and every tool call passes through a governance
kernel you cannot unplug, and a turn that can be answered exactly is answered with no
model at all. Everything outside the loop and the kernel is a plugin, written against a
small, stable SDK.

The proof is the email assistant that ships with it: a private inbox assistant that
reads, sorts and labels your mail on a local model, writes nothing until you approve it,
and leaves an audit row for every call.

Documentation: **https://evarness-ai.github.io/iris-harness/**

## Try it in three minutes

You need [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/); neither ships with Python, and `pip install uv` is the shortest route ([details](https://evarness-ai.github.io/iris-harness/getting-started/install/)).

```bash
uv tool install "iris-harness[email]"   # or: pipx install "iris-harness[email]"
iris doctor                             # can IRIS run here, and what to fix
iris email demo                         # a synthetic mailbox, a scripted model, no network
```

Then your own mailbox. Connect an account first, because `iris email setup` only takes one
that is already connected:

```bash
iris auth imap login --user you@example.com    # an app password; or: iris auth gmail login --user you@gmail.com
ollama pull qwen3.5:4b-q4_K_M                  # the email judge's model; iris doctor --fix does not pull it
iris email setup                               # fetch, classify, preview labels, ask before any write
```

[Connect your mailbox](https://evarness-ai.github.io/iris-harness/getting-started/connect-your-mailbox/)
has the provider notes (iCloud, Fastmail, Yahoo, Gmail; Outlook and Microsoft 365 are not
supported over IMAP) and the Gmail OAuth steps.

If `iris` is not found after installing, add `~/.local/bin` to your PATH: run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal.

If an install or setup stops:

- **Python.** IRIS needs Python 3.12 or 3.13. A plain `uv tool install` fetches one for you.
  If it stops with `No solution found ... Python>=3.12,<3.14`, uv is pinned to another
  Python (a `--python` flag, `UV_PYTHON`, or a `python` setting in your uv config). Pass a
  supported one: `uv tool install --python 3.13 "iris-harness[email]"`, or
  `pipx install --python python3.13 "iris-harness[email]"`.
- **Do not use `uv sync` to install IRIS.** It is for uv projects, and IRIS is a Poetry
  project, so in a clone it builds an empty environment and installs nothing. Install the
  package with the commands above; to work on the code, use Poetry (see below).
- **The email judge's model.** If `iris email setup` stops with `model 'qwen3.5:4b-q4_K_M' is
  not pulled`, run `ollama pull qwen3.5:4b-q4_K_M` and run setup again. On 0.1.0 to 0.1.2 a
  missing model instead printed a 404 traceback per email and marked every email Unsure.
  Upgrade, then judge them again:

  ```bash
  uv tool install --refresh --force "iris-harness[email]"   # 0.1.3 or later
  iris email setup --restart                                # or: iris email judge --retry-failed
  ```

`iris email demo` needs no account, no credentials and no model server. It fetches a
200-message synthetic mailbox, sorts it, previews and writes labels, prints a first
digest, and ends with what it did, audit rows included. For your own mail you need
[Ollama](https://ollama.com/download) and 8 GB of RAM (16 GB for the standard models);
`iris doctor --fix` pulls the models for the set your machine fits. Step by step:
[Getting started](https://evarness-ai.github.io/iris-harness/getting-started/install/);
if something does not work, see the [common setup gotchas](https://evarness-ai.github.io/iris-harness/guides/setup-gotchas/).

## Build on it

A plugin is a package with a `manifest.yaml` and a `setup(api)`. Scaffold one, with a
test that passes offline, by kind:

```bash
iris plugins new weather-now --kind tool   # or channel, mail-provider, research-provider, skill
cd weather-now && pip install -e ".[test]" && pytest
```

Through `PluginAPI` a plugin registers deterministic handlers, tools, agents, scheduled
jobs, channels and approval executors. Each lands on the same machinery the core uses,
so the same governance applies, with no side door. `iris_harness.testing` runs your
plugin inside a real governed IRIS in your test, on a scripted model, and lets you
assert on the audit ledger.

| Use case | What IRIS gives you |
|---|---|
| **A private email assistant** | Triage, a daily digest and labels on a local model. Personal data never reaches a cloud model; nothing changes in your mailbox until you approve it, once per account. |
| **Your own domain agent** | Tools, deterministic handlers and an agent on the governed loop ([`examples/05-your-own-agent`](examples/05-your-own-agent/)). |
| **Proactive routines** | Scheduled jobs that deliver to a channel: Telegram, web push, the web console ([`examples/04-routine-to-telegram`](examples/04-routine-to-telegram/)). |
| **Governed research** | One `research` tool over a provider chain (SearXNG, Tavily, Brave, DuckDuckGo), cached and reranked, behind the egress gate. |
| **Safe code execution** | A bounded model-to-sandbox loop that runs shell commands in a container. |

Nine runnable examples, each tested in CI, are in [`examples/`](examples/README.md).
What a plugin may import, and the deprecation rule that protects it, is the
[stable API](docs/reference/stable-api.md).

## Why a harness

- **Determinism is a primitive.** On one grounded action, a 7B model called the right
  tool 25% of the time and a 35B model 0%; a deterministic handler did it 100% of the
  time. A plugin can claim the turns it can answer exactly, and IRIS governs those
  answers like generated ones: the input screen before, the model-free response checks
  after, an audit row marked `deterministic`.
- **Governance is a passage, not a filter.** Data is classified before it moves.
  Personal and secret data stays on local models. Secrets reach tools as vault handles,
  never as prompt text. Writes ask once; destructive actions wait for your approval.
- **Every call is audited.** Each model call, tool call and answer writes a ledger row.
  A [proof bundle](docs/reference/proof-bundle.md) lets a CI job verify the email
  assistant's invariants offline: no personal data to a cloud model, no mailbox write
  without an approval, every call and answer audited. Check them yourself:

  ```bash
  iris governance proof-bundle export --out bundle.json
  iris governance proof-bundle verify bundle.json   # exit 0 verified, 1 a violation
  ```
- **The model-free guards hold.** In an adversarial battery of 14 probes against the
  full guard stack, 11 were fully defended; the misses were in model-based judges, and
  the deterministic guards held. Since then every answer, generated or not, passes the
  model-free guards.

## See every decision

The web console shows the ledger. Governance has the kernel's posture and the proof
bundle's invariants, checked against the audit rows; Call trace has each turn as a graph,
with the governance decision at every hook. Both are from `iris email demo`:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/evarness-ai/iris-harness/raw/main/docs/assets/screenshots/governance-dark.webp">
  <img alt="The Governance screen after the email demo: the kernel's protections, mostly on, over the run's audit entries, and the proof bundle verified, with no-private-to-cloud and mailbox-write-approved holding." src="https://github.com/evarness-ai/iris-harness/raw/main/docs/assets/screenshots/governance-light.webp" width="800">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/evarness-ai/iris-harness/raw/main/docs/assets/screenshots/call-trace-dark.webp">
  <img alt="The Call trace of the first-chat welcome turn: four steps, no model call, with the pre_response guard's allow decision open in the detail panel." src="https://github.com/evarness-ai/iris-harness/raw/main/docs/assets/screenshots/call-trace-light.webp" width="800">
</picture>

## What ships

| Part | Where |
|---|---|
| The agentic core: router, planner, ReAct loop, executor, curator, with tiered model routing (a router model, local tiers on Ollama or LM Studio, an opt-in cloud tier) | `src/iris_harness/agent/`, `config/llm_tiers.yaml` |
| The governance kernel: classification, the egress gate, a Fernet vault, tool policy, approvals, an out-of-process evaluator, the audit ledger | `src/iris_harness/kernel/governance/` |
| The plugin SDK and host | `src/iris_harness/sdk/`, `src/iris_harness/runtime/plugin_host/` |
| Reference plugins: `system`, `research`, `code_exec`, and the Telegram, web and web-push channels | `src/iris_harness/plugins_builtin/` |
| The email assistant: the mail record and the `gmail`, `imap` and `email_workflows` plugins | `src/iris_personal/` |
| **memris**, the memory graph: claims with provenance | `src/memris/` |
| Four services: the IRIS API (chat, the MCP server, the web console; 8003), the Governor (8080), the evaluator (8090), the channel gateway (8006) | `src/iris_harness/server/` |
| The web console (React), built into the released package | `webui/` |

IRIS runs on macOS (Apple Silicon) and Linux; on Windows, use Docker Desktop or WSL2
([Windows guide](docs/guides/windows.md)). It collects no telemetry. It is pre-1.0: the
stable API changes only after a deprecation cycle, and everything else may change in any
release.

The MCP bridge is experimental and off by default; it only runs servers you list in
`mcp-servers.yaml`. Known gaps are tracked in
[#111](https://github.com/evarness-ai/iris-harness/issues/111),
[#112](https://github.com/evarness-ai/iris-harness/issues/112),
[#113](https://github.com/evarness-ai/iris-harness/issues/113) and
[#114](https://github.com/evarness-ai/iris-harness/issues/114).

## Run the services

The scheduled jobs (the email sweep and judge) and the web console run in the IRIS API.
[Run with Docker](https://evarness-ai.github.io/iris-harness/guides/docker/) keeps it
up as a service:

```bash
echo "IRIS_AUTH_SECRET=$(openssl rand -hex 32)" >> .env   # once; .env is git-ignored
docker compose up        # Ollama, the Governor and the IRIS API, on 127.0.0.1 only
```

On Windows, the snippet is bash: in PowerShell use the one in the
[Windows guide](https://evarness-ai.github.io/iris-harness/guides/windows/), which writes
`.env` as ASCII (Windows PowerShell's `>>` writes UTF-16, which Compose cannot read).

The first start pulls about 6 GB of model weights. Containerized Ollama is CPU-only, so on a
Mac with Ollama already installed, run `make up-host` instead: it starts the Governor and the
IRIS API against your native Ollama and skips the pull. Open the console at
http://127.0.0.1:8003/ in a browser; every API call needs `Authorization: Bearer
$IRIS_AUTH_SECRET`. Docker is for keeping IRIS up as a service; `iris email demo` is the
quicker way to try it.

Every `docker compose` command, not only `up`, needs `IRIS_AUTH_SECRET`: `ps`, `logs` and
`down` fail with `required variable IRIS_AUTH_SECRET is missing a value` in a shell where it
is not set. That is why the snippet saves it to `.env`, which Compose reads every time (use
the same value the stack started with). `make down` and `make logs` need no variable.

Two things to know before `docker compose up`. If Ollama is already running on the host, it
holds port 11434 and the bundled one fails to start (`address already in use`); use `make
up-host`, or stop the host Ollama. And the 7B model needs memory: see
[Run with Docker](https://evarness-ai.github.io/iris-harness/guides/docker/#memory).

Checked on 0.1.5 (macOS, Docker Desktop 29.4.1, 8 GB given to Docker): against a native
Ollama the image builds, the Governor and the IRIS API come up healthy, `/chat` answers a
deterministic turn and a model turn, and the console loads. With the bundled Ollama the stack
starts healthy and a deterministic turn answers, but a turn that needs the 7B model
(`qwen2.5:7b-instruct`) failed twice with `llama-server process has terminated: signal:
killed`, and Docker reported the Ollama container `OOMKilled`. Whether more memory fixes it
has not been tested.
On Windows 11 (Docker Desktop 28.3.2) against Ollama for Windows, the image builds, the
Governor and the IRIS API come up healthy and `/chat` answers a deterministic turn; a model
turn was not checked there.

The [email assistant guide](https://evarness-ai.github.io/iris-harness/guides/email/)
shows how to run the API from an install and pair the web console.

## Contributing

Plugins are the contribution unit. This is the shortest path from a clone to a change you
can check. You need Python 3.12 or 3.13 (`.python-version` pins 3.12) and
[Poetry](https://python-poetry.org/) 2.x.

```bash
git clone https://github.com/evarness-ai/iris-harness.git && cd iris-harness
poetry install --with dev --extras email
poetry run iris email demo               # the whole stack from source: no account, no model server
```

Tests need two environment variables, and run on the narrowest path that covers your
change:

```bash
export IRIS_AUTH_SECRET="test-secret-for-testing" IRIS_DISABLE_WARMUP=1
poetry run pytest tests/unit/<the path you changed>
```

Before you push, run the local gate: `scripts/ci_local.sh --fast` while you iterate (ruff,
black, mypy, import contracts, the tests for what changed), and `scripts/ci_local.sh`
before a pull request (the full suite, a playground smoke run, the secret and PII scan).

Where to look first:

| You are changing | Start here |
|---|---|
| A plugin | `iris plugins new` (above), [`examples/`](examples/README.md), [stable API](docs/reference/stable-api.md) |
| The turn pipeline | `src/iris_harness/runtime/turn/` |
| Governance (classification, egress, approvals, audit) | `src/iris_harness/kernel/governance/` |
| The email assistant | `src/iris_personal/` |
| The web console | `webui/` |

Read [CONTRIBUTING.md](CONTRIBUTING.md) (the governance rules every change keeps, DCO
sign-off, the stable-tier rule) and the [code of conduct](CODE_OF_CONDUCT.md). Coding
agents: [AGENTS.md](AGENTS.md). Questions and ideas go to GitHub Discussions; bugs to
Issues. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

Apache License 2.0; see [LICENSE](LICENSE). IRIS is, and will remain, Apache-2.0.
