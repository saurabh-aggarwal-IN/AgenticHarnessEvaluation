# ==============================================================================
# Clinical Coding Agent Evals Platform - Unified Shutdown Script
# Presenter: Dr. Saurabh Aggarwal · Staff AI Scientist
# Prepared for HighLevel Technical Evaluation
# ==============================================================================

[CmdletBinding()]
param (
    [switch]$KeepInfra,
    [switch]$KeepFreeLlm
)

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Success ($text) { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Info ($text) { Write-Host "  [..] $text" -ForegroundColor DarkGray }
function Write-Warn ($text) { Write-Host "  [!] $text" -ForegroundColor Yellow }

Write-Host "`nStopping Clinical Coding Agent Evals Platform..." -ForegroundColor Cyan

# Resolve root workspace dir (e.g. E:\HighLevel) whether invoked from workspace root, HarnessComparison, or scripts/
$WorkspaceRoot = $ScriptDir
if (Test-Path (Join-Path $WorkspaceRoot "HarnessComparison")) {
    # Invoked from workspace root E:\HighLevel
} elseif (Test-Path (Join-Path (Split-Path -Parent $WorkspaceRoot) "HarnessComparison")) {
    $WorkspaceRoot = Split-Path -Parent $WorkspaceRoot
} elseif (Test-Path (Join-Path (Split-Path -Parent (Split-Path -Parent $WorkspaceRoot)) "HarnessComparison")) {
    $WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $WorkspaceRoot)
}

$PidFile = Join-Path $WorkspaceRoot ".platform_pids.json"
$HarnessDir = Join-Path $WorkspaceRoot "HarnessComparison"
$HarnessPy = Join-Path $HarnessDir ".venv\Scripts\python.exe"

# 1. Kill tracked PIDs
if (Test-Path $PidFile) {
    try {
        $json = Get-Content $PidFile -Raw | ConvertFrom-Json
        foreach ($prop in $json.PSObject.Properties) {
            if ($prop.Name -eq "freellmapi" -and $KeepFreeLlm) {
                Write-Info "Preserving FreeLLMAPI router per -KeepFreeLlm flag."
                continue
            }
            $pidToKill = $prop.Value
            if ($pidToKill) {
                Write-Info "Stopping $($prop.Name) (PID $pidToKill)..."
                Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Warn "Could not read $PidFile"
    }
    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

# 2. Kill by port ownership to guarantee clean release
$Ports = @(8080, 8081, 5173)
if (-not $KeepFreeLlm) {
    $Ports += 3001
}

foreach ($p in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        if ($procId -gt 0) {
            Write-Info "Releasing port $p (PID $procId)..."
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}
$stoppedServices = "API, Worker, Clinical Coder, UI" + $(if (-not $KeepFreeLlm) { ", FreeLLMAPI" } else { "" })
Write-Success "All application processes ($stoppedServices) terminated."

# 3. Stop Docker containers
if (-not $KeepInfra -and (Test-Path $HarnessPy)) {
    Write-Info "Stopping PostgreSQL and Redis infrastructure containers..."
    Push-Location $HarnessDir
    & $HarnessPy -m harness_compare.cli infra down 2>&1 | Out-Null
    Pop-Location
    Write-Success "Database and message bus containers stopped."
}

Write-Host "Platform shutdown complete.`n" -ForegroundColor Green
