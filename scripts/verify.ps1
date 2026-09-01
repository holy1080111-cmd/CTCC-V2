$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$boundaryScript = Join-Path $PSScriptRoot "verify_v168_live_boundary.ps1"
& $boundaryScript
if (-not $?) { throw "v1.6.9 Live boundary verification failed" }

Write-Host "Checking liveness..."
Invoke-RestMethod http://127.0.0.1:8100/liveness | ConvertTo-Json

Write-Host "Checking readiness..."
Invoke-RestMethod http://127.0.0.1:8100/readiness | ConvertTo-Json -Depth 5

Write-Host "Checking capabilities..."
Invoke-RestMethod http://127.0.0.1:8100/api/capabilities | ConvertTo-Json -Depth 5

Write-Host "Checking persistence/recovery..."
Invoke-RestMethod http://127.0.0.1:8100/api/recovery/status | ConvertTo-Json -Depth 6

Write-Host "Checking OKX Demo safety status..."
Invoke-RestMethod http://127.0.0.1:8100/api/okx-demo/status | ConvertTo-Json -Depth 8

Write-Host "Checking Demo performance summary authentication separately with verify_demo_performance.ps1..."

Write-Host "CTCC V2 v1.6.9 local platform verification completed."
