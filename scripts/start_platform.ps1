# ==============================================================================
# Clinical Coding Agent Evals Platform - Unified Startup Script
# Presenter: Dr. Saurabh Aggarwal · Staff AI Scientist
# Prepared for HighLevel Technical Evaluation
# ==============================================================================

[CmdletBinding()]
param (
    [switch]$Headless,
    [switch]$Present,
    [switch]$NoInfra,
    [switch]$Detach
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Colors
function Write-Header ($text) {
    Write-Host "`n=== $text ===" -ForegroundColor Cyan
}
function Write-Success ($text) {
    Write-Host "  [OK] $text" -ForegroundColor Green
}
function Write-Info ($text) {
    Write-Host "  [..] $text" -ForegroundColor DarkGray
}
function Write-Warn ($text) {
    Write-Host "  [!] $text" -ForegroundColor Yellow
}
function Write-Err ($text) {
    Write-Host "  [ERR] $text" -ForegroundColor Red
}

Clear-Host
Write-Host "================================================================================" -ForegroundColor Blue
Write-Host "  CLINICAL CODING AGENT EVALS PLATFORM - MULTI-HARNESS LAB" -ForegroundColor Cyan
Write-Host "  Developed by Dr. Saurabh Aggarwal (Staff AI Scientist, 11+ yrs experience)" -ForegroundColor White
Write-Host "  Recruitment Evaluation for HighLevel (https://www.gohighlevel.com)" -ForegroundColor DarkCyan
Write-Host "================================================================================" -ForegroundColor Blue

# ------------------------------------------------------------------------------
# 1. Path & Prerequisites Resolution
# ------------------------------------------------------------------------------
Write-Header "Step 1: Validating Environment & Virtualenvs"

# Resolve root workspace dir (e.g. E:\HighLevel) whether invoked from workspace root, HarnessComparison, or scripts/
$WorkspaceRoot = $ScriptDir
if (Test-Path (Join-Path $WorkspaceRoot "HarnessComparison")) {
    # Invoked from workspace root E:\HighLevel
} elseif (Test-Path (Join-Path (Split-Path -Parent $WorkspaceRoot) "HarnessComparison")) {
    $WorkspaceRoot = Split-Path -Parent $WorkspaceRoot
} elseif (Test-Path (Join-Path (Split-Path -Parent (Split-Path -Parent $WorkspaceRoot)) "HarnessComparison")) {
    $WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $WorkspaceRoot)
}

$HarnessDir = Join-Path $WorkspaceRoot "HarnessComparison"
$ClinicalDir = Join-Path $WorkspaceRoot "ClinicalCoderAgent"
$UiDir = Join-Path $HarnessDir "ui"

$HarnessPy = Join-Path $HarnessDir ".venv\Scripts\python.exe"
$ClinicalPy = Join-Path $ClinicalDir ".venv\Scripts\python.exe"
$PidFile = Join-Path $WorkspaceRoot ".platform_pids.json"

if (-not (Test-Path $HarnessPy)) {
    Write-Err "HarnessComparison virtualenv not found at $HarnessPy"
    exit 1
}
Write-Success "HarnessComparison Python environment found."

if (Test-Path $ClinicalPy) {
    Write-Success "ClinicalCoderAgent Python environment found."
} else {
    Write-Info "ClinicalCoderAgent repository not yet downloaded."
    Write-Info "Starting platform in Standalone Mode (agent code will be cloned and downloaded automatically when GitHub link and commit IDs are entered in the UI)."
}

if (-not (Test-Path $UiDir)) {
    Write-Err "UI directory not found at $UiDir"
    exit 1
}
Write-Success "Vue 3 UI directory verified."

# FreeLLMAPI Gateway discovery
$FreeLlmDirCandidates = @(
    $env:FREELLMAPI_DIR,
    "C:\Users\Saurabh\Documents\Default Project\freellmapi",
    (Join-Path $ScriptDir "..\freellmapi"),
    (Join-Path $env:USERPROFILE "Documents\Default Project\freellmapi")
) | Where-Object { $_ -and (Test-Path (Join-Path $_ "package.json")) }

$FreeLlmDir = if ($FreeLlmDirCandidates.Count -gt 0) { $FreeLlmDirCandidates[0] } else { $null }
if ($FreeLlmDir) {
    Write-Success "FreeLLMAPI Gateway directory verified: $FreeLlmDir"
} else {
    Write-Info "FreeLLMAPI directory not detected on default paths (can be configured via `$env:FREELLMAPI_DIR)."
}

# ------------------------------------------------------------------------------
# 2. Infrastructure Setup (PostgreSQL 16 & Redis 7 via Docker/Podman)
# ------------------------------------------------------------------------------
if (-not $NoInfra) {
    Write-Header "Step 2: Starting Database & Message Bus Infrastructure"
    Write-Info "Checking Docker / Podman daemon..."

    try {
        $dockerVer = & docker --version 2>&1
        Write-Success "Container engine active: $dockerVer"
    } catch {
        Write-Err "Docker or Podman is not running or not in PATH. Please start your container engine."
        exit 1
    }

    Write-Info "Launching cc-postgres (:55432) and cc-redis (:56379)..."
    Push-Location $HarnessDir
    try {
        $infraOutput = & $HarnessPy -m harness_compare.cli infra up 2>&1
        Write-Success "Infrastructure containers verified."
    } catch {
        Write-Warn "Infra up output: $infraOutput"
    }
    Pop-Location

    # Wait for Redis port
    Write-Info "Waiting for Redis port 56379 to accept connections..."
    $timeout = 20
    $connected = $false
    while ($timeout -gt 0 -and -not $connected) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", 56379)
            $tcp.Close()
            $connected = $true
        } catch {
            Start-Sleep -Seconds 1
            $timeout--
        }
    }
    if ($connected) {
        Write-Success "Redis message bus active on localhost:56379."
    } else {
        Write-Warn "Could not reach Redis on localhost:56379 directly, proceeding with service startup."
    }
}

# ------------------------------------------------------------------------------
# 3. Clean up any stale instances from previous runs
# ------------------------------------------------------------------------------
Write-Header "Step 3: Checking For Existing Process Instances"

$freellmRunning = $false
$freellmPid = $null
$existing3001 = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
if ($existing3001) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:3001/livez" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $freellmRunning = $true
            $freellmPid = ($existing3001 | Select-Object -ExpandProperty OwningProcess -Unique | Select-Object -First 1)
            Write-Success "FreeLLMAPI Router already active and healthy on :3001 (PID $freellmPid)."
        }
    } catch {
        $pids3001 = $existing3001 | Select-Object -ExpandProperty OwningProcess -Unique
        Write-Warn "Port 3001 is occupied by an unresponsive process (PID $($pids3001 -join ', ')). Terminating stale process..."
        foreach ($pidToKill in $pids3001) {
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 600
    }
}

$PortsToCheck = @(8080, 8081, 5173)
foreach ($p in $PortsToCheck) {
    $existing = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        $pids = $existing | Select-Object -ExpandProperty OwningProcess -Unique
        Write-Warn "Port $p is currently occupied by PID $($pids -join ', '). Terminating stale process..."
        foreach ($pidToKill in $pids) {
            Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 600
    }
}
Write-Success "Application ports (8080, 8081, 5173) verified ready."

# ------------------------------------------------------------------------------
# 4. Spawning Background / Windowed Services
# ------------------------------------------------------------------------------
Write-Header "Step 4: Launching Platform Modules"

$ProcessTable = @{}

# Module 1: FreeLLMAPI Gateway & Router (Port 3001)
if (-not $freellmRunning) {
    if ($FreeLlmDir) {
        Write-Info "Starting FreeLLMAPI Gateway & Router on :3001..."
        $pFreeLlm = Start-Process -FilePath "npm.cmd" `
            -ArgumentList "run", "dev", "-w", "server" `
            -WorkingDirectory $FreeLlmDir `
            -PassThru
        $ProcessTable["freellmapi"] = $pFreeLlm.Id
        Write-Success "FreeLLMAPI Gateway started (PID $($pFreeLlm.Id))."
    } else {
        Write-Warn "FreeLLMAPI directory not detected. Set `$env:FREELLMAPI_DIR to auto-launch."
    }
} else {
    $ProcessTable["freellmapi"] = $freellmPid
}

# Module 2: Harness Comparison Control Plane API (Port 8080)
Write-Info "Starting Harness Comparison Control Plane API on :8080..."
$pApi = Start-Process -FilePath $HarnessPy `
    -ArgumentList "-m", "harness_compare.api" `
    -WorkingDirectory $HarnessDir `
    -PassThru
$ProcessTable["harness_api"] = $pApi.Id
Write-Success "Control Plane API started (PID $($pApi.Id))."

# Module 3: Harness Comparison Async Worker Runner
Write-Info "Starting Harness Comparison Async Worker Runner (Redis Streams Consumer)..."
$pWorker = Start-Process -FilePath $HarnessPy `
    -ArgumentList "-m", "harness_compare.cli", "worker" `
    -WorkingDirectory $HarnessDir `
    -PassThru
$ProcessTable["harness_worker"] = $pWorker.Id
Write-Success "Worker Runner started (PID $($pWorker.Id))."

# Module 4: Clinical Coder Agent Service (Port 8081)
if (Test-Path $ClinicalPy) {
    Write-Info "Starting Clinical Coder Agent API on :8081..."
    $pClinical = Start-Process -FilePath $ClinicalPy `
        -ArgumentList "-m", "uvicorn", "clinical_coder.api:app", "--host", "127.0.0.1", "--port", "8081" `
        -WorkingDirectory $ClinicalDir `
        -PassThru
    $ProcessTable["clinical_coder"] = $pClinical.Id
    Write-Success "Clinical Coder Agent started (PID $($pClinical.Id))."
} else {
    Write-Info "Skipping local agent service (:8081); will be activated once repository is downloaded via UI."
}

# Module 5: Vue 3 Dashboard UI (Port 5173)
Write-Info "Starting Vue 3 + Vite Evaluation Dashboard on :5173..."
$pUi = Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev" `
    -WorkingDirectory $UiDir `
    -PassThru
$ProcessTable["vue_ui"] = $pUi.Id
Write-Success "Vue 3 UI started (PID $($pUi.Id))."

# Save PIDs to file for stop script
$ProcessTable | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
Write-Success "Saved active process IDs to $PidFile."

# ------------------------------------------------------------------------------
# 5. Polling & Health Checks
# ------------------------------------------------------------------------------
Write-Header "Step 5: Verifying Service Health & Readiness"

function Wait-For-Endpoint ($url, $serviceName, $maxRetries = 25) {
    Write-Info "Waiting for $serviceName at $url..."
    $retries = $maxRetries
    while ($retries -gt 0) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                Write-Success "$serviceName is ready and healthy."
                return $true
            }
        } catch {
            Start-Sleep -Seconds 1
            $retries--
        }
    }
    Write-Warn "$serviceName did not respond within $maxRetries seconds (still starting in background)."
    return $false
}

Wait-For-Endpoint "http://127.0.0.1:3001/livez" "FreeLLMAPI Gateway & Router (:3001)" 25
Wait-For-Endpoint "http://127.0.0.1:8080/health" "Harness Control Plane API (:8080)" 20
if ($ProcessTable.ContainsKey("clinical_coder")) {
    Wait-For-Endpoint "http://127.0.0.1:8081/health" "Clinical Coder Agent Service (:8081)" 20
}
Wait-For-Endpoint "http://127.0.0.1:5173" "Vue 3 Web Dashboard (:5173)" 20

# ------------------------------------------------------------------------------
# 6. Service Dashboard & Quick Links
# ------------------------------------------------------------------------------
Write-Header "Step 6: Platform Live & Operational"

# Resolve presentation path (check Docs\PRESENTATION.html first, then root)
$PresPath = if (Test-Path (Join-Path $ScriptDir "Docs\PRESENTATION.html")) {
    Join-Path $ScriptDir "Docs\PRESENTATION.html"
} elseif (Test-Path (Join-Path $ScriptDir "PRESENTATION.html")) {
    Join-Path $ScriptDir "PRESENTATION.html"
} else {
    $null
}

$presUrl = if ($PresPath) { "file:///$($PresPath.Replace('\', '/'))" } else { "Not found" }

$DashboardText = @"

  +--------------------------------------------------------------------------------+
  |                        SERVICE ENDPOINTS & PORT OVERVIEW                       |
  +--------------------------------------------------------------------------------+
  |  1. Vue 3 Dashboard (UI):      http://localhost:5173                           |
  |  2. Control Plane API (Docs):  http://localhost:8080/docs                      |
  |  3. Clinical Coder API (Docs): http://localhost:8081/docs                      |
  |  4. FreeLLMAPI Router & LLM:   http://localhost:3001/v1 (or LAN :3001)         |
  |  5. PostgreSQL 16 Store:       localhost:55432 (Database: harness_compare)     |
  |  6. Redis 7 Message Bus:       localhost:56379 (Stream: cc:jobs)               |
  |  7. Interactive Presentation:  $presUrl
  +--------------------------------------------------------------------------------+

"@
Write-Host $DashboardText -ForegroundColor Green

# ------------------------------------------------------------------------------
# 7. Browser Launch
# ------------------------------------------------------------------------------
if (-not $Headless) {
    Write-Info "Opening platform in default browser..."
    Start-Process "http://localhost:5173"
    if ($Present -and $PresPath) {
        Start-Process $PresPath
    }
}

Write-Host "`n  To stop all services and containers, run: .\stop_platform.ps1" -ForegroundColor Yellow

if (-not $Detach) {
    Write-Host "`n[INFO] Platform is running. Press [Ctrl+C] to stop all services..." -ForegroundColor Cyan
    try {
        while ($true) {
            Start-Sleep -Seconds 2
        }
    } finally {
        Write-Host "`nStopping platform services..." -ForegroundColor Yellow
        $stopArgs = @()
        if ($NoInfra) { $stopArgs += "-KeepInfra" }
        & (Join-Path $ScriptDir "stop_platform.ps1") @stopArgs
    }
}
