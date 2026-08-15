$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8100"

$secureToken = Read-Host "輸入 CTCC API_TOKEN" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
$headers = @{ "X-CTCC-Token" = $token }
$expectedSymbols = @(
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
    "LINK-USDT-SWAP",
    "LTC-USDT-SWAP"
)

Write-Host "Checking v1.2 Demo automation status..."
$status = Invoke-RestMethod -Uri "$baseUrl/api/demo-automation/status" -Headers $headers
$status | ConvertTo-Json -Depth 10

if ($status.armed -eq $true) {
    throw "Automation is already armed. Disarm it before running the installation dry-run."
}
if ($status.running -eq $true) {
    throw "Automation scheduler is already running."
}
$configuredSymbols = @($status.symbols)
if (
    ($configuredSymbols.Count -ne $expectedSymbols.Count) -or
    (($configuredSymbols -join ",") -cne ($expectedSymbols -join ","))
) {
    throw (
        "Demo automation does not expose the reviewed eight-symbol universe: " +
        ($configuredSymbols -join ",")
    )
}

Write-Host "Running strategy and risk dry-run. No order can be submitted..."
$body = @{ execute = $false } | ConvertTo-Json
$result = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-automation/run-once" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

$result | ConvertTo-Json -Depth 20

$submitted = @($result.results | Where-Object { $_.outcome -eq "submitted" })
if ($submitted.Count -gt 0) {
    throw "Safety failure: dry-run reported a submitted order."
}

$resultSymbols = @(
    $result.results |
        Where-Object { $null -ne $_.instrument_id } |
        ForEach-Object { [string]$_.instrument_id } |
        Sort-Object -Unique
)
$missingSymbols = @(
    $expectedSymbols | Where-Object { $_ -notin $resultSymbols }
)
$unexpectedSymbols = @(
    $resultSymbols | Where-Object { $_ -notin $expectedSymbols }
)
if ($missingSymbols.Count -ne 0 -or $unexpectedSymbols.Count -ne 0) {
    throw (
        "Dry-run did not evaluate the exact reviewed universe; missing=" +
        ($missingSymbols -join ",") + "; unexpected=" +
        ($unexpectedSymbols -join ",")
    )
}

Write-Host "Safe Demo automation dry-run passed. No order was submitted."
Write-Host "DEMO_UNIVERSE_DRYRUN_VERIFIED=1"
