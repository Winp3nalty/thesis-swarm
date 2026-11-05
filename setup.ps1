#Set-ExecutionPolicy Unrestricted -Scope Process
#.\setup.ps1

# setup.ps1 - Zero-Hang Restorer for Marsio Workstation
# Installs everything per-user (no admin rights needed): Python, venv + packages, Ollama, model.
# Usage:
#   .\setup.ps1                                          -> sets up, then runs run_loop.py (the swarm)
#   .\setup.ps1 -Script tests\test_workers.py                  -> sets up, then runs the Week 3 worker test
#   .\setup.ps1 -Script tests\test_workers.py -ScriptArgs "--limit 3"
#   .\setup.ps1 -Model qwen3:14b                         -> one run with another model (default: model.txt)
param(
    [string]$Script = "run_loop.py",
    [string]$Model = "",
    [string]$ScriptArgs = ""
)

$ProgressPreference = 'SilentlyContinue'

# Always work from the folder this script lives in (run_loop.py is here)
Set-Location $PSScriptRoot

# Model choice, highest priority first: -Model argument, $env:SWARM_MODEL, model.txt, qwen3:8b.
# Normally just edit model.txt (it also lists which models fit which GPU).
# A value this script set in an earlier run stays in the terminal; forget it so it never overrides model.txt.
if ($env:SWARM_MODEL_AUTO) {
    Remove-Item Env:SWARM_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:SWARM_MODEL_AUTO -ErrorAction SilentlyContinue
}
$modelSource = "built-in default"
if ($Model) {
    $env:SWARM_MODEL = $Model
    $env:SWARM_MODEL_AUTO = "1"
    $modelSource = "-Model argument"
} elseif ($env:SWARM_MODEL) {
    $modelSource = "`$env:SWARM_MODEL variable left in this terminal (run: Remove-Item Env:SWARM_MODEL to use model.txt)"
} else {
    $modelFile = Join-Path $PSScriptRoot "model.txt"
    if (Test-Path $modelFile) {
        $line = Get-Content $modelFile | ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith("#") } | Select-Object -First 1
        if ($line) { $env:SWARM_MODEL = $line; $env:SWARM_MODEL_AUTO = "1"; $modelSource = "model.txt" }
    }
}
if (-not $env:SWARM_MODEL) { $env:SWARM_MODEL = "qwen3:8b"; $env:SWARM_MODEL_AUTO = "1" }
$model = $env:SWARM_MODEL
Write-Host "[*] Model: $model (from $modelSource)" -ForegroundColor Green
$versionFile = Join-Path $PSScriptRoot "version.txt"
if (Test-Path $versionFile) {
    $versionLabel = Get-Content $versionFile | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") } | Select-Object -First 1
    if ($versionLabel) { Write-Host "[*] Project version: $versionLabel (edit version.txt)" -ForegroundColor Green }
}

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

Write-Host "[+] Installing pydantic into thesis_venv..." -ForegroundColor Yellow
& $venvPython -m pip install -q --disable-pip-version-check pydantic
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
foreach ($f in @($Script, "run_loop.py", "model.txt", "version.txt", "requirements.txt",
                 "swarm\__init__.py", "swarm\core\paths.py", "swarm\core\deps.py", "swarm\llm_agents\client.py",
                 "swarm\skills\__init__.py", "swarm\code_agents\dispatcher.py", "swarm\output\output_writer.py",
                 "scenarios\shenzhen_3day.json", "scenarios\shenzhen_7day.json", "scenarios\silicon_valley_7day.json",
                 "data\cost\shenzhen.json", "data\cost\silicon_valley.json",
                 "data\practical\shenzhen.json", "data\practical\silicon_valley.json", "tests\w3_cases.json",
                 "prompts\tech_planner\SOUL.md", "prompts\logistics\SOUL.md", "prompts\justice\SOUL.md",
                 "prompts\transit\SOUL.md", "prompts\outreach\SOUL.md")) {
    if (!(Test-Path (Join-Path $PSScriptRoot $f))) { Fail "$f not found next to setup.ps1." }
}

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Environment Ready! Launching Swarm...  " -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan

# Run the chosen script directly using the venv Python executable
$extra = @()
if ($ScriptArgs) { $extra = $ScriptArgs -split '\s+' | Where-Object { $_ } }
& $venvPython $Script @extra
