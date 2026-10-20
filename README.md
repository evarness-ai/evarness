# Evarness

**The governed agent harness for small local models.**
Local by default. Deterministic where it can be. Governed before action. Learns in the open.

[![PyPI](https://img.shields.io/pypi/v/evarness?label=PyPI)](https://pypi.org/project/evarness/)
[![Python](https://img.shields.io/pypi/pyversions/evarness?label=Python)](https://pypi.org/project/evarness/)
[![CI](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml/badge.svg)](https://github.com/evarness-ai/evarness/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-evarness--ai.github.io-blue)](https://evarness-ai.github.io/evarness/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Discussions](https://img.shields.io/badge/discussions-ask%20and%20share-green)](https://github.com/evarness-ai/evarness/discussions)

Evarness runs an agent loop (classify, plan, act with tools, curate the answer) on models
that fit on a laptop, through Ollama, LM Studio or any OpenAI-compatible server. Every model
call and every tool call passes through a governance kernel you cannot unplug. A turn that
can be answered exactly is answered with no model at all. The harness measures its own
routing and tool use and proposes changes with evidence; it applies none of them until you
say so. Everything outside the loop and the kernel is a plugin on a small, stable SDK.

The proof is the email assistant that ships with it: a private inbox assistant that reads,
sorts and labels your mail on a local model, writes nothing until you approve it, and leaves
an audit row for every call. The email assistant is not the product. It is the first proof
that the harness works.

Documentation: **https://evarness-ai.github.io/evarness/**. Say it "ev-AR-ness".

## Why I built it

I started Evarness as a private experiment: a personal assistant that runs on my own
machines, on small local models, and helps with real work without sending my mail to
someone else's cloud. As I added tools, routing, memory and email, the model turned out to
be the smaller problem. A bigger model did not give me reliable tool calls, a local model did
not give me permission boundaries, a system prompt was not a security boundary, and I did
not want "self-learning" to mean the system quietly rewriting itself. Those became the
harness's responsibilities, and Evarness is the result. Models provide cognition.
Deterministic paths provide reliability. Plugins provide capability. Learning provides
adaptation. Governance provides authority.

## Try it in three minutes

You need [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/)
([install notes](https://evarness-ai.github.io/evarness/getting-started/install/)), and
Python 3.12 or 3.13: `uv` fetches one if you have neither, and a uv or pipx pinned to
another Python says `No solution found`; pass `--python 3.13` then. On Windows, one script
sets everything up in Docker Desktop; see the
[Windows guide](https://evarness-ai.github.io/evarness/guides/windows/).

```bash
uv tool install "evarness[email]"   # or: pipx install "evarness[email]"
evarness doctor                     # can Evarness run here, and what to fix
evarness email demo                 # a synthetic mailbox, a scripted model, no network
```

The demo needs no account, no credentials and no model server. It fetches a 200-message
synthetic mailbox, sorts it, previews and writes labels, prints a first digest, and ends with
what it did, audit rows included. The demo keeps its own home, `~/.evarness-demo`, and never
touches yours. Export that home's proof bundle, ledger and session logs both, and verify it
offline:

```bash
evarness governance proof-bundle export --home ~/.evarness-demo --out bundle.json
evarness governance proof-bundle verify bundle.json   # exit 0 verified, 1 a violation
```

<img alt="A terminal recording: evarness email demo runs to its summary, then the proof bundle is exported and verified." src="https://github.com/evarness-ai/evarness/raw/main/docs/assets/recording/launch.gif" width="800">

For your own mail you need [Ollama](https://ollama.com/download) and 8 GB of RAM for the
`lite` model set, 16 GB for `standard`. Connect an account, let `evarness doctor --fix` pull
the models your machine fits, then run `evarness email setup`; it asks before any write.
[Connect your mailbox](https://evarness-ai.github.io/evarness/getting-started/connect-your-mailbox/)
has the provider notes, and [setup gotchas](https://evarness-ai.github.io/evarness/guides/setup-gotchas/)
the known snags.

## How it works

<img alt="How a turn is governed: the input screen, then either a deterministic handler answers with no model call or the agent loop runs; both paths pass the governance kernel (classify, egress gate, tool policy and approvals, audit ledger) before model tiers or plugins are reached." src="https://github.com/evarness-ai/evarness/raw/main/docs/assets/how-a-turn-is-governed.png" width="800">

The kernel is a passage, not a filter: it runs at
[nine fixed hook points](https://evarness-ai.github.io/evarness/concepts/governance/#hook-points)
in every turn, and plugins cannot add, remove or reorder them. The stage-by-stage view is in
[Architecture](https://evarness-ai.github.io/evarness/concepts/architecture/); the editable
source of the drawing is
[`docs/architecture/how-a-turn-is-governed.excalidraw`](docs/architecture/how-a-turn-is-governed.excalidraw).

## Three things a bigger model would not give you

**Deterministic where it can be.** On one grounded action, a 7B model called the right tool
25% of the time and a 35B model 0%; a deterministic handler did it 100% of the time
([the measurement](https://evarness-ai.github.io/evarness/concepts/deterministic-handlers/)).
A plugin can claim the turns it can answer exactly, and those answers pass the same
model-free guards and leave an audit row marked `deterministic`.

**Governed before action.** Data is classified before it moves; personal and secret data
stays on local models even when a cloud tier is on. Secrets reach tools as vault handles,
never as prompt text. Writes ask once; destructive actions wait for your approval, per call.
The [proof bundle](https://evarness-ai.github.io/evarness/reference/proof-bundle/) checks
three invariants offline: no personal data to a cloud model, no mailbox write without an
approval, every call and answer audited. The demo's bundle exercises the first two; the third
is observed from chat sessions, which a scripted demo does not record.

**Learns in the open.** Four scheduled jobs read the signals real turns leave behind (intent
confidence, tool failures, your corrections, task outcomes), propose experiments, recommend a
start tier per intent, and write recurring successes as quarantined skill proposals. Each is
recorded, none is applied, until you turn its flag on; the lifecycle is measure, propose,
review, promote. The jobs, the signals and
[every flag](https://evarness-ai.github.io/evarness/concepts/learning/#what-is-off-until-you-turn-it-on)
are in [Learning](https://evarness-ai.github.io/evarness/concepts/learning/).

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

## Build on it

A plugin is a package with a `manifest.yaml` and a `setup(api)`. It can answer a turn with a
deterministic handler, add tools to the governed pool, run on a schedule and deliver to a
channel, extend the console and the API, feed memory and search, and reach out through the
governed HTTP client. Everything it registers lands on the same hooks the core uses.

```bash
evarness plugins new weather-now --kind tool   # or channel, mail-provider, research-provider, skill
cd weather-now && pip install -e ".[test]" && pytest
```

`evarness.testing` runs your plugin inside a real governed Evarness on a scripted model and
lets you assert on the audit ledger. Nine runnable examples, each tested in CI, are in
[`examples/`](https://github.com/evarness-ai/evarness/tree/main/examples); what a plugin may
import, and the deprecation rule that protects it, is the
[stable API](https://evarness-ai.github.io/evarness/reference/stable-api/). Plugins can also
arrive as MCP servers, signed and verified before launch.

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

Evarness runs on macOS (Apple Silicon) and Linux; on Windows, use Docker Desktop or WSL2. It
collects no telemetry. It is pre-1.0: the stable API changes only after a deprecation cycle,
and everything else may change in any release. Until 0.2.0 the project was called IRIS; the
old package, environment variable and directory names keep working for one release and warn
once (see the [changelog](CHANGELOG.md)). To run the scheduled jobs and the console as a
service, see [Run with Docker](https://evarness-ai.github.io/evarness/guides/docker/).

## Contributing

Outside review is the contribution this project needs most. Run the demo from a clean
machine and report friction. Try to find a path around the governance kernel; what counts is
in [SECURITY.md](SECURITY.md), which also says how to report it privately. Build a plugin
outside this repository and say where the SDK gets in your way. Run it on hardware the
maintainer does not have. Tell us which abstraction does not belong in the core. A
well-reasoned challenge to an architectural decision is a contribution too; the reasons
behind the design are in the [decision log](docs/adr/index.md).

```bash
git clone https://github.com/evarness-ai/evarness.git && cd evarness
poetry install --with dev --extras email
poetry run evarness email demo           # the whole stack from source: no account, no model server
scripts/ci_local.sh --fast               # ruff, black, mypy, import contracts, the tests for what changed
```

`uv sync` in a clone installs the package and the `evarness` command too
(`uv run evarness doctor`); the gate's tools are Poetry's dev group, so `poetry install`
is the step to work on the code.

[CONTRIBUTING.md](CONTRIBUTING.md) has the governance rules every change keeps, the DCO
sign-off, the stable-tier rule and **where help is wanted**. Read the
[code of conduct](CODE_OF_CONDUCT.md). Coding agents: [AGENTS.md](AGENTS.md). Questions and
ideas go to [Discussions](https://github.com/evarness-ai/evarness/discussions); bugs to
Issues. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

Apache License 2.0; see [LICENSE](LICENSE). Evarness is, and will remain, Apache-2.0.
