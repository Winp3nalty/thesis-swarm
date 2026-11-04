#Set-ExecutionPolicy Unrestricted -Scope Process
#.\setup.ps1

# setup.ps1 - Zero-Hang Restorer for Marsio Workstation
# Installs everything per-user (no admin rights needed): Python, venv + packages, Ollama, model.
$ProgressPreference = 'SilentlyContinue'

# Always work from the folder this script lives in (run_loop.py and schemas.py are here)
Set-Location $PSScriptRoot

# Model used by the swarm. Override before running, e.g.  $env:SWARM_MODEL = "qwen3:30b-instruct"
# Default qwen3:8b (5.2 GB) fits every GPU you use, including 12 GB cards like the RTX 4070 SUPER.
# Bigger options: qwen3:14b (9.3 GB, wants 16 GB), qwen3:30b-instruct (19 GB, wants 24 GB).
if (-not $env:SWARM_MODEL) { $env:SWARM_MODEL = "qwen3:8b" }
$model = $env:SWARM_MODEL

# Show GPU memory and warn if the chosen model is probably too big (it would spill to the CPU).
$vramMiB = $null
try { $vramMiB = [int]((& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null) | Select-Object -First 1) } catch { }
if ($vramMiB) {
    Write-Host "[*] GPU memory: $vramMiB MiB, model: $model" -ForegroundColor Green
    if ($vramMiB -lt 14000 -and $model -match "(14b|27b|30b|32b|35b)") {
        Write-Host "[!] $model is probably too large for this GPU and will run slowly on the CPU. Use qwen3:8b." -ForegroundColor Yellow
    }
}

# Note: the context size (8192 tokens) is now set per request by run_loop.py, so no
# Ollama environment variable is needed. Override with $env:SWARM_NUM_CTX if required.

function Fail($msg) {
    Write-Host "[!] $msg" -ForegroundColor Red
    exit 1
}

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Fast-Restoring Thesis Environment...   " -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# ---------------------------------------------------------------
# 1. Find or install Python (per-user, no admin)
# ---------------------------------------------------------------
function Test-Python($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $out = & $exe --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$out" -match "^Python 3\.(\d+)" -and [int]$Matches[1] -ge 9) {
            return $true
        }
    } catch { }
    return $false
}

function Find-Python {
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $perUser = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    foreach ($p in $perUser) { $candidates += $p.FullName }
    foreach ($c in $candidates) {
        if (Test-Python $c) { return $c }
    }
    return $null
}

$pythonExe = Find-Python
if (-not $pythonExe) {
    Write-Host "[+] Python not found. Installing Python 3.12 for current user (no admin)..." -ForegroundColor Yellow
    $pyVersion = "3.12.8"
    $pyInstaller = "$env:TEMP\python-$pyVersion-amd64.exe"
    curl.exe -L --silent --show-error "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe" -o $pyInstaller
    if (!(Test-Path $pyInstaller)) { Fail "Could not download the Python installer (network blocked?)." }

    $proc = Start-Process -FilePath $pyInstaller -Wait -PassThru -ArgumentList `
        "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0 Shortcuts=0"
    if ($proc.ExitCode -ne 0) { Fail "Python installer failed with exit code $($proc.ExitCode)." }

    $pythonExe = Find-Python
    if (-not $pythonExe) { Fail "Python was installed but could not be found. Open a new PowerShell and re-run setup.ps1." }
    Write-Host "[*] Python installed!" -ForegroundColor Green
} else {
    Write-Host "[*] Python found: $pythonExe" -ForegroundColor Green
}

# ---------------------------------------------------------------
# 2. Add Ollama to current session PATH
# ---------------------------------------------------------------
$ollamaDir = "$env:LOCALAPPDATA\Programs\Ollama"
$ollamaExe = "$ollamaDir\ollama.exe"
if ($env:Path -notlike "*$ollamaDir*") {
    $env:Path += ";$ollamaDir"
}

# ---------------------------------------------------------------
# 3. Check and Install Ollama
# ---------------------------------------------------------------
if (!(Test-Path $ollamaExe)) {
    Write-Host "[+] Downloading Ollama with curl..." -ForegroundColor Yellow
    $installerPath = "$env:TEMP\OllamaSetup.exe"
    curl.exe -L --silent --show-error "https://ollama.com/download/OllamaSetup.exe" -o $installerPath
    if (!(Test-Path $installerPath)) { Fail "Could not download the Ollama installer (network blocked?)." }

    Write-Host "[+] Launching Ollama installer..." -ForegroundColor Yellow
    Start-Process -FilePath $installerPath -ArgumentList "/VERYSILENT /NORESTART"

    $waited = 0
    while (!(Test-Path $ollamaExe)) {
        Start-Sleep -Seconds 2
        $waited += 2
        if ($waited -ge 300) { Fail "Ollama did not finish installing within 5 minutes." }
    }
    Start-Sleep -Seconds 3
    Write-Host "[*] Ollama installed!" -ForegroundColor Green
} else {
    Write-Host "[*] Ollama is already installed." -ForegroundColor Green
}

# ---------------------------------------------------------------
# 4. Create Local Virtual Environment & Explicitly Install Packages into It
# ---------------------------------------------------------------
$localVenv = "$env:LOCALAPPDATA\thesis_venv"
$venvPython = "$localVenv\Scripts\python.exe"

# Recreate the venv if it is missing or broken (e.g. points to a Python that no longer exists)
if ((Test-Path $venvPython) -and -not (Test-Python $venvPython)) {
    Write-Host "[!] Existing venv is broken. Recreating..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $localVenv -ErrorAction SilentlyContinue
}

if (!(Test-Path $venvPython)) {
    Write-Host "[+] Creating fast local venv outside OneDrive..." -ForegroundColor Yellow
    & $pythonExe -m venv $localVenv
    if ($LASTEXITCODE -ne 0 -or !(Test-Path $venvPython)) { Fail "Failed to create the virtual environment." }
}

Write-Host "[+] Installing openai and pydantic directly into thesis_venv..." -ForegroundColor Yellow
& $venvPython -m pip install -q --disable-pip-version-check openai pydantic
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (network or proxy blocking PyPI?)." }

# ---------------------------------------------------------------
# 5. Ensure Ollama Server is Running
# ---------------------------------------------------------------
Write-Host "[+] Checking local Ollama server..." -ForegroundColor Yellow
function Test-OllamaUp {
    try {
        $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 3 -ErrorAction Stop
        return $true
    } catch { return $false }
}

if (Test-OllamaUp) {
    Write-Host "[*] Ollama server is responding!" -ForegroundColor Green
} else {
    Write-Host "[+] Starting Ollama server..." -ForegroundColor Yellow
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    $up = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-OllamaUp) { $up = $true; break }
    }
    if (-not $up) { Fail "Ollama server did not start within 30 seconds." }
    Write-Host "[*] Ollama server is up!" -ForegroundColor Green
}

# ---------------------------------------------------------------
# 6. Pull qwen2.5:7b if missing
# ---------------------------------------------------------------
Write-Host "[+] Verifying $model model..." -ForegroundColor Yellow
& $ollamaExe pull $model
if ($LASTEXITCODE -ne 0) { Fail "Could not pull $model (disk quota or network?)." }

# ---------------------------------------------------------------
# 7. Make sure the project files are present
# ---------------------------------------------------------------
foreach ($f in @("run_loop.py", "schemas.py", "state_machine.py", "handoff.py", "soul_loader.py",
                 "skills\__init__.py", "skills\builtin.py",
                 "agents\tech_planner\SOUL.md", "agents\logistics\SOUL.md", "agents\auditor\SOUL.md")) {
    if (!(Test-Path (Join-Path $PSScriptRoot $f))) { Fail "$f not found next to setup.ps1." }
}

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Environment Ready! Launching Swarm...  " -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan

# Run the script directly using the venv Python executable
& $venvPython run_loop.py
