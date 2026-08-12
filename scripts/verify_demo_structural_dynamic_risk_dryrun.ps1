param(
    [decimal]$ExpectedBucketUsdt = 2000,
    [decimal]$MinimumNetRiskReward = 2.0
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

Write-Host "Checking disabled-write structural dynamic-risk status..."
$status = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-automation/status" `
    -Headers $headers
$status | ConvertTo-Json -Depth 30

if ($status.armed -eq $true -or $status.running -eq $true) {
    throw "Automation must be disarmed and stopped for this dry-run."
}
if ($status.demo_writes_enabled -eq $true -or $status.capability_enabled -eq $true) {
    throw "Demo writes and automatic execution must remain disabled."
}
if ($status.structural_dynamic_leverage_enabled -ne $true) {
    throw "Structural dynamic leverage is not enabled for read-only verification."
}
if ($status.structural_margin_mode -ne "isolated") {
    throw "Structural risk must publish isolated margin mode."
}
if ($status.continuous_session_enabled -ne $true) {
    throw "The integrated profile requires continuous Demo session mode."
}
if ($status.capital_bucket_enabled -ne $true) {
    throw "The integrated profile requires capital buckets."
}
if ([decimal]$status.capital_bucket_usdt -ne $ExpectedBucketUsdt) {
    throw "Unexpected capital bucket: $($status.capital_bucket_usdt)"
}
if ([decimal]$status.structural_min_net_risk_reward -lt $MinimumNetRiskReward) {
    throw "Configured structural net RR is below the verification floor."
}

$tiers = @($status.score_risk_tiers)
$tierNames = @($tiers | ForEach-Object { $_.name }) -join ","
if ($tierNames -ne "low,medium,high,elite,extreme") {
    throw "Unexpected structural score tiers: $tierNames"
}
if ([int]$tiers[-1].leverage -ne 20) {
    throw "Extreme structural tier does not expose a 20x ceiling."
}

Write-Host "Running execute=false structural strategy/risk probe..."
$body = @{ execute = $false } | ConvertTo-Json
$result = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/demo-automation/run-once" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
$result | ConvertTo-Json -Depth 40

if ($result.execute -eq $true) {
    throw "Safety failure: endpoint returned execute=true."
}
$submitted = @($result.results | Where-Object { $_.outcome -eq "submitted" })
if ($submitted.Count -ne 0) {
    throw "Safety failure: execute=false reported a submitted order."
}

$ladder = @(3, 5, 8, 10, 20)
$approved = @(
    $result.results | Where-Object { $_.outcome -eq "approved_dry_run" }
)
foreach ($item in $approved) {
    if ($item.protection_model -ne "structure") {
        throw "Approved result lacks structural protection: $($item.instrument_id)"
    }
    if ($item.margin_mode -ne "isolated") {
        throw "Approved result is not isolated: $($item.instrument_id)"
    }
    if ($ladder -notcontains [int]$item.selected_leverage) {
        throw "Approved result selected leverage outside the ladder."
    }
    if ([decimal]$item.net_risk_reward -lt $MinimumNetRiskReward) {
        throw "Approved result has insufficient cost-adjusted net RR."
    }
    if ([decimal]$item.estimated_round_trip_cost_pct -le 0) {
        throw "Approved result omitted estimated execution costs."
    }
    if ([decimal]$item.estimated_margin -gt [decimal]$item.position_margin_cap_usdt) {
        throw "Approved result exceeds its capital bucket."
    }
    if ([int]$item.selected_leverage -eq 20 -and @($item.leverage_cap_reasons).Count -ne 0) {
        throw "20x result retained unresolved cap reasons."
    }
}

Write-Host "DEMO_STRUCTURAL_DYNAMIC_RISK_DRYRUN_VERIFIED=1"
Write-Host "APPROVED_RESULT_COUNT=$($approved.Count)"
Write-Host "NO_ORDER_SUBMITTED=1"
Write-Host "ISOLATED_MARGIN_REQUIRED=1"
Write-Host "COST_ADJUSTED_NET_RR_MINIMUM=$MinimumNetRiskReward"
