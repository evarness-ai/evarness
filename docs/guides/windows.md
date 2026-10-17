# Windows

On Windows, run IRIS with Docker Desktop. To work on the code, a native checkout works too:
the CLI, the test suite and the local gate run on Windows directly, and `iris doctor` warns on
the platform row instead of refusing. WSL2 remains an option for either, the same as Linux.

The commands below are for PowerShell unless they say Git Bash.

## Run IRIS with Docker Desktop

Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) and
start it. Then, from a clone of the repository:

```powershell
# A long random secret in .env, which Compose reads for every command.
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
"IRIS_AUTH_SECRET=$(-join ($bytes | ForEach-Object { $_.ToString('x2') }))" |
    Set-Content -Encoding ascii .env

docker compose up
```

Write `.env` as ASCII as shown: Windows PowerShell 5.1 writes UTF-16 by default (`>` and
`Out-File`), and Compose cannot read that. Compose needs `IRIS_AUTH_SECRET` for every command,
`build`, `logs` and `exec` included, so keeping it in `.env` beats setting it in one session.

The rest is the same as [Run with Docker](docker.md): the services, the ports (all bound to
`127.0.0.1`) and the first start, which pulls about 6 GB of models.

### With Ollama for Windows

If [Ollama for Windows](https://ollama.com/download/windows) already runs on the machine, IRIS
in Docker can use it instead of starting its own, which saves the model download when the
models are already pulled:

```powershell
docker compose -f docker-compose.yml -f docker-compose.hostllm.yml up -d --no-deps governor iris-api
```

This is what `make up-host` runs; `make` is usually not installed on Windows. Pull the models
`config/llm_tiers.yaml` names with `ollama pull` first.

## Develop natively

You need Python 3.12 or 3.13, Poetry 2.x and Git for Windows (for Git Bash, which runs the
local gate). Check the version `python` gives you: an older Python on the `PATH` (3.11 from
the Microsoft Store, for example) is common, so name 3.12 through the `py` launcher.

```powershell
# Poetry, isolated from any project: with uv, or the official installer.
uv tool install poetry

poetry env use (py -3.12 -c "import sys; print(sys.executable)")
poetry install
poetry run iris --help
poetry run iris doctor
```

`iris doctor` warns on the Platform row: native Windows is for development, and owner-only
file modes and the audit locks are not enforced there (below). The vault master key goes in
Windows Credential Manager, as it goes in the Keychain on a Mac.

### Tests and the gate

```powershell
$env:IRIS_AUTH_SECRET = "test-secret-for-testing"
$env:IRIS_DISABLE_WARMUP = "1"
poetry run pytest tests/unit/<the path you changed>
```

The gate is a bash script; run it from Git Bash, with Poetry on the `PATH`:

```bash
scripts/ci_local.sh --fast
```

It type-checks as Linux on Windows, the platform CI runs, so mypy reports what CI would. The
full gate's secret scan needs [gitleaks](https://github.com/gitleaks/gitleaks) at the version CI
pins: `winget install Gitleaks.Gitleaks`.

### What differs on Windows

- **Audit locks.** The audit spool and compaction lock with `flock` on POSIX. On Windows the
  lock is skipped, so two IRIS processes writing the same home at once are not kept apart.
  Run one.
- **File modes.** IRIS creates its databases and keys `0o600` on POSIX. Windows has no such
  modes; what protects the files is the folder's access list. Keep `IRIS_HOME` under your user
  profile (the default), which only your account can read.
- **Symlinks.** Creating one needs Developer Mode (Settings, System, For developers). Without it
  the tests that build symlinks fail with `WinError 1314`.
- **Line endings.** `.gitattributes` checks every text file out with LF whatever
  `core.autocrlf` says, so the shell scripts run in the containers unchanged.
