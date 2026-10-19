# ADR-0109: The layered tree — `src/iris_harness` as the harness's building blocks

Status: Accepted (2026-09-13) — M6.0 landed (the contracts, the gate rule, the export fences); M6.1–M6.3 are the moves.
Relates to: OSS-PLUGIN-HARNESS-PLAN (in the private project log) decisions 1–2 and 7 (one core wheel, two repos, clean-history export), M5.1 (the LLM client is core), M5.2 (the import contracts), ADR-0106/0107/0108 (the state that becomes `memory/state`).
Supersedes nothing. Narrows the M0 layout: the package name and the plugin seam stay; the folders under them change.

## Context

The extraction milestones (M2–M5) made the harness *correct* as a plugin host: the domains
left the composition root, the reference plugins load through `PluginAPI`, two import
contracts pin the boundaries. They did not make it *legible*. `src/iris_harness` has 39
top-level folders and six loose modules; the five building blocks the README diagram names
(pipeline, tier routing, tools, memory, governance kernel) are not visible in the tree, and a
reader cannot tell the SDK from the internals or the surfaces from the services. The HTTP
services are not even in the package — they live at the repo-root `services/` and Docker
puts the repo root on `PYTHONPATH` to import them, so a wheel does not ship the server.

The owner's framing, 2026-09-13: this is going public *and* it may be a package or SDK
others build on, so the folder structure has to depict the building blocks and follow
recognisable conventions.

Measured before deciding: the import graph already contains a layering (nine packages with
high fan-in and almost no outbound edges; runtime the hub that imports 28), broken by a
handful of upward cycles (`llm → cli`, `governance → channels`, `observability → learning`,
`learning → runtime`, `plugins → runtime`). The changed-scope test gate maps source folders
to test folders *by name*, so any move that does not move tests in lockstep narrows the gate
silently.

## Decision

Eleven decisions, taken one at a time with the code measured before each; the plan's M6
section carries the full record. The shape:

```
src/
  iris_harness/            what ships — one wheel, one import root
    cli/                   the `iris` command (today's main.py + cli/*)
    server/                the HTTP services, moved in from the repo root
    playground/            the YAML scenario test bench
    plugins/               the five reference plugins — may import only iris_harness.sdk
    sdk/                   PluginAPI, HarnessServices, PluginCLI, manifest, types, providers,
                           events, logging — what a plugin author imports
    runtime/               composition root, turn pipeline, facade, loader + profiles
    agent/                 the five AgenticCore stages + the SOUL/USER loader
    services/              learning, routines, missions, heartbeat, tasks, activities,
                           health, channels, notifications, rag
    memory/                conversational memory; knowledge/ (wiki); state/ (checkpoints,
                           continuations)
    tools/  llm/           siblings: tools + skills + sandbox + mcp; client, providers, tiers
    kernel/                governance + governor; the vault absorbs the secret stores
    foundation/            persistence, observability, eventbus, data
  iris_personal/           the domains: finance, filemanager, email, calendar, market and
                           their plugins, registered as `iris_harness.plugins` entry points
  iris_code/               the coding agent (out of release 1)
```

Three rules make it hold:

1. **Layers are a contract, and the contract comes first.** An import-linter `layers`
   contract enforces the order top-down. It landed against *today's* package names with every
   upward edge on its ignore list before a single file moved; each slice renames rows and
   deletes ignores. An empty ignore list is release gate 9. Two more contracts: the core never
   imports `iris_personal` or `iris_code`.
2. **The tests mirror the source tree, and the gate never guesses.** `tests/unit/<root>/<layer>/<pkg>/`;
   `changed_tests.sh` maps by one rule and escalates to the full suite when a source path has no
   test home. The full-suite triggers live in a file a test checks against `git ls-files`.
3. **One pyproject; the public one is derived.** Private-only sections are fenced with
   `# oss-export: private-begin/-end`, stripped by `scripts/oss_strip_private.sh` during export,
   and a test strips the live file and asserts exactly one package survives.

Execution is bottom-up, one PR per layer, files moved with `git mv`, imports rewritten in bulk,
**no compatibility shims** — nothing outside the repo imports these paths, and a shim keeps the
old folder alive so the contract cannot see through it.

## Alternatives considered

- **Multiple wheels under a namespace package** (`iris-harness-kernel`, `-sdk`, `-runtime`).
  Rejected: the two-repo split already gives the one boundary that matters for reputation;
  more wheels add versioning cost with no reader benefit, and a layers contract in CI proves
  the architecture as convincingly.
- **Leave the domains in place under the export exclude list.** Rejected: the layers contract
  would need to allow-list 45 edges forever and the folder count stays at 39.
- **A `capabilities/` bucket** for everything that is not the kernel. Rejected: it buries
  memory, llm and tools one level down — the same "where is everything" problem.
- **A `surfaces/` (or `interfaces/`, `entrypoints/`) bucket** over cli, server and playground.
  Rejected after surveying Prefect, Airflow, crewAI, pydantic-ai, LangGraph and Goose: every one
  puts `cli` and `server` at the top level; nobody looks for the bucket.
- **Checkpoint state stays under the kernel** because the kernel writes it. Rejected by the
  owner: memory is everything remembered across turns, workflow state included; the kernel
  only imports the side-effect ledger from that package, which stays.
- **SDK-only for the domains too, now.** Deferred: the domain plugins import 166 distinct core
  modules; shrinking that before launch competes with the launch. The five reference plugins
  are the SDK's proof; the domains tighten after the domains repo is cut.
- **A Poetry monorepo** with three sub-projects. Rejected: three lockfiles, no workspace
  support, the gate and conftest split three ways — too much before late October.
- **Top-down or big-bang moves.** Rejected: top-down touches every file twice; a single PR is
  unreviewable and the playground diff cannot localise a regression.
- **Compatibility shims for old import paths.** Rejected: see above.

## Consequences

- **Positive.** A newcomer reads eleven folders and sees the diagram. The SDK is a folder with
  a contract, so "what is public" is a fact, not a convention. The wheel ships the server. The
  gate cannot report green for code it did not test. The public pyproject cannot drift from the
  private one without a test failing.
- **Negative.** About fourteen PRs of mechanical moves before the public flip, roughly two
  weeks at the owner's pace; during them more changed-scope runs escalate to the full suite.
  The private `poetry install` puts all three roots in one distribution, so the domain
  plugins' entry points resolve locally without ever exercising a separate wheel — accepted
  until the domains repo exists.
- **Reversibility.** Each slice is a `git mv` plus an import rewrite, individually revertible
  while the ignore list still carries the edge. After M6.3 the old layout is gone and the
  contracts would have to be rewritten to go back.

## Related ADRs

ADR-0106, 0107, 0108 (the checkpoint and continuation stores that become `memory/state`).
