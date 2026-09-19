$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$sourceRoot = (Get-Location).Path

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    Write-Host "== $Name =="
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = @()
    $exitCode = -1
    try {
        $output = & $Command 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $output | ForEach-Object { Write-Host $_ }
    if ($exitCode -ne 0) { throw "$Name failed (exit=$exitCode)" }
}

function Test-TruthyValue {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return $false
    }
    return @("1", "true", "yes", "on") -contains (
        "$Value".Trim().ToLowerInvariant()
    )
}

Write-Host "== Host Compose execution-authority preflight =="
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$composeJson = ""
$composeExit = -1
try {
    $composeJson = (& docker compose config --format json | Out-String)
    $composeExit = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPreference
}
if ($composeExit -ne 0) {
    throw "Docker Compose configuration failed (exit=$composeExit)"
}
$composeConfiguration = $composeJson | ConvertFrom-Json
$apiEnvironment = $composeConfiguration.services.api.environment
if ($null -eq $apiEnvironment) {
    throw "Docker Compose api environment is unavailable"
}
$authorityNames = @(
    "AUTO_TRADE",
    "PAPER_AUTO_EXECUTION",
    "LIVE_TRADING",
    "OKX_LIVE_ALLOW_ORDER_WRITES",
    "OKX_LIVE_AUTO_EXECUTION",
    "OKX_DEMO_ALLOW_ORDER_WRITES",
    "OKX_DEMO_AUTO_EXECUTION",
    "OKX_DEMO_SOAK_ALLOW_EXECUTE"
)
$enabledAuthority = @(
    foreach ($name in $authorityNames) {
        $property = $apiEnvironment.PSObject.Properties[$name]
        if ($null -ne $property -and (Test-TruthyValue $property.Value)) {
            $name
        }
    }
)
if ($enabledAuthority.Count -ne 0) {
    throw "Disable execution authority before startup: $($enabledAuthority -join ', ')"
}
Write-Host "V168_HOST_EXECUTION_AUTHORITY_DISABLED=1"

Invoke-NativeStep "Docker build and start" {
    docker compose up -d --build
}

$deadline = (Get-Date).AddSeconds(120)
do {
    $health = (docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' ctcc-v2-api 2>$null).Trim()
    if ($health -eq "healthy") { break }
    if ((Get-Date) -ge $deadline) {
        docker compose ps api
        docker compose logs --tail 120 api
        throw "API did not become healthy (last status=$health)"
    }
    Start-Sleep -Seconds 2
} while ($true)

Invoke-NativeStep "Verification safety preflight" {
    $authorityProbe = @'
from app.config.settings import get_settings

settings = get_settings()
active = any(
    (
        settings.auto_trade,
        settings.paper_auto_execution,
        settings.live_trading,
        settings.okx_live_allow_order_writes,
        settings.okx_live_auto_execution,
        settings.okx_demo_allow_order_writes,
        settings.okx_demo_auto_execution,
        settings.okx_demo_soak_allow_execute,
    )
)
assert not active, (
    "Disable every Paper, Demo, and Live execution-authority switch "
    "before running the regression suite"
)
print("REGRESSION_WRITE_AUTHORITY_DISABLED=1")
'@
    $authorityProbe | docker compose exec -T api python -
}

$script:verifiedMigrationHead = $null
Invoke-NativeStep "Alembic source/database identity" {
    $identityLines = @(docker compose exec -T api python -m scripts.verify_migration_identity)
    $identityExit = $LASTEXITCODE
    $identityLines | ForEach-Object { Write-Host $_ }
    if ($identityExit -ne 0) {
        throw "Migration identity read failed (exit=$identityExit)"
    }
    $identity = ($identityLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if (
        $identity.schema -cne "ctcc.migration_identity.v1" -or
        $identity.status -cne "PASS" -or
        $identity.source_head -isnot [string] -or
        $identity.database_head -isnot [string] -or
        $identity.database_head -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$' -or
        $identity.source_head -cne $identity.database_head -or
        $identity.migration_sha256 -isnot [string] -or
        $identity.migration_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        $identity.execution_authority -isnot [bool] -or
        $identity.execution_authority -ne $false
    ) {
        throw "Migration identity readback invalid"
    }
    $script:verifiedMigrationHead = $identity.database_head
}
Invoke-NativeStep "Alembic schema drift" {
    docker compose exec -T api alembic check
}
Invoke-NativeStep "Live boundary targeted tests" {
    docker compose exec -T api python scripts/hermetic_pytest.py -q -p no:cacheprovider `
        tests/unit/test_hermetic_pytest.py `
        tests/unit/test_settings.py `
        tests/unit/exchange/test_live_private_rest.py `
        tests/unit/exchange/test_live_execution_rest.py `
        tests/unit/exchange/test_live_private_parsers.py `
        tests/unit/database/test_okx_live_models.py `
        tests/unit/database/test_okx_live_repository.py `
        tests/unit/database/test_okx_live_execution_repository.py `
        tests/unit/test_okx_live_service.py `
        tests/unit/test_okx_live_automation.py `
        tests/integration/test_okx_live_schema_integration.py `
        tests/integration/test_okx_live_repository_integration.py `
        tests/integration/test_okx_live_execution_repository_integration.py
}
Invoke-NativeStep "Adaptive Demo portfolio targeted tests" {
    docker compose exec -T api python scripts/hermetic_pytest.py -q -p no:cacheprovider `
        tests/unit/exchange/test_symbols.py `
        tests/unit/exchange/test_parsers.py `
        tests/unit/indicators/test_causal_trend.py `
        tests/unit/indicators/test_causal_state.py `
        tests/unit/indicators/test_conformal_return.py `
        tests/unit/analysis/test_service.py `
        tests/unit/analysis/test_mathematical_core.py `
        tests/unit/strategies/test_derivative_confirmation.py `
        tests/unit/strategies/test_mathematical_confirmation.py `
        tests/unit/strategies/test_service_mathematical_gate.py `
        tests/unit/strategies/test_structural_protection.py `
        tests/unit/test_demo_capital_bucket.py `
        tests/unit/test_demo_automation_risk_profile.py `
        tests/unit/test_demo_execution_quality.py `
        tests/unit/test_demo_structural_risk.py `
        tests/unit/test_risk_engine.py `
        tests/unit/test_demo_automation.py `
        tests/unit/test_observability.py `
        tests/unit/test_okx_demo_models.py `
        tests/unit/test_okx_demo_service.py `
        tests/unit/database/test_demo_adaptive_portfolio_models.py `
        tests/integration/test_demo_adaptive_portfolio_schema_integration.py
}
Invoke-NativeStep "External benchmark pack targeted tests" {
    docker compose exec -T api python scripts/hermetic_pytest.py `
        -q -p no:cacheprovider `
        tests/unit/research `
        tests/integration/test_binance_reference_batch_flow.py `
        tests/integration/test_binance_reference_probe_flow.py `
        tests/integration/test_external_benchmark_acquisition_flow.py `
        tests/integration/test_external_benchmark_reference_flow.py
}
Invoke-NativeStep "Full regression" {
    docker compose exec -T api python scripts/hermetic_pytest.py -q -p no:cacheprovider
}
Invoke-NativeStep "Git whitespace check" {
    git diff --check
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    git diff --cached --check
}

Invoke-NativeStep "Canonical source manifest" {
    # Verify the actual COPY output, without exposing the host .env or source
    # checkout to the container or substituting a host-mounted manifest.
    docker compose exec -T api python scripts/manifest.py --check
}

$head = (git rev-parse HEAD).Trim()
$health = (docker inspect --format '{{.State.Health.Status}}' ctcc-v2-api).Trim()
Write-Host "V168_LIVE_BOUNDARY_VERIFIED=1"
Write-Host "HEAD=$head"
Write-Host ("ALEMBIC_HEAD={0}" -f $script:verifiedMigrationHead)
Write-Host "API_HEALTH=$health"
