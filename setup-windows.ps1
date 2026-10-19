<#
.SYNOPSIS
    Set up and start Evarness on Windows: Evarness in Docker Desktop, the models on Ollama for Windows.

.DESCRIPTION
    Runs every step a first install needs, and is safe to run again (it then just starts
    Evarness on what the checkout holds now):

      1. Docker Desktop: installed and running (offers to install it with winget).
      2. Ollama for Windows: installed and running (offers to install it with winget).
         The models run there, not in a container: Ollama for Windows uses an NVIDIA (CUDA)
         or AMD (ROCm) GPU, and an integrated Intel or AMD GPU (Vulkan) once allowed;
         Docker Desktop passes only NVIDIA GPUs to a container.
      3. The GPU: if Ollama reports it skipped an integrated GPU, offers to let it use it
         (OLLAMA_IGPU_ENABLE=1 for your user) and restarts Ollama. Then says what runs the
         models.
      4. .env (git-ignored): the API secret and the vault master key, created once and never
         replaced, and this machine's RAM, so `evarness doctor` sizes the model set for the
         machine the models run on rather than the container.
      5. Evarness: builds the image from this checkout and starts the Governor and the Evarness API
         against Ollama for Windows (docker-compose.hostllm.yml).
      6. Models: `evarness doctor --fix` picks the model set for this machine
         (config/doctor.yaml), points the tiers at it and pulls its models.
      7. A first answer, to show it works end to end.

    The console is then at http://127.0.0.1:8003/. Everything listens on 127.0.0.1 only.

.PARAMETER Yes
    Answer yes to every question (installs, the GPU setting, Ollama restarts).

.PARAMETER Stop
    Stop Evarness (docker compose down; the data volume stays) and exit.

.PARAMETER ModelSet
    Use this model set from config/doctor.yaml instead of the one the machine fits.

.PARAMETER SkipModels
    Do not run `evarness doctor --fix` (no model set change, no downloads).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$Stop,
    [string]$ModelSet = "",
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$ComposeFiles = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.hostllm.yml")
$OllamaApi = "http://127.0.0.1:11434"
$OllamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
$OllamaLog = Join-Path $env:LOCALAPPDATA "Ollama\server.log"
$DockerApp = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$EvarnessApi = "http://127.0.0.1:8003"

function Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Info([string]$Text) { Write-Host "    $Text" }
function Warn([string]$Text) { Write-Host "    ! $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host "    x $Text" -ForegroundColor Red; exit 1 }

function Confirm-Step([string]$Question) {
    if ($Yes) { return $true }
    $answer = Read-Host "    $Question [Y/n]"
    return ($answer -eq "" -or $answer -match '^(y|yes)$')
}

# A native command's exit code, its output discarded. Windows PowerShell 5.1 turns a native
# command's stderr into errors when it is redirected, so the preference is relaxed here.
function Test-Native([scriptblock]$Command) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command *> $null; return ($LASTEXITCODE -eq 0) } finally { $ErrorActionPreference = $saved }
}

function Invoke-Compose {
    & docker @ComposeFiles @args
    if ($LASTEXITCODE -ne 0) { Fail "docker compose $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

function Wait-Until([scriptblock]$Condition, [int]$Seconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Seconds 3
    }
    Warn "$What did not happen within $Seconds s"
    return $false
}

# The Evarness API container's health ("healthy", "starting", ...), or "" before it exists. Looked
# up through Compose: the container's name follows the checkout's folder name.
function Get-ApiHealth {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $id = (& docker @ComposeFiles ps -q evarness-api 2>$null | Select-Object -First 1)
        if (-not $id) { return "" }
        return [string](& docker inspect -f "{{.State.Health.Status}}" $id 2>$null)
    } finally { $ErrorActionPreference = $saved }
}

function Test-OllamaApi {
    try { Invoke-RestMethod "$OllamaApi/api/version" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

# The server log from Ollama's most recent start: each start writes a "server config" line.
function Get-OllamaStartupLog {
    if (-not (Test-Path -LiteralPath $OllamaLog)) { return @() }
    $lines = @(Get-Content -LiteralPath $OllamaLog -Tail 4000)
    $start = 0
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        if ($lines[$i] -match 'msg="server config"') { $start = $i; break }
    }
    return $lines[$start..($lines.Count - 1)]
}

function Restart-Ollama {
    Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    Start-Process -FilePath $OllamaApp
    if (-not (Wait-Until { Test-OllamaApi } 90 "Ollama answering")) { Fail "Ollama for Windows did not come back" }
    # The compute lines follow the server config line by a second or two.
    Wait-Until { @(Get-OllamaStartupLog | Select-String 'msg="inference compute"').Count -gt 0 } 30 "Ollama listing its compute" | Out-Null
}

function Set-EnvLine([string[]]$Lines, [string]$Name, [string]$Value) {
    $out = @($Lines | Where-Object { $_ -notmatch "^\s*$([regex]::Escape($Name))\s*=" })
    return $out + "$Name=$Value"
}

function Get-EnvValue([string[]]$Lines, [string]$Name) {
    foreach ($line in $Lines) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)$") {
            $value = $Matches[1].Trim().Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }
    return $null
}

function New-RandomBytes([int]$Count) {
    $bytes = New-Object byte[] $Count
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return , $bytes
}

if ($Stop) {
    Step "Stopping Evarness"
    Invoke-Compose down
    Info "Stopped. The data volume stays; run .\setup-windows.ps1 to start again."
    exit 0
}

# -- 1. Docker Desktop ---------------------------------------------------------------------
Step "Docker Desktop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    if (-not (Confirm-Step "Docker Desktop is not installed. Install it with winget?")) {
        Fail "Evarness runs in Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/"
    }
    winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements
    Info "Docker Desktop is installed. Start it once (it may ask you to sign out and in), then run this script again."
    exit 0
}
if (-not (Test-Native { docker version --format "{{.Server.Version}}" })) {
    if (-not (Test-Path -LiteralPath $DockerApp)) { Fail "Docker is installed but not running, and Docker Desktop was not found to start it" }
    Info "Starting Docker Desktop..."
    Start-Process -FilePath $DockerApp
    if (-not (Wait-Until { Test-Native { docker version --format "{{.Server.Version}}" } } 240 "Docker Desktop starting")) {
        Fail "Docker Desktop did not start; open it, wait for 'Engine running', and run this script again"
    }
}
Info ("Docker " + (& docker version --format "{{.Server.Version}}") + " is running")

# -- 2. Ollama for Windows -----------------------------------------------------------------
Step "Ollama for Windows"
if (-not (Test-Path -LiteralPath $OllamaApp)) {
    if (-not (Confirm-Step "Ollama for Windows is not installed. Install it with winget?")) {
        Fail "The models run on Ollama for Windows: https://ollama.com/download/windows"
    }
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    if (-not (Test-Path -LiteralPath $OllamaApp)) { Fail "Ollama for Windows did not install where expected ($OllamaApp)" }
}
if (-not (Test-OllamaApi)) {
    Info "Starting Ollama for Windows..."
    Start-Process -FilePath $OllamaApp
    if (-not (Wait-Until { Test-OllamaApi } 90 "Ollama answering")) { Fail "Ollama for Windows did not start" }
}
Info ("Ollama " + (Invoke-RestMethod "$OllamaApi/api/version").version + " at $OllamaApi")

# -- 3. The GPU ----------------------------------------------------------------------------
Step "What runs the models"
$ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
Info ("This machine: $ramGb GB RAM; graphics: " + ($gpus -join ", "))

# Ollama decides which GPUs it can use and logs it; read that rather than guess from names.
$startup = @(Get-OllamaStartupLog)
$dropped = $startup | Select-String 'dropping integrated GPU' | Select-Object -First 1
if ($dropped) {
    $name = ""
    if ($dropped.Line -match 'description="([^"]*)"') { $name = $Matches[1] }
    Info "Ollama found an integrated GPU ($name) and is not using it, so the models run on the CPU."
    if (Confirm-Step "Let Ollama use it? (sets OLLAMA_IGPU_ENABLE=1 for your user and restarts Ollama)") {
        [Environment]::SetEnvironmentVariable("OLLAMA_IGPU_ENABLE", "1", "User")
        $env:OLLAMA_IGPU_ENABLE = "1"
        Restart-Ollama
        $startup = @(Get-OllamaStartupLog)
    }
}
$compute = @($startup | Select-String 'msg="inference compute"' | ForEach-Object {
        $library = ""; $description = ""
        if ($_.Line -match 'library=(\S+)') { $library = $Matches[1] }
        if ($_.Line -match 'description="([^"]*)"') { $description = $Matches[1] }
        [pscustomobject]@{ Library = $library; Description = $description }
    })
$onGpu = @($compute | Where-Object { $_.Library -and $_.Library -ne "cpu" })
if ($onGpu.Count -gt 0) {
    foreach ($device in $onGpu) { Info "Ollama runs the models on: $($device.Description) ($($device.Library))" }
} elseif ($compute.Count -gt 0) {
    Warn "Ollama runs the models on the CPU: expect a minute or two per answer."
} else {
    Info "Ollama's log does not say what it runs on yet; 'ollama ps' shows it once a model is loaded."
}

# -- 4. .env -------------------------------------------------------------------------------
Step ".env (git-ignored)"
$envLines = @()
if (Test-Path -LiteralPath ".env") { $envLines = @(Get-Content -LiteralPath ".env") }
if (-not (Get-EnvValue $envLines "EVARNESS_AUTH_SECRET")) {
    $envLines = Set-EnvLine $envLines "EVARNESS_AUTH_SECRET" (-join ((New-RandomBytes 32) | ForEach-Object { $_.ToString("x2") }))
    Info "Created EVARNESS_AUTH_SECRET: the bearer token every API call carries."
}
if (-not (Get-EnvValue $envLines "EVARNESS_VAULT_MASTER_KEY")) {
    # A Fernet key: 32 random bytes, URL-safe base64. The vault encrypts secrets under it, so
    # an existing one is never replaced.
    $fernet = [Convert]::ToBase64String((New-RandomBytes 32)).Replace("+", "-").Replace("/", "_")
    $envLines = Set-EnvLine $envLines "EVARNESS_VAULT_MASTER_KEY" $fernet
    Info "Created EVARNESS_VAULT_MASTER_KEY: governed tool calls need it (a container has no OS keyring)."
}
$envLines = Set-EnvLine $envLines "EVARNESS_MODEL_HOST_RAM_GB" "$ramGb"
# ASCII: Windows PowerShell's > and Out-File write UTF-16, which Compose cannot read.
Set-Content -LiteralPath ".env" -Value $envLines -Encoding ascii
Info "EVARNESS_MODEL_HOST_RAM_GB=$ramGb (the model set is sized for this machine)"

# -- 5. Evarness -------------------------------------------------------------------------------
Step "Building and starting Evarness (the first build takes a few minutes)"
# --force-recreate: both services share the evarness:latest tag, and Compose otherwise keeps a
# container on the image it was created from after a rebuild; ./config is mounted from the
# checkout, so old code would read new config.
Invoke-Compose up -d --build --force-recreate --no-deps governor evarness-api
$healthy = Wait-Until { (Get-ApiHealth) -eq "healthy" } 300 "the Evarness API reporting healthy"
if (-not $healthy) {
    & docker @ComposeFiles logs --tail 40 evarness-api
    Fail "The Evarness API did not become healthy; its last log lines are above"
}
Info "The Governor and the Evarness API are up."

# -- 6. Models -----------------------------------------------------------------------------
if (-not $SkipModels) {
    Step "Model set and models (evarness doctor --fix; the first download is several GB)"
    $doctor = @("exec", "-T", "evarness-api", "evarness", "doctor", "--fix", "--yes")
    if ($ModelSet) { $doctor += @("--model-set", $ModelSet) }
    & docker @ComposeFiles @doctor
    if ($LASTEXITCODE -ne 0) { Warn "evarness doctor reports something still missing (above); Evarness may not answer yet" }
    # The tiers the set points at are read at startup.
    Invoke-Compose restart evarness-api
    Wait-Until { (Get-ApiHealth) -eq "healthy" } 180 "the Evarness API reporting healthy" | Out-Null
}

# -- 7. A first answer ---------------------------------------------------------------------
Step "A first answer"
$secret = Get-EnvValue @(Get-Content -LiteralPath ".env") "EVARNESS_AUTH_SECRET"
$started = Get-Date
try {
    $reply = Invoke-RestMethod -Method Post "$EvarnessApi/chat" -Headers @{ authorization = "Bearer $secret" } `
        -ContentType "application/json" -Body '{"message": "hi, how are you?", "session_id": "setup-check"}' -TimeoutSec 600
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds)
    Info "You: hi, how are you?"
    Info "Evarness: $($reply.response)"
    Info "($seconds s, model $($reply.metadata.model))"
} catch {
    Warn "No answer yet: $($_.Exception.Message)"
    Info "Run 'docker compose exec evarness-api evarness doctor' to see what is missing."
}

Step "Done"
Info "Console:  $EvarnessApi/   (API calls carry EVARNESS_AUTH_SECRET from .env as a bearer token)"
Info "Stop:     .\setup-windows.ps1 -Stop"
Info "Start:    .\setup-windows.ps1   (again after a git pull: it rebuilds from the checkout)"
