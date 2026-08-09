param(
    [string[]]$Symbols = @("BTC-USDT-SWAP"),
    [ValidateRange(60, 300)]
    [int]$ArmSeconds = 120,
    [string]$BaseUrl = "http://127.0.0.1:8100"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-EnvValue([string]$Name) {
    $line = Get-Content -LiteralPath ".env" | Where-Object {
        $_ -match "^\s*$Name\s*="
    } | Select-Object -Last 1
    if (-not $line) { throw "$Name is missing from .env" }
    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Invoke-CtccPost([string]$Path, [hashtable]$Body) {
    return Invoke-RestMethod `
        -Method Post `
        -Uri "$BaseUrl$Path" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8 -Compress)
}

$token = Read-EnvValue "API_TOKEN"
if ($token.Length -lt 32) { throw "API_TOKEN must contain at least 32 characters" }
$headers = @{ "X-CTCC-Token" = $token }

$status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/status" -Headers $headers
if (-not $status.automation_enabled) { throw "OKX Live automation capability is disabled" }
if ($status.arm.armed -or $status.arm.emergency_stop) { throw "Live service is already armed or stopped" }

$dryRun = Invoke-CtccPost "/api/okx-live/automation/run-once" @{
    symbols = @($Symbols)
    execute = $false
}
$dryRun | ConvertTo-Json -Depth 8
if (@($dryRun.results | Where-Object { $_.outcome -eq "approved_dry_run" }).Count -eq 0) {
    throw "No strategy candidate passed the dry-run gates"
}

$armPhrase = Read-Host "Type ARM_OKX_LIVE_REAL_MONEY"
if ($armPhrase -cne "ARM_OKX_LIVE_REAL_MONEY") { throw "Arm confirmation did not match" }
$executePhrase = Read-Host "Type EXECUTE_OKX_LIVE_AUTOMATION"
if ($executePhrase -cne "EXECUTE_OKX_LIVE_AUTOMATION") { throw "Automation confirmation did not match" }

$armed = $false
try {
    $arm = Invoke-CtccPost "/api/okx-live/arm" @{
        duration_seconds = $ArmSeconds
        confirmation = $armPhrase
    }
    if (-not $arm.arm.armed) { throw "Live Arm was not established" }
    $armed = $true

    $run = Invoke-CtccPost "/api/okx-live/automation/run-once" @{
        symbols = @($Symbols)
        execute = $true
        confirmation = $executePhrase
    }
    if (@($run.results | Where-Object { $_.outcome -eq "submitted" }).Count -gt 0) {
        $armed = $false
    }
    $run | ConvertTo-Json -Depth 8
}
finally {
    if ($armed) {
        Invoke-CtccPost "/api/okx-live/disarm" @{
            confirmation = "DISARM_OKX_LIVE"
        } | Out-Null
    }
}
