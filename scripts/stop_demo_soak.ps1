$ErrorActionPreference = "Stop"
$secureToken = Read-Host "Enter CTCC API_TOKEN" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}

$body = @{ confirmation = "STOP_DEMO_SOAK" } | ConvertTo-Json
Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8100/api/demo-observability/soak/stop" `
    -Headers @{ "X-CTCC-Token" = $token } `
    -ContentType "application/json" `
    -Body $body |
ConvertTo-Json -Depth 10
