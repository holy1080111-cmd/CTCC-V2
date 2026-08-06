$ErrorActionPreference = "Stop"

$baseUrl = "http://127.0.0.1:8100"
$secureToken = Read-Host "Enter the local CTCC API token" -AsSecureString
$token = [System.Net.NetworkCredential]::new("", $secureToken).Password
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "CTCC API token is required."
}
$headers = @{ "X-CTCC-Token" = $token }

Write-Host "Reconciling before API restart..."
$before = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/okx-demo/reconcile" -Headers $headers

Write-Host "Restarting API container..."
docker compose restart api | Out-Host

$ready = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $null = Invoke-RestMethod "$baseUrl/liveness" -TimeoutSec 3
        $ready = $true
        break
    }
    catch {
        Write-Host "Waiting for API... ($attempt/30)"
    }
}
if (-not $ready) {
    throw "API did not become live after restart."
}

Write-Host "Reconciling exchange-authoritative state after restart..."
$after = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/okx-demo/reconcile" -Headers $headers
$status = Invoke-RestMethod "$baseUrl/api/okx-demo/status"

if (-not $after.persisted -or -not $status.local_mirror_available) {
    throw "Post-restart OKX Demo reconciliation did not persist successfully."
}

Write-Host ("Before: positions={0}, pending_orders={1}, algo_orders={2}" -f `
    $before.positions.Count, $before.pending_orders.Count, $before.pending_algo_orders.Count)
Write-Host ("After:  positions={0}, pending_orders={1}, algo_orders={2}" -f `
    $after.positions.Count, $after.pending_orders.Count, $after.pending_algo_orders.Count)
Write-Host "OKX Demo restart reconciliation passed. Exchange state was re-read after restart."
