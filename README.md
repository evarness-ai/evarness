# Evarness

**The governed agent harness for small local models.**
Deterministic where it can be, governed everywhere, and it learns in the open: it proposes, you approve.

[![PyPI](https://img.shields.io/pypi/v/evarness?label=PyPI)](https://pypi.org/project/evarness/)
[![Python](https://img.shields.io/pypi/pyversions/evarness?label=Python)](https://pypi.org/project/evarness/)
[![CI](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml/badge.svg)](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-evarness--ai.github.io-blue)](https://evarness-ai.github.io/evarness/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Discussions](https://img.shields.io/badge/discussions-ask%20and%20share-green)](https://github.com/evarness-ai/evarness/discussions)

Python 3.12 and 3.13. FastAPI and Pydantic. Ollama, LM Studio or any OpenAI-compatible
server for the models. OpenTelemetry for traces, a Fernet vault for secrets, SQLite for the
ledger, a React console. MCP as client and as server. Say it "ev-AR-ness".

Evarness runs an agent loop (classify, plan, act with tools, curate the answer) on models
that fit on a laptop. Every model call and every tool call in the agent loop passes through
a governance kernel you cannot unplug. A turn that can be answered exactly is answered with
no model at all. The harness measures its own routing and tool use, proposes changes with
evidence, and applies none of them until you say so. Everything outside the loop and the
kernel is a plugin, written against a small, stable SDK.

The proof is the email assistant that ships with it: a private inbox assistant that reads,
sorts and labels your mail on a local model, writes nothing until you approve it, and leaves
an audit row for every call.

Documentation: **https://evarness-ai.github.io/evarness/**

## Try it in three minutes

On Windows, skip to [On Windows](#on-windows): one script sets everything up.

You need [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/); neither ships
with Python, and `pip install uv` is the shortest route
([details](https://evarness-ai.github.io/evarness/getting-started/install/)).

```bash
uv tool install "evarness[email]"   # or: pipx install "evarness[email]"
evarness doctor                     # can Evarness run here, and what to fix
evarness email demo                 # a synthetic mailbox, a scripted model, no network
```

`evarness email demo` needs no account, no credentials and no model server. It fetches a
200-message synthetic mailbox, sorts it, previews and writes labels, prints a first digest,
and ends with what it did, audit rows included.

Then your own mailbox. Connect an account first, because `evarness email setup` only takes
one that is already connected:

```bash
evarness auth imap login --user you@example.com   # an app password; or: evarness auth gmail login --user you@gmail.com
evarness doctor --fix                             # pulls the model set your machine fits, the email judge included
evarness email setup                              # fetch, classify, preview labels, ask before any write
```

For your own mail you need [Ollama](https://ollama.com/download) and 8 GB of RAM for the
`lite` model set, 16 GB for `standard`; `evarness doctor --fix` picks the set and pulls it.
[Connect your mailbox](https://evarness-ai.github.io/evarness/getting-started/connect-your-mailbox/)
has the provider notes (iCloud, Fastmail, Yahoo, Gmail; Outlook and Microsoft 365 are not
supported over IMAP) and the Gmail OAuth steps. If something stops, the
[setup gotchas](https://evarness-ai.github.io/evarness/guides/setup-gotchas/) page has the
known ones: a uv pinned to another Python (`uv tool install --python 3.13 "evarness[email]"`),
`uv sync` in a clone (Evarness is a Poetry project; use Poetry to work on the code), and
`evarness` not on your PATH after an install (`uv tool update-shell`, then a new terminal).

### On Windows

One script sets up Evarness in Docker Desktop with the models on Ollama for Windows, sized
for your machine. Install [Git for Windows](https://git-scm.com/download/win), then in
PowerShell:

```powershell
git clone https://github.com/evarness-ai/evarness.git
cd evarness
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
```

It asks before it installs or changes anything (`-Yes` answers for you): it installs and
starts Docker Desktop and Ollama for Windows if they are missing, lets Ollama use your GPU
(NVIDIA and AMD as they are; an integrated Intel or AMD GPU is turned on when Ollama reports
skipping it), writes `.env` once, builds Evarness from your checkout, runs
`evarness doctor --fix`, and asks it "hi, how are you?" to show it answers. The first run
downloads 4.5 to 11.4 GB of models; later runs take about two minutes. Then open
http://127.0.0.1:8003/. Start again or rebuild after a `git pull` with `.\setup-windows.ps1`;
stop with `.\setup-windows.ps1 -Stop`. The
[Windows guide](https://evarness-ai.github.io/evarness/guides/windows/) has each step.

## How a turn is governed

The kernel runs its checks at fixed points in every turn. Each check is a small plugin of
the kernel with a priority, and each decision (allow, deny, transform, or ask the owner)
writes a row to the audit ledger.

| Hook point | When | Typical checks |
|---|---|---|
| `pre_turn` | A user message arrives, before anything acts on it | Classification, the input safety screen |
| `pre_classify` | Text is about to enter a model | Data classification |
| `pre_llm_call` | Before each model call | The egress gate, the cost limiter |
| `pre_tool_use` | Before each tool call | Tool policy, approvals, the filesystem jail, network and command allowlists, vault handles |
| `post_tool_use` | After each tool call | The side-effect ledger, scanning external content |
| `pre_egress` / `post_egress` | A plugin's request through the governed HTTP client, before it is sent and after it ends | The plugin's declared hosts, the owner-PII egress read |
| `post_step` | After each step of the loop | The evaluator: loops, goal drift, the step cap |
| `pre_response` | Before an answer ships | Credentials, identity secrets, internal details, the model-free response checks |

Plugins cannot add, remove or reorder kernel hooks. Everything a plugin registers (see
[Build on it](#build-on-it)) lands on these same hooks, so the same governance applies, with
no side door. The full description is in
[Governance](https://evarness-ai.github.io/evarness/concepts/governance/).

## Three things a bigger model would not give you

### Deterministic where it can be

On one grounded action, a 7B model called the right tool 25% of the time and a 35B model 0%;
a deterministic handler did it 100% of the time. A plugin can claim the turns it can answer
exactly, and Evarness governs those answers like generated ones: the input screen before, the
model-free response checks after, an audit row marked `deterministic`
([deterministic handlers](https://evarness-ai.github.io/evarness/concepts/deterministic-handlers/)).

### Governed everywhere

Governance is a passage, not a filter. Data is classified before it moves. Personal and
secret data stays on local models. Secrets reach tools as vault handles, never as prompt
text. Writes ask once; destructive actions wait for your approval, per call. Every model
call, tool call and answer writes a ledger row, and a
[proof bundle](https://evarness-ai.github.io/evarness/reference/proof-bundle/) lets a CI job
verify the email assistant's invariants offline: no personal data to a cloud model, no
mailbox write without an approval, every call and answer audited. Check them yourself:

```bash
evarness governance proof-bundle export --out bundle.json
evarness governance proof-bundle verify bundle.json   # exit 0 verified, 1 a violation
```

In an adversarial battery of 14 probes against the full guard stack, 11 were fully defended;
the misses were in model-based judges, and the deterministic guards held. Since then every
answer, generated or not, passes the model-free guards.

### It learns in the open

Evarness watches its own performance and writes down what it would change. By default it
**measures and proposes**; it **applies nothing** until you turn a flag on, and what it
applies goes through the same kernel as everything else.

What runs out of the box, as scheduled jobs in the API process:

- **The learning tick** (every 15 minutes) reads the signals every turn leaves behind
  (intent confidence, tool failures, corrections, task outcomes), evaluates the strategies in
  `config/learning/strategies.yaml` and proposes or discards experiments.
- **Escalation-prior mining** (hourly) works out which intents keep needing a bigger model
  and recommends a start tier for each. The recommendation is recorded, not applied.
- **Skill crystallization** (every 6 hours) finds intents the harness handles well,
  repeatedly, without corrections, and writes each as a quarantined skill proposal under
  `config/skills/auto/<slug>/`. The registry ignores that directory until a human moves a
  proposal out of it.
- **Experiment re-measurement** (every 6 hours) keeps or discards only the experiments a
  human explicitly promoted.

What is off until you say so, and how to turn it on:

| Capability | Default | Turn on with |
|---|---|---|
| The escalation judge: decides when a turn deserves a bigger model | off | `EVARNESS_CURATOR_ESCALATION=1`; it starts in `shadow` (records the verdict, changes nothing) and `EVARNESS_CURATOR_ESCALATION_MODE=enforce` makes it act |
| Learned start-tier priors applied to the router | off | `EVARNESS_CURATOR_ESCALATION_PRIORS=1`, or `priors.enabled` in `config/escalation.yaml` |
| Escalation to a cloud tier | off | `EVARNESS_CURATOR_ESCALATION_CLOUD=1`; the egress gate still refuses personal and secret data |
| Routine reflection: proposes changes to failing or stale routines, into your approval queue | off | `EVARNESS_ROUTINE_REFLECTION=1` |
| Sandbox pre-flight: replays a frozen workload under a proposed variant in an isolated runtime before it is re-measured live | off | `EVARNESS_SANDBOX_PREFLIGHT=1`, and `EVARNESS_CRYSTALLIZE_PREFLIGHT=1` for skill proposals |
| The digital twin: a behavior miner and an intention analyst that propose behaviors and goals for your approval, and a learning analyst that only reports | off | `EVARNESS_BEHAVIOR_MINER=1`, `EVARNESS_INTENTION_ROLLUP=1`, `EVARNESS_LEARNING_ANALYST=1`, or the console's Self-Learning screen, which flips them live |
| The correction judge: spends a governed model call to tell whether you corrected the last answer | off | `EVARNESS_LEARNING_CORRECTION_JUDGE=1` |
| Agent self-management tools: the agent may act on its own memory and settings | off | `EVARNESS_AGENT_SELF_MANAGEMENT=1` |

See what it has learned with `evarness learning status`, `evarness learning recommendations`
and `evarness learning preview`, review the digital twin's proposals with
`evarness behaviors` and `evarness intentions`, or open the console's Self-Learning screen.
The whole loop, its signals and its files are described in
[Learning](https://evarness-ai.github.io/evarness/concepts/learning/).

## Build on it

A plugin is a package with a `manifest.yaml` and a `setup(api)`. Scaffold one, with a test
that passes offline, by kind:

```bash
evarness plugins new weather-now --kind tool   # or channel, mail-provider, research-provider, skill
cd weather-now && pip install -e ".[test]" && pytest
```

Everything a plugin registers through `PluginAPI` lands on the same machinery the core uses:

- **Answer a turn:** `register_intercept` (a deterministic handler that claims a turn),
  `register_intent_handler`, `register_loop_intent`.
- **Act:** `register_tool` and `declare_tool` (tools in the governed pool, with their
  effect and approval rules in the manifest), `provide` and `capability` (typed
  capabilities between plugins), `register_confirmation_executor` (what runs once the owner
  approves), `register_credential_check`, `register_owner_identity_source`.
- **Run on a schedule and deliver:** `register_heartbeat`, `register_channel` (Telegram,
  web push, your own), `register_public_callback`, `subscribe` and `publish` on the event
  bus, `on_approved_call`.
- **Extend the console and the API:** `register_api_router`, `register_agent_panel`,
  `register_footer_line`.
- **Feed memory, search and learning:** `register_learned_source`,
  `register_search_provider`.
- **Reach out safely:** `api.http`, the governed HTTP client (declared hosts only, every
  request on the ledger), and `api.tools`, the plugin's own bound tools.

`evarness.testing` runs your plugin inside a real governed Evarness in your test, on a
scripted model, and lets you assert on the audit ledger. Plugins can also arrive as MCP
servers (`trust: mcp`), signed and verified before launch.

| Use case | What Evarness gives you |
|---|---|
| **A private email assistant** | Triage, a daily digest and labels on a local model. Personal data never reaches a cloud model; nothing changes in your mailbox until you approve it, once per account. |
| **Your own domain agent** | Tools, deterministic handlers and an agent on the governed loop ([`examples/05-your-own-agent`](https://github.com/evarness-ai/evarness/tree/main/examples/05-your-own-agent)). |
| **Proactive routines** | Scheduled jobs that deliver to a channel: Telegram, web push, the web console ([`examples/04-routine-to-telegram`](https://github.com/evarness-ai/evarness/tree/main/examples/04-routine-to-telegram)). |
| **Governed research** | One `research` tool over a provider chain (SearXNG, Tavily, Brave, DuckDuckGo), cached and reranked, behind the egress gate. |
| **Safe code execution** | A bounded model-to-sandbox loop that runs shell commands in a container. |

Nine runnable examples, each tested in CI, are in
[`examples/`](https://github.com/evarness-ai/evarness/tree/main/examples). What a plugin may
import, and the deprecation rule that protects it, is the
[stable API](https://evarness-ai.github.io/evarness/reference/stable-api/).

## See every decision

The web console shows the ledger. Governance has the kernel's posture and the proof bundle's
invariants, checked against the audit rows; Call trace has each turn as a graph, with the
governance decision at every hook. Both are from `evarness email demo`:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/evarness-ai/evarness/raw/main/docs/assets/screenshots/governance-dark.webp">
  <img alt="The Governance screen after the email demo: the kernel's protections, mostly on, over the run's audit entries, and the proof bundle verified, with no-private-to-cloud and mailbox-write-approved holding." src="https://github.com/evarness-ai/evarness/raw/main/docs/assets/screenshots/governance-light.webp" width="800">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/evarness-ai/evarness/raw/main/docs/assets/screenshots/call-trace-dark.webp">
  <img alt="The Call trace of the first-chat welcome turn: four steps, no model call, with the pre_response guard's allow decision open in the detail panel." src="https://github.com/evarness-ai/evarness/raw/main/docs/assets/screenshots/call-trace-light.webp" width="800">
</picture>

## What ships

| Part | Where |
|---|---|
| The agentic core: router, planner, ReAct loop, executor, curator, with tiered model routing (a router model, local tiers on Ollama or LM Studio, an opt-in cloud tier) and model sets sized to the host (`evarness doctor` picks one) | `src/evarness/agent/`, `config/llm_tiers.yaml`, `config/doctor.yaml` |
| The governance kernel: classification, the egress gate, a Fernet vault, tool policy, approvals, an out-of-process evaluator, the audit ledger | `src/evarness/kernel/governance/` |
| The learning loop: signals, strategies and experiments, escalation priors, skill crystallization, routine reflection, the digital twin | `src/evarness/services/learning/`, `src/evarness/runtime/self_learning_loop.py`, `config/learning/` |
| The plugin SDK and host | `src/evarness/sdk/`, `src/evarness/runtime/plugin_host/` |
| Model adapters: LangChain's Ollama and OpenAI-compatible clients, kept as a thin layer | `src/evarness/llm/` |
| Reference plugins: `system`, `research`, `code_exec`, and the Telegram, web and web-push channels | `src/evarness/plugins_builtin/` |
| The email assistant: the mail record and the `gmail`, `imap` and `email_workflows` plugins | `src/evarness_personal/` |
| **memris**, the memory graph: claims with provenance | `src/memris/` |
| Four services: the Evarness API (chat, the MCP server, the web console; 8003), the Governor (8080), the evaluator (8090), the channel gateway (8006) | `src/evarness/server/` |
| The web console (React), built into the released package | `webui/` |
| The decision log: 120 architecture decision records, with a status pass | `docs/adr/` |

Evarness runs on macOS (Apple Silicon) and Linux; on Windows, use Docker Desktop or WSL2. It
collects no telemetry. It is pre-1.0: the stable API changes only after a deprecation cycle,
and everything else may change in any release. Until 0.2.0 the project was called IRIS; the
old package, environment variable and directory names keep working for one release and warn
once (see the [changelog](CHANGELOG.md)).

## Where it is today (0.2.0)

1. **Learning proposes, it does not apply.** Priors, crystallized skills and experiment
   variants are recorded and quarantined; each has a documented flag to turn application on.
2. **Brief and digest slots run their skill tools in-process:** a redaction pass, but no
   per-call hook, approval check or audit row. Tracked in
   [#199](https://github.com/evarness-ai/evarness/issues/199), help wanted.
3. **MCP launch signing defaults to shadow:** every call is verified and the verdict is on
   the ledger rows; nothing is blocked until you set `enforce`.
4. **The audit ledger is append-only, not tamper-evident.** The hash chain is designed and
   deferred ([#231](https://github.com/evarness-ai/evarness/issues/231)).
5. **Outlook and Microsoft 365 are not supported over IMAP.** iCloud, Fastmail, Yahoo and
   Gmail are. A provider over the Graph API is the first project on the help-wanted list.
6. **The MCP bridge is experimental and off by default.** Known gaps in
   [#111](https://github.com/evarness-ai/evarness/issues/111) to
   [#114](https://github.com/evarness-ai/evarness/issues/114).
7. **Docker with the bundled CPU-only Ollama needs more than 8 GB for the 7B model,** and
   the lite set has not been tried inside Docker. Native Ollama with `make up-host` works.
8. **Evarness has one maintainer.** Response times follow from that; contributions are how
   it changes.

## Run the services

The scheduled jobs (the email sweep, the judge, the learning loop) and the web console run
in the Evarness API. [Run with Docker](https://evarness-ai.github.io/evarness/guides/docker/)
keeps it up as a service:

```bash
echo "EVARNESS_AUTH_SECRET=$(openssl rand -hex 32)" >> .env   # once; .env is git-ignored
docker compose up        # Ollama, the Governor and the Evarness API, on 127.0.0.1 only
```

Every `docker compose` command needs `EVARNESS_AUTH_SECRET`, which is why the snippet saves it
to `.env`. Containerized Ollama is CPU-only, so on a Mac with Ollama already installed run
`make up-host` instead: it starts the Governor and the API against your native Ollama. If a
host Ollama already holds port 11434, the bundled one fails to start; use `make up-host` or
stop the host Ollama. Open the console at http://127.0.0.1:8003/; every API call needs
`Authorization: Bearer $EVARNESS_AUTH_SECRET`. On Windows, use `setup-windows.ps1` instead.

## Contributing

Plugins are the contribution unit. From a clone to a change you can check:

```bash
git clone https://github.com/evarness-ai/evarness.git && cd evarness
poetry install --with dev --extras email
poetry run evarness email demo           # the whole stack from source: no account, no model server
export EVARNESS_AUTH_SECRET="test-secret-for-testing" EVARNESS_DISABLE_WARMUP=1
poetry run pytest tests/unit/<the path you changed>
scripts/ci_local.sh --fast               # ruff, black, mypy, import contracts, the tests for what changed
```

`pre-commit install` wires the same linters and the DCO sign-off check into your commits.
Before a pull request, run `scripts/ci_local.sh` (the full suite, a playground smoke run, the
secret and PII scan).

| You are changing | Start here |
|---|---|
| A plugin | `evarness plugins new`, [`examples/`](https://github.com/evarness-ai/evarness/tree/main/examples), the [stable API](https://evarness-ai.github.io/evarness/reference/stable-api/) |
| The turn pipeline | `src/evarness/runtime/turn/` |
| Governance (classification, egress, approvals, audit) | `src/evarness/kernel/governance/` |
| The learning loop | `src/evarness/services/learning/`, `config/learning/` |
| The email assistant | `src/evarness_personal/` |
| The web console | `webui/` |

[CONTRIBUTING.md](CONTRIBUTING.md) has the governance rules every change keeps, the
DCO sign-off, the stable-tier rule, and **where help is wanted**: the Outlook and Microsoft
365 mail provider, #199, the tamper-evident ledger, and the issues labelled good first
issue. Read the [code of conduct](CODE_OF_CONDUCT.md). Coding agents: [AGENTS.md](AGENTS.md).
Questions and ideas go to [Discussions](https://github.com/evarness-ai/evarness/discussions);
bugs to Issues. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
The reasons behind the design are in the [decision log](docs/adr/index.md). Changes are
listed in [CHANGELOG.md](CHANGELOG.md).

## License

Apache License 2.0; see [LICENSE](LICENSE). Evarness is, and will remain, Apache-2.0.
