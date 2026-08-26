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

if ($null -eq $before.risk_equity -or [decimal]$before.risk_equity -le 0) {
    throw "Preflight did not resolve a positive Demo strategy risk equity."
}
if ([string]::IsNullOrWhiteSpace([string]$before.equity_basis) -or
    [string]::IsNullOrWhiteSpace([string]$before.equity_currency)) {
    throw "Preflight did not identify the Demo equity basis and currency."
}
if ([string]$before.execution_order_type -cne "fok") {
    throw "Preflight did not enforce bounded FOK execution."
}
if ($null -eq $before.execution_max_adverse_slippage_bps -or
    [decimal]$before.execution_max_adverse_slippage_bps -lt 0) {
    throw "Preflight did not expose a valid adverse-fill slippage ceiling."
}
if ($null -eq $before.minimum_execution_risk_reward -or
    [decimal]$before.minimum_execution_risk_reward -le 0) {
    throw "Preflight did not expose a positive execution reward/risk floor."
}

Start-Sleep -Seconds 1
$after = Invoke-RestMethod `
    -Uri "$baseUrl/api/demo-observability/soak/preflight" `
    -Headers $headers

if ($before.exchange_position_count -ne $after.exchange_position_count -or
    $before.exchange_pending_order_count -ne $after.exchange_pending_order_count -or
    $before.exchange_algo_order_count -ne $after.exchange_algo_order_count) {
    throw "Preflight verification changed or observed unstable exchange exposure."
}
if ($before.equity_basis -cne $after.equity_basis -or
    $before.equity_currency -cne $after.equity_currency) {
    throw "Preflight observed an unstable Demo equity identity."
}

Write-Host "Controlled Demo execution-soak preflight endpoint passed."
Write-Host "EQUITY_BASIS=$($before.equity_basis)"
Write-Host "EQUITY_CURRENCY=$($before.equity_currency)"
Write-Host "RISK_EQUITY=$($before.risk_equity)"
Write-Host "EXECUTION_ORDER_TYPE=$($before.execution_order_type)"
Write-Host "EXECUTION_MAX_ADVERSE_SLIPPAGE_BPS=$($before.execution_max_adverse_slippage_bps)"
Write-Host "MINIMUM_EXECUTION_RISK_REWARD=$($before.minimum_execution_risk_reward)"
Write-Host "No order was submitted and no automation state was changed."
