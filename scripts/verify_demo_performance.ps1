param(
    [ValidateRange(1, 365)]
    [int]$WindowDays = 30
)

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

$beforePositions = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/positions" `
    -Headers $headers)
$beforeOrders = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/orders/pending" `
    -Headers $headers)
$beforeAlgos = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/okx-demo/algo-orders/pending" `
    -Headers $headers)

Write-Host "Capturing one read-only Demo performance snapshot..."
$snapshot = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-performance/snapshot/capture" `
    -Headers $headers

if ($null -eq $snapshot.total_equity) {
    throw "Performance snapshot did not return total_equity."
}

Write-Host "Reading performance summary and reliability validation..."
$summary = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-performance/summary?window_days=$WindowDays" `
    -Headers $headers
$validation = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-performance/validation?window_days=$WindowDays" `
    -Headers $headers
$strategies = Get-Items (Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-performance/strategies" `
    -Headers $headers)

if ([int]$summary.window_days -ne $WindowDays) {
    throw "Performance summary window mismatch."
}
if ($strategies.Count -ne 8) {
    throw "Expected 8 strategy controls, got $($strategies.Count)."
}
if ($validation.PSObject.Properties.Name -notcontains "reliability_ready") {
    throw "Reliability validation response is incomplete."
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
    throw "Read-only performance verification changed Demo exchange exposure."
}

Write-Host "Demo performance verification passed."
Write-Host "Snapshot, summary, validation, and strategy controls are readable."
Write-Host "No Demo position, pending order, or Algo-order count changed."
Write-Host "Reliability ready: $($validation.reliability_ready)"
