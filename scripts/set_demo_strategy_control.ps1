param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "trend_pullback",
        "breakout_continuation",
        "liquidity_sweep_reversal",
        "fvg_return",
        "order_block_return",
        "range_reversal",
        "structure_reversal",
        "volatility_expansion"
    )]
    [string]$Strategy,

    [Parameter(Mandatory = $true)]
    [ValidateSet("enable", "disable")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [ValidateLength(3, 250)]
    [string]$Reason
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8100"

$secureToken = Read-Host "Enter CTCC API_TOKEN" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
$headers = @{ "X-CTCC-Token" = $token }

if ($Action -eq "disable") {
    $expected = "DISABLE_DEMO_STRATEGY"
    $endpoint = "disable"
}
else {
    $expected = "ENABLE_DEMO_STRATEGY"
    $endpoint = "enable"
}

$confirmation = Read-Host "Type $expected"
if ($confirmation -cne $expected) {
    throw "Confirmation text did not match. Strategy state was not changed."
}

$body = @{
    confirmation = $expected
    reason = $Reason
    actor = "operator"
} | ConvertTo-Json

$result = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-performance/strategies/$Strategy/$endpoint" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

$result | ConvertTo-Json -Depth 10
Write-Host "Strategy control updated."
Write-Host "This does not close or modify an already-open Demo position."
