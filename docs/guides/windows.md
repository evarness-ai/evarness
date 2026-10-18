# Windows

On Windows, run IRIS with Docker Desktop: one script sets it up. To work on the code, a
native checkout works too: the CLI, the test suite and the local gate run on Windows directly,
and `iris doctor` warns on the platform row instead of refusing. WSL2 remains an option for
either, the same as Linux.

The commands below are for PowerShell unless they say Git Bash.

## Run IRIS with Docker Desktop

IRIS runs in Docker Desktop and the models run on
[Ollama for Windows](https://ollama.com/download/windows). The models are not in a
container because Docker Desktop passes only NVIDIA GPUs to one, while Ollama for Windows uses
NVIDIA (CUDA), AMD (ROCm) and integrated Intel or AMD graphics (Vulkan), and the CPU when there
is no GPU.

From a clone of the repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
```

It asks before it installs or changes anything; `-Yes` answers yes to every question. In order:

1. **Docker Desktop.** Installs it with winget if it is missing, and starts it.
2. **Ollama for Windows.** The same.
3. **The GPU.** Ollama decides which GPUs it can use and logs it. An NVIDIA or AMD GPU needs
   nothing. Ollama skips an integrated GPU unless `OLLAMA_IGPU_ENABLE=1` is set, and logs
   `dropping integrated GPU` when it does; the script then offers to set it for your user and
   restart Ollama. It ends by saying what runs the models.
4. **`.env`** (git-ignored). `IRIS_AUTH_SECRET`, the bearer token every API call carries, and
   `IRIS_VAULT_MASTER_KEY`, which governed tool calls need (a container has no OS keyring),
   are created once and never replaced: your secrets are encrypted under the key. It also
   writes `IRIS_MODEL_HOST_RAM_GB`, this machine's RAM, because `iris doctor` in the container
   would otherwise size the model set for the Docker VM's memory instead of the machine the
   models run on. The file is written as ASCII: Windows PowerShell's `>` and `Out-File` write
   UTF-16, which Compose cannot read.
5. **IRIS.** Builds the image from your checkout and starts the Governor and the IRIS API
   against Ollama for Windows (`docker-compose.hostllm.yml`). It always rebuilds and
   recreates: `./config` is mounted from the checkout, and an old image reading new config
   fails.
6. **Models.** Runs `iris doctor --fix` in the container: it picks the model set your RAM fits
   (`config/doctor.yaml`), points the tiers at it and downloads its models (4.5 to 11.4 GB).
   `-ModelSet lite` forces a set; `-SkipModels` leaves the models alone.
7. **A first answer.** Asks IRIS "hi, how are you?" and prints the reply and how long it took.

Then open http://127.0.0.1:8003/. To start again, or after a `git pull`, run the script again.
`.\setup-windows.ps1 -Stop` stops IRIS; the data volume stays.

### How fast

Measured on a Core Ultra 7 155H laptop (32 GB, integrated Arc graphics), the `standard` set:

| | A short answer |
|---|---|
| On the CPU (Ollama's default for integrated graphics) | 1.5 to 2 minutes |
| On the integrated GPU (`OLLAMA_IGPU_ENABLE=1`) | about 37 seconds |
| The first answer after a start, on the integrated GPU (the models load) | 80 to 100 seconds |

Nearly all of it is the model reading IRIS's prompt, not writing the answer; a discrete GPU is
faster still. A run of the script after the first takes about two minutes before that first
answer: the image rebuilds from cache, the containers restart, and doctor finds nothing to
download.

### By hand

Without the script, the same setup is:

```powershell
# .env: the secret, written as ASCII (create it once; Compose reads it for every command).
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
"IRIS_AUTH_SECRET=$(-join ($bytes | ForEach-Object { $_.ToString('x2') }))" |
    Set-Content -Encoding ascii .env

# IRIS against Ollama for Windows; never the bundled Ollama.
docker compose -f docker-compose.yml -f docker-compose.hostllm.yml up -d --build --force-recreate --no-deps governor iris-api
docker compose exec iris-api iris doctor --fix
```

Do not use plain `docker compose up` while Ollama for Windows runs: it starts a second Ollama,
which fails with `bind: Only one usage of each socket address ... 127.0.0.1:11434`. The rest
of the stack is as in [Run with Docker](docker.md).

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
file modes are not enforced there (below). The vault master key goes in Windows Credential
Manager, as it goes in the Keychain on a Mac.

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

- **Audit locks.** The audit spool and compaction lock with `flock` on POSIX and
  `msvcrt.locking` on Windows, so two IRIS processes writing the same home are kept apart on
  both.
- **File modes.** IRIS creates its databases and keys `0o600` on POSIX. Windows has no such
  modes; what protects the files is the folder's access list. Keep `IRIS_HOME` under your user
  profile (the default), which only your account can read.
- **Symlinks.** Creating one needs Developer Mode (Settings, System, For developers). Without it
  the tests that build symlinks fail with `WinError 1314`.
- **Line endings.** `.gitattributes` checks every text file out with LF whatever
  `core.autocrlf` says, so the shell scripts run in the containers unchanged.
