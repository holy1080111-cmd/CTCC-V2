$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8100"

$secureToken = Read-Host "Enter CTCC API_TOKEN" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$headers = @{ "X-CTCC-Token" = $token }

Write-Host "Checking controlled Demo execution-soak preflight..."
$before = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-observability/soak/preflight" `
    -Headers $headers

$before | ConvertTo-Json -Depth 12

Start-Sleep -Seconds 1
$after = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-observability/soak/preflight" `
    -Headers $headers

if ($before.exchange_position_count -ne $after.exchange_position_count -or
    $before.exchange_pending_order_count -ne $after.exchange_pending_order_count -or
    $before.exchange_algo_order_count -ne $after.exchange_algo_order_count) {
    throw "Preflight verification changed or observed unstable exchange exposure."
}

Write-Host "Controlled Demo execution-soak preflight endpoint passed."
Write-Host "No order was submitted and no automation state was changed."
