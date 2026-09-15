# Smoke test the built single-file exe.
#
#   powershell -ExecutionPolicy Bypass -File scripts\smoke_exe.ps1
#
# Starts dist\AutoFarmStation.exe, waits a few seconds, verifies the process is
# still alive (i.e. it did not crash on startup), then shuts it down cleanly.
# Exit code 0 = OK, 1 = the exe died early.

[CmdletBinding()]
param(
    [int]$WaitSeconds = 15,
    [string]$Exe = "dist\AutoFarmStation.exe"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot

$exePath = (Resolve-Path $Exe -ErrorAction SilentlyContinue)
if (-not $exePath) {
    Write-Host "[smoke] exe not found: $Exe" -ForegroundColor Red
    exit 1
}

$size = (Get-Item $exePath).Length / 1MB
Write-Host ("[smoke] launching {0} ({1:N1} MB) ..." -f $exePath, $size) -ForegroundColor Cyan

$proc = Start-Process -FilePath $exePath -PassThru
Start-Sleep -Seconds $WaitSeconds

if ($proc.HasExited) {
    Write-Host ("[smoke] FAIL: exe exited early, exit code = " + $proc.ExitCode) -ForegroundColor Red
    exit 1
}

Write-Host ("[smoke] OK: still running after {0}s (pid {1})" -f $WaitSeconds, $proc.Id) -ForegroundColor Green

# graceful shutdown, then force if needed
try { $proc.CloseMainWindow() | Out-Null } catch { }
Start-Sleep -Seconds 2
if (-not $proc.HasExited) {
    try { $proc.Kill() } catch { }
    $proc.WaitForExit(5000) | Out-Null
}
Write-Host "[smoke] done" -ForegroundColor Green
exit 0
