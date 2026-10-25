# Contributing to Evarness

Evarness is a governed agent harness for small local models. Most contributions are
**plugins**; changes to the core are welcome too, but they carry the governance rules
below. Coding agents working on the repo: read [`AGENTS.md`](AGENTS.md) as well.

## Where things go

- **Bugs and concrete, actionable changes:** GitHub Issues, using one of the forms.
- **Questions, ideas, "is this a good plugin?":** GitHub Discussions.
- **Security issues:** never in public. See [`SECURITY.md`](SECURITY.md).

## Dev setup

Python 3.12 or 3.13 (`.python-version` says 3.12 for tools that read it), [Poetry](https://python-poetry.org/) 2.x.

```bash
poetry install --extras email  # add `-E ml` for the torch-backed embedders
poetry run evarness --help
```

Or run `./install.sh`, which finds a supported Python (Homebrew's `python3` is often
newer), does the `poetry install` above and hands off to `evarness setup`: a guided
wizard for where the home and the data go, the auth secret and the optional add-ons
(background services, Telegram pairing, email).

Let Poetry or `uv sync` create the environment. A `venv` made by hand on another Python
and then pointed at by `uv` or Poetry is where "Python version mismatch" errors come
from. To only try Evarness, do not clone at all: `uv tool install "evarness[email]"
--upgrade` ([Install](https://evarness-ai.github.io/evarness/getting-started/install/)).

## Start with a plugin

A plugin is a package with a `manifest.yaml` and a `setup(api)` that registers what it
adds. Scaffold one, with a passing test, by kind:

```bash
poetry run evarness plugin new my-plugin --kind tool   # or channel, mail-provider, research-provider, skill
```

The contract and the registration kinds are in
[`docs/architecture/plugin-contract.md`](docs/architecture/plugin-contract.md); runnable
examples are in [`examples/`](examples/README.md). A plugin imports only the **stable
tier** ([`docs/reference/stable-api.md`](docs/reference/stable-api.md)):
`evarness.sdk`, `evarness.testing`, the `PluginAPI` kinds, the manifest schema and
the `evarness.plugins` entry-point group. Everything else is internal and may change in
any release. Hold your plugin to the tier with `evarness.testing.check_stable_imports`.

## Where help is wanted

The issues labelled `help wanted` and `good first issue` are current. The ones that would
change the most for users, in order:

1. **An Outlook and Microsoft 365 mail provider over the Graph API.** The number one limit
   on the README's list. It is a plugin of kind `mail-provider`, the scaffold exists
   (`evarness plugins new my-outlook --kind mail-provider`), and the IMAP and Gmail providers
   under `src/evarness_personal/plugins/` are the two worked examples.
2. **Govern the brief and digest slots** ([#199](https://github.com/evarness-ai/evarness/issues/199)):
   they run their skill tools in-process with a redaction pass but no per-call hook, approval
   or audit row. Kernel work, with a design note on the issue.
3. **A tamper-evident audit ledger** ([#231](https://github.com/evarness-ai/evarness/issues/231)):
   the per-writer hash chain is designed and deferred.
4. **A research provider or a channel** you use and we do not ship: both are plugin kinds
   with a scaffold and a worked example.
5. **Docs:** the good-first-issue label holds the pages people ask for.

Say in the issue or in Discussions that you are taking one, so two people do not build the
same thing.

## Stable tier and deprecation

A change that removes or renames a stable name, kind, manifest key or command, or changes
its meaning, is breaking. It keeps the old name working for at least one minor release,
warns with `DeprecationWarning` naming the replacement, and is listed in the release notes.
Adding is never breaking. `tests/unit/test_stable_tier.py` pins the tier; update
`tests/fixtures/stable_tier/names.txt` only with a deprecation in the same PR.

## Governance non-negotiables

A contribution must keep all of these; review rejects a PR that weakens one.

- **Every tool call goes through the governed tool runner** (`GovernedToolRunner` in
  `src/evarness/agent/tool_runner.py`), and every model call through the governance
  kernel (`src/evarness/kernel/governance/`). `tests/security/test_no_bypass.py` fails
  a new direct call site.
- **Every model call, tool call and answer leaves an audit row.** Do not add a path that
  answers without one, including deterministic handlers.
- **Personal data stays local.** Anything that could egress is gated by the egress layer
  and ships off by default behind an opt-in flag.
- **Import contracts hold** (`poetry run lint-imports`, `[tool.importlinter]` in
  `pyproject.toml`): the core is layered, reference plugins import only the SDK, no plugin
  imports another. A new edge is a design question; do not add it to `ignore_imports`.
- **User state resolves `EVARNESS_HOME`** (`evarness.foundation.paths`), never
  `Path.home()` directly, so tests cannot write into a real profile.

## Tests and the local gate

```bash
export EVARNESS_AUTH_SECRET="test-secret-for-testing" EVARNESS_DISABLE_WARMUP=1
poetry run pytest tests/unit/<the path you changed>
scripts/ci_local.sh --fast     # ruff, black, mypy, import contracts, changed-scope tests
scripts/ci_local.sh            # the full gate (full suite, playground smoke, secret scan)
```

**`scripts/ci_local.sh`, in full, is the gate for a pull request.** Hosted `ci` does not run on
pull requests or on pushes to `main`; it runs only when started by hand, at the final phase of
a piece of work and before a release. Paste the gate's summary into the pull request's "How it
was tested" (`--fast` is for iterating and does not count). `security` (secrets, dependency
audit), `dco` and `pr-title` still run on every pull request, and CodeQL scans on its schedule.

Start the hosted run with `gh workflow run ci` (changed-scope tests on Python 3.12) or
`gh workflow run ci -f full=true` (the entire suite on 3.12 and 3.13), or Actions, ci, Run
workflow, full. Changed-scope selection is `scripts/changed_tests.sh`, which never escalates to
the whole suite on the hosted runner: when a core file such as `pyproject.toml` changes it runs
`-m smoke` plus the mapped tests and says so in the job summary.

### Releasing

A release is a `v<version>` tag (see `.github/workflows/release.yml`; the `pypi` environment
needs the maintainer's approval, so a pushed tag alone never publishes). Because hosted `ci`
no longer runs on every change, **the full run is the release checklist's first step**:

1. On the commit to be tagged, run `gh workflow run ci -f full=true` and wait for it to pass
   (whole suite on 3.12 and 3.13, lint, quickstart, docs, licenses, webui).
2. Only then tag and push `v<version>`; `release.yml` does not check for step 1 itself.

The `test` job also runs the smoke tests (`-m smoke`, the deterministic playground suite on a
real in-process runtime). The `webui` job skips itself when a pull request touches nothing
under `webui/`, `.github/workflows/` or `scripts/bundle_webui.py`; GitHub reports a skipped job
as passing, so it never blocks a merge. Every other check runs on every pull request.

Tests mirror the source tree under `tests/unit/`. Async tests run with
`asyncio_mode = "auto"`: never add `@pytest.mark.asyncio`. Tests never reach a model or the
network; use the scripted fake model in `evarness.testing`. New behaviour ships with
tests; a bug fix ships with the test that would have caught it.

Style: Black (line length 100), Ruff, MyPy strict. No emojis in code, docs or commits.
`pre-commit install` (after `pip install pre-commit` or `uv tool install pre-commit`) runs
Ruff, Black and the DCO sign-off check on every commit, from `.pre-commit-config.yaml`.

## Commits, PRs and sign-off

- **DCO, no CLA.** Every commit carries a `Signed-off-by:` line certifying the
  [Developer Certificate of Origin](https://developercert.org/): commit with `git commit -s`.
  A GitHub no-reply address is fine. The `dco` check fails an unsigned commit.
- **Titles:** `<type>(<scope>): <summary>`, type one of `feat`, `fix`, `docs`, `style`,
  `refactor`, `test`, `chore`, `ci`, `perf`, `build`; scope optional. The PR title is linted
  the same way, because it becomes the commit message.
- **Squash merge only.** One PR, one commit on `main`. Keep PRs focused.
- One CODEOWNER review is required; `src/evarness/kernel/governance/` always needs one.
- No AI attribution in commits, PRs or credits: no `Co-Authored-By` trailers for tools, no
  "generated with" footers. You are the author of what you submit.

## Reporting a bug

Use the bug form: what you ran, what you expected, what happened, your version and model
backend, and the relevant log excerpt with secrets and personal data removed.
