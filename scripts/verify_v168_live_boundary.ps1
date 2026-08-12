$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$sourceRoot = (Get-Location).Path
$testEnvironment = @(
    "-e", "ENVIRONMENT=test",
    "-e", "TRADING_MODE=analysis_only",
    "-e", "AUTO_TRADE=false",
    "-e", "LIVE_TRADING=false",
    "-e", "PAPER_AUTO_EXECUTION=false",
    "-e", "OKX_LIVE_ENABLED=false",
    "-e", "OKX_LIVE_ALLOW_ORDER_WRITES=false",
    "-e", "OKX_LIVE_AUTO_RECONCILE_ON_START=false",
    "-e", "OKX_LIVE_AUTO_EXECUTION=false",
    "-e", "OKX_DEMO_ALLOW_ORDER_WRITES=false",
    "-e", "OKX_DEMO_AUTO_EXECUTION=false",
    "-e", "OKX_DEMO_SCORE_RISK_ENABLED=false",
    "-e", "OKX_DEMO_CAPITAL_BUCKET_ENABLED=false",
    "-e", "OKX_DEMO_CONTINUOUS_SESSION_ENABLED=false",
    "-e", "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED=false",
    "-e", "OKX_DEMO_SOAK_ALLOW_EXECUTE=false"
)

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    Write-Host "== $Name =="
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
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
    docker compose exec -T api python -c 'from app.config.settings import get_settings; s = get_settings(); active = any((s.auto_trade, s.paper_auto_execution, s.live_trading, s.okx_live_allow_order_writes, s.okx_live_auto_execution, s.okx_demo_allow_order_writes, s.okx_demo_auto_execution, s.okx_demo_score_risk_enabled, s.okx_demo_capital_bucket_enabled, s.okx_demo_continuous_session_enabled, s.okx_demo_structural_dynamic_leverage_enabled, s.okx_demo_soak_allow_execute)); assert not active, "Disable every Paper, Demo, and Live write, automation, score-risk, capital-bucket, continuous-session, and structural-risk switch before running the regression suite"; print("REGRESSION_WRITE_FLAGS_DISABLED=1")'
}

Invoke-NativeStep "Alembic heads" {
    docker compose exec -T api alembic heads
}
Invoke-NativeStep "Alembic current" {
    docker compose exec -T api alembic current
}
Invoke-NativeStep "Alembic schema drift" {
    docker compose exec -T api alembic check
}
Invoke-NativeStep "Live boundary targeted tests" {
    docker compose exec -T @testEnvironment api python -m pytest -q -p no:cacheprovider `
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
    docker compose exec -T @testEnvironment api python -m pytest -q -p no:cacheprovider `
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
        tests/unit/test_demo_structural_risk.py `
        tests/unit/test_risk_engine.py `
        tests/unit/test_demo_automation.py `
        tests/unit/test_observability.py `
        tests/unit/database/test_demo_adaptive_portfolio_models.py `
        tests/integration/test_demo_adaptive_portfolio_schema_integration.py
}
Invoke-NativeStep "Full regression" {
    docker compose exec -T @testEnvironment api python -m pytest -q -p no:cacheprovider
}
Invoke-NativeStep "Git whitespace check" {
    git diff --check
}

Invoke-NativeStep "Canonical source manifest" {
    docker compose run --rm --no-deps `
        --volume "${sourceRoot}:/source:ro" `
        api python /source/scripts/manifest.py `
        --root /source `
        --manifest /source/MANIFEST.sha256 `
        --check
}

$head = (git rev-parse HEAD).Trim()
$health = (docker inspect --format '{{.State.Health.Status}}' ctcc-v2-api).Trim()
Write-Host "V168_LIVE_BOUNDARY_VERIFIED=1"
Write-Host "HEAD=$head"
Write-Host "ALEMBIC_HEAD=0013"
Write-Host "API_HEALTH=$health"
