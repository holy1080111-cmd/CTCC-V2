$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8100"

function Convert-SecureStringToPlainText([Security.SecureString]$secure) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Get-Items($response) {
    if ($null -eq $response) { return @() }
    if ($response.PSObject.Properties.Name -contains "value") {
        return @($response.value) | Where-Object { $null -ne $_ }
    }
    return @($response) | Where-Object { $null -ne $_ }
}

$secureToken = Read-Host "Enter CTCC API_TOKEN" -AsSecureString
$token = Convert-SecureStringToPlainText $secureToken
$headers = @{ "X-CTCC-Token" = $token }

Write-Host "Checking observability summary..."
$summary = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-observability/summary" `
    -Headers $headers

if ($summary.recovered -ne $true) {
    throw "Observability recovery is not complete."
}

$beforePositions = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/positions" `
    -Headers $headers)
$beforeOrders = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/orders/pending" `
    -Headers $headers)
$beforeAlgos = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/algo-orders/pending" `
    -Headers $headers)

Write-Host "Starting one-run observation-only soak verification..."
$body = @{
    execute = $false
    duration_minutes = 1
    interval_seconds = 5
    max_runs = 1
    symbols = @("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    confirmation = "START_DEMO_SOAK_OBSERVE"
} | ConvertTo-Json

$started = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-observability/soak/start" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

if ($started.execute -ne $false -or $started.state -ne "running") {
    throw "Observation-only soak did not start safely."
}

$finished = $null
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Seconds 1
    $finished = Invoke-RestMethod `
        -Uri "$baseUrl/api/demo-observability/soak/status" `
        -Headers $headers
    if ($finished.state -ne "running") { break }
}

if ($null -eq $finished -or $finished.state -ne "completed") {
    throw "Observation soak did not complete. Current state: $($finished.state)"
}
if ($finished.completed_runs -ne 1) {
    throw "Expected exactly one completed run, got $($finished.completed_runs)."
}
if ($finished.execute -ne $false) {
    throw "Verification session unexpectedly used execute mode."
}

$afterPositions = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/positions" `
    -Headers $headers)
$afterOrders = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/orders/pending" `
    -Headers $headers)
$afterAlgos = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/algo-orders/pending" `
    -Headers $headers)

if (
    $afterPositions.Count -ne $beforePositions.Count -or
    $afterOrders.Count -ne $beforeOrders.Count -or
    $afterAlgos.Count -ne $beforeAlgos.Count
) {
    throw "Observation soak changed exchange exposure."
}

$metrics = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-observability/metrics?window_hours=24" `
    -Headers $headers

if ($metrics.total_runs -lt 1 -or $metrics.dry_runs -lt 1) {
    throw "Observability metrics did not record the dry run."
}

Write-Host "Demo observability verification passed."
Write-Host "Observation soak completed one run and did not change Demo exposure."
