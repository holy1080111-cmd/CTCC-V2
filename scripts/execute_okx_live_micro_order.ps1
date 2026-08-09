param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Z0-9]+-[A-Z0-9]+-SWAP$')]
    [string]$InstrumentId,

    [Parameter(Mandatory = $true)]
    [ValidateSet("long", "short")]
    [string]$Direction,

    [Parameter(Mandatory = $true)]
    [decimal]$Size,

    [Parameter(Mandatory = $true)]
    [decimal]$StopLoss,

    [Parameter(Mandatory = $true)]
    [decimal]$TakeProfit,

    [ValidateRange(1, 3)]
    [int]$Leverage = 1,

    [ValidateRange(60, 300)]
    [int]$ArmSeconds = 120,

    [string]$BaseUrl = "http://127.0.0.1:8100"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-EnvValue([string]$Name) {
    $line = Get-Content -LiteralPath ".env" | Where-Object {
        $_ -match "^\s*$Name\s*="
    } | Select-Object -Last 1
    if (-not $line) { throw "$Name is missing from .env" }
    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Invoke-CtccPost([string]$Path, [hashtable]$Body) {
    return Invoke-RestMethod `
        -Method Post `
        -Uri "$BaseUrl$Path" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 8 -Compress)
}

$token = Read-EnvValue "API_TOKEN"
if ($token.Length -lt 32) { throw "API_TOKEN must contain at least 32 characters" }
$headers = @{ "X-CTCC-Token" = $token }

$status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/okx-live/status" -Headers $headers
if (-not $status.live_trading_enabled -or -not $status.writes_enabled) { throw "Live write capability is not enabled" }
if ($status.automation_enabled) { throw "Manual micro-order gate requires Live automation disabled" }
if ($status.arm.armed) { throw "Live service is already armed" }
if ($status.arm.emergency_stop) { throw "Emergency Stop is active" }

$snapshot = Invoke-CtccPost "/api/okx-live/reconcile" @{}
if ($snapshot.position_count -ne 0 -or $snapshot.pending_order_count -ne 0 -or $snapshot.pending_algo_order_count -ne 0) {
    throw "Real exchange exposure must be zero before the micro-order gate"
}

Write-Host "REAL-MONEY ORDER: $Direction $Size contracts of $InstrumentId at ${Leverage}x"
Write-Host "Stop loss: $StopLoss  Take profit: $TakeProfit"
$armPhrase = Read-Host "Type ARM_OKX_LIVE_REAL_MONEY"
if ($armPhrase -cne "ARM_OKX_LIVE_REAL_MONEY") { throw "Arm confirmation did not match" }
$orderPhrase = Read-Host "Type EXECUTE_OKX_LIVE_REAL_MONEY"
if ($orderPhrase -cne "EXECUTE_OKX_LIVE_REAL_MONEY") { throw "Order confirmation did not match" }

$clientOrderId = "CTCCL" + ([guid]::NewGuid().ToString("N").Substring(0, 27))
$armed = $false
try {
    $arm = Invoke-CtccPost "/api/okx-live/arm" @{
        duration_seconds = $ArmSeconds
        confirmation = $armPhrase
    }
    if (-not $arm.arm.armed) { throw "Live Arm was not established" }
    $armed = $true

    $result = Invoke-CtccPost "/api/okx-live/orders" @{
        instrument_id = $InstrumentId
        direction = $Direction
        size = $Size
        margin_mode = "cross"
        leverage = $Leverage
        order_type = "market"
        stop_loss = $StopLoss
        take_profit = $TakeProfit
        trigger_price_type = "mark"
        client_order_id = $clientOrderId
        confirmation = $orderPhrase
    }
    $armed = $false

    if (-not $result.accepted) { throw "OKX did not accept the real-money order" }
    if (-not $result.final_state_confirmed) {
        throw "Real-money order state or protection is not fully confirmed; inspect OKX immediately"
    }
    if (-not $result.order -or $result.order.state -ne "filled" -or [decimal]$result.order.accumulated_fill_size -le 0) {
        throw "The micro-order did not produce a confirmed fill; inspect OKX before continuing"
    }

    $after = Invoke-CtccPost "/api/okx-live/reconcile" @{}
    if ($after.position_count -gt 0 -and $after.pending_algo_order_count -eq 0) {
        throw "Live exposure exists without a reconciled protective Algo order; inspect OKX immediately"
    }
    [pscustomobject]@{
        client_order_id = $clientOrderId
        exchange_order_id = $result.acknowledgement.order_id
        accepted = $result.accepted
        final_state_confirmed = $result.final_state_confirmed
        order_state = $result.order.state
        accumulated_fill_size = $result.order.accumulated_fill_size
        warnings = @($result.warnings)
        position_count = $after.position_count
        pending_order_count = $after.pending_order_count
        pending_algo_order_count = $after.pending_algo_order_count
    } | ConvertTo-Json -Depth 6
}
catch {
    try {
        Invoke-CtccPost "/api/okx-live/emergency-stop" @{
            confirmation = "EMERGENCY_STOP_OKX_LIVE"
        } | Out-Null
    }
    catch {
        Write-Warning "Could not confirm Emergency Stop; inspect OKX immediately."
    }
    throw
}
finally {
    if ($armed) {
        try {
            Invoke-CtccPost "/api/okx-live/disarm" @{
                confirmation = "DISARM_OKX_LIVE"
            } | Out-Null
        }
        catch {
            Write-Warning "Could not confirm disarm; restart the single API process and inspect OKX."
        }
    }
}
