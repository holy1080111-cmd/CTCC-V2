$ErrorActionPreference = "Stop"

$baseUrl = "http://127.0.0.1:8100"
$secureToken = Read-Host "Enter the local CTCC API token" -AsSecureString
$token = [System.Net.NetworkCredential]::new("", $secureToken).Password
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "CTCC API token is required."
}
$headers = @{ "X-CTCC-Token" = $token }

Write-Host "Checking public Demo status..."
$status = Invoke-RestMethod "$baseUrl/api/okx-demo/status"
$status | ConvertTo-Json -Depth 8

if (-not $status.enabled) {
    throw "OKX Demo is disabled. Set OKX_DEMO_ENABLED=true."
}
if ($status.trading_mode -ne "okx_demo") {
    throw "TRADING_MODE must be okx_demo."
}
if (-not $status.credentials_configured) {
    throw "OKX Demo credentials are missing."
}
if ($status.writes_enabled) {
    throw "Read-only verification requires OKX_DEMO_ALLOW_ORDER_WRITES=false."
}
if ($status.simulated_trading_header -ne "1") {
    throw "Simulated-trading safety header is not active."
}

Write-Host "Checking authenticated Demo account connectivity..."
$connectivity = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/okx-demo/connectivity-check" `
    -Headers $headers
$connectivity | ConvertTo-Json -Depth 8

Write-Host "Reconciling Demo exchange state into PostgreSQL..."
$reconcile = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/okx-demo/reconcile" `
    -Headers $headers
$reconcile | ConvertTo-Json -Depth 12

if (-not $reconcile.persisted) {
    throw "Exchange read succeeded, but PostgreSQL mirror was not persisted."
}

$statusAfter = Invoke-RestMethod "$baseUrl/api/okx-demo/status"
if (-not $statusAfter.local_mirror_available) {
    throw "OKX Demo PostgreSQL mirror is unavailable."
}

Write-Host "OKX Demo read-only verification passed. No order was submitted."
