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

$stopBody = @{ confirmation = "STOP_DEMO_SOAK" } | ConvertTo-Json
$status = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-observability/soak/stop" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $stopBody

$status | ConvertTo-Json -Depth 15
Write-Host "Soak stopped. Existing exchange exposure was not closed automatically."
Write-Host "Review OKX Demo positions and protection orders before any further action."
