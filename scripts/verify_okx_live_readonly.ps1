param(
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

$token = Read-EnvValue "API_TOKEN"
if ($token.Length -lt 32) { throw "API_TOKEN must contain at least 32 characters" }
$headers = @{ "X-CTCC-Token" = $token }

$initial = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/status" -Headers $headers
if (-not $initial.enabled) { throw "OKX Live read capability is disabled" }
if ($initial.trading_mode -ne "live") { throw "TRADING_MODE is not live" }
if ($initial.writes_enabled) { throw "Read-only verification requires Live writes disabled" }
if ($initial.automation_enabled) { throw "Read-only verification requires Live automation disabled" }
if ($initial.arm.armed) { throw "Live service is unexpectedly armed" }

$null = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/okx-live/connectivity-check" -Headers $headers
$beforePositions = @(Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/positions" -Headers $headers).Count
$beforeOrders = @(Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/orders/pending" -Headers $headers).Count
$beforeAlgo = @(Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/algo-orders/pending" -Headers $headers).Count

$reconcile = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/okx-live/reconcile" -Headers $headers
$final = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/status" -Headers $headers

if (-not $final.capability.read_permission) { throw "OKX API key lacks Read permission" }
if (-not $final.capability.trade_permission) { throw "OKX API key lacks Trade permission" }
if (-not $final.capability.ip_bound) { throw "OKX API key is not IP-bound" }
if ($final.capability.withdraw_permission) { throw "OKX API key has forbidden Withdraw permission" }
if (@($final.capability.unknown_permissions).Count -ne 0) { throw "Unknown OKX API permission detected" }
if (-not $reconcile.persisted) { throw "Live reconciliation was not persisted" }
if ($reconcile.position_count -ne $beforePositions) { throw "Position count changed during read-only verification" }
if ($reconcile.pending_order_count -ne $beforeOrders) { throw "Pending-order count changed during read-only verification" }
if ($reconcile.pending_algo_order_count -ne $beforeAlgo) { throw "Algo-order count changed during read-only verification" }

[pscustomobject]@{
    verified = $true
    read_permission = $final.capability.read_permission
    trade_permission = $final.capability.trade_permission
    ip_bound = $final.capability.ip_bound
    withdraw_permission = $final.capability.withdraw_permission
    position_count = $reconcile.position_count
    pending_order_count = $reconcile.pending_order_count
    pending_algo_order_count = $reconcile.pending_algo_order_count
    last_reconciled_at = $final.last_reconciled_at
} | ConvertTo-Json -Depth 5
