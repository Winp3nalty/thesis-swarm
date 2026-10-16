# setup.ps1 - Zero-Hang Restorer for Marsio Workstation
$ProgressPreference = 'SilentlyContinue'

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Fast-Restoring Thesis Environment...   " -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# 1. Add Ollama to current session PATH
$ollamaDir = "$env:LOCALAPPDATA\Programs\Ollama"
$ollamaExe = "$ollamaDir\ollama.exe"
if ($env:Path -notlike "*$ollamaDir*") {
    $env:Path += ";$ollamaDir"
}

# 2. Check and Install Ollama
if (!(Test-Path $ollamaExe)) {
    Write-Host "[+] Downloading Ollama with curl..." -ForegroundColor Yellow
    $installerPath = "$env:TEMP\OllamaSetup.exe"
    curl.exe -L "https://ollama.com/download/OllamaSetup.exe" -o $installerPath
    
    Write-Host "[+] Launching Ollama installer..." -ForegroundColor Yellow
    Start-Process -FilePath $installerPath -ArgumentList "/VERYSILENT /NORESTART"
    
    while (!(Test-Path $ollamaExe)) {
        Start-Sleep -Seconds 2
    }
    Start-Sleep -Seconds 3
    Write-Host "[*] Ollama installed!" -ForegroundColor Green
} else {
    Write-Host "[*] Ollama is already installed." -ForegroundColor Green
}

# 3. Create Local Virtual Environment & Explicitly Install Packages into It
$localVenv = "$env:LOCALAPPDATA\thesis_venv"
$venvPython = "$localVenv\Scripts\python.exe"

if (!(Test-Path $venvPython)) {
    Write-Host "[+] Creating fast local venv outside OneDrive..." -ForegroundColor Yellow
    python -m venv $localVenv
}

Write-Host "[+] Installing openai and pydantic directly into thesis_venv..." -ForegroundColor Yellow
& $venvPython -m pip install -q openai pydantic

# 4. Ensure Ollama Server is Running
Write-Host "[+] Checking local Ollama server..." -ForegroundColor Yellow
try {
    $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 3 -ErrorAction Stop
    Write-Host "[*] Ollama server is responding!" -ForegroundColor Green
} catch {
    Write-Host "[+] Starting Ollama server..." -ForegroundColor Yellow
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

# 5. Pull qwen2.5:7b if missing
Write-Host "[+] Verifying qwen2.5:7b model..." -ForegroundColor Yellow
& $ollamaExe pull qwen2.5:7b

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Environment Ready! Launching Swarm...  " -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan

# Run the script directly using the venv Python executable
& $venvPython run_loop.py