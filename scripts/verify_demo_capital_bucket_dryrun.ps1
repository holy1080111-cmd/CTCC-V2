param(
    [decimal]$ExpectedBucketUsdt = 2000,
    [switch]$ExpectContinuousSession
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
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

Write-Host "Checking disabled-write 2,000 USDT Demo capital-bucket status..."
$status = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-automation/status" `
    -Headers $headers
$status | ConvertTo-Json -Depth 20

if ($status.armed -eq $true) {
    throw "Automation must be disarmed before this dry-run."
}
if ($status.running -eq $true) {
    throw "Automation scheduler must be stopped before this dry-run."
}
if ($status.demo_writes_enabled -eq $true) {
    throw "OKX Demo writes must remain disabled for capital-bucket verification."
}
if ($status.capability_enabled -eq $true) {
    throw "OKX Demo automation capability must remain disabled for this verification."
}
if ($status.capital_bucket_enabled -ne $true) {
    throw "OKX Demo capital-bucket sizing is not enabled."
}
if ([decimal]$status.capital_bucket_usdt -ne $ExpectedBucketUsdt) {
    throw "Unexpected capital bucket: $($status.capital_bucket_usdt)"
}
if ($ExpectContinuousSession) {
    if ($status.continuous_session_enabled -ne $true) {
        throw "Continuous Demo session is not enabled."
    }
    if ($status.daily_loss_limit_enforced -ne $false) {
        throw "Continuous Demo session still enforces the daily loss gate."
    }
    if ($status.daily_trade_limit_enforced -ne $false) {
        throw "Continuous Demo session still enforces the daily trade-count gate."
    }
    if ($status.consecutive_loss_limit_enforced -ne $false) {
        throw "Continuous Demo session still enforces the consecutive-loss gate."
    }
    if ([int]$status.effective_trade_cooldown_seconds -ne 0) {
        throw "Continuous Demo session must expose a zero effective cooldown."
    }
}

Write-Host "Running execute=false strategy and risk probe..."
$body = @{ execute = $false } | ConvertTo-Json
$result = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-automation/run-once" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
$result | ConvertTo-Json -Depth 30

if ($result.execute -eq $true) {
    throw "Safety failure: endpoint returned execute=true."
}
if ($result.capital_bucket_enabled -ne $true) {
    throw "Dry-run did not use capital-bucket sizing."
}
if ([decimal]$result.capital_bucket_usdt -ne $ExpectedBucketUsdt) {
    throw "Dry-run used an unexpected capital bucket."
}
if ($result.risk_equity_currency -ne "USDT") {
    throw "Capital buckets require the verified single-currency USDT equity basis."
}
if ($null -eq $result.capital_bucket_position_limit) {
    throw "Dry-run did not publish its effective capital-slot position limit."
}

$submitted = @($result.results | Where-Object { $_.outcome -eq "submitted" })
if ($submitted.Count -gt 0) {
    throw "Safety failure: execute=false reported a submitted order."
}

$riskEquity = [decimal]$result.risk_equity
$maximumPerPosition = $ExpectedBucketUsdt
if ($riskEquity -lt $ExpectedBucketUsdt) {
    $maximumPerPosition = $riskEquity
}

$sized = @(
    $result.results | Where-Object {
        $null -ne $_.estimated_margin -and $null -ne $_.position_margin_cap_usdt
    }
)
foreach ($item in $sized) {
    $margin = [decimal]$item.estimated_margin
    $cap = [decimal]$item.position_margin_cap_usdt
    if ($cap -le 0 -or $cap -gt $maximumPerPosition) {
        throw "Invalid per-position margin cap for $($item.instrument_id): $cap"
    }
    if ($margin -gt $cap) {
        throw "Estimated margin exceeds its capital bucket for $($item.instrument_id)."
    }
}

if ([int]$result.active_position_count -gt [int]$result.capital_bucket_position_limit) {
    throw "Dry-run shadow portfolio exceeded complete capital-slot count."
}
if ([decimal]$result.portfolio_estimated_margin -gt $riskEquity) {
    throw "Dry-run shadow portfolio margin exceeds verified USDT risk equity."
}

Write-Host "DEMO_CAPITAL_BUCKET_DRYRUN_VERIFIED=1"
Write-Host "BUCKET_USDT=$ExpectedBucketUsdt"
Write-Host "RISK_EQUITY_USDT=$riskEquity"
Write-Host "POSITION_LIMIT=$($result.capital_bucket_position_limit)"
Write-Host "SIZED_RESULT_COUNT=$($sized.Count)"
Write-Host "NO_ORDER_SUBMITTED=1"
if ($ExpectContinuousSession) {
    Write-Host "CONTINUOUS_SESSION_VERIFIED=1"
    Write-Host "DAILY_LOSS_LIMIT_ENFORCED=0"
}
