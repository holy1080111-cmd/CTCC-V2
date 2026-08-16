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
    if ($exitCode -ne 0) {
        throw "$Name failed (exit=$exitCode)"
    }
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

Write-Host "== External benchmark execution-authority preflight =="
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
    throw "Disable execution authority before verification: $($enabledAuthority -join ', ')"
}
Write-Host "EXTERNAL_BENCHMARK_HOST_EXECUTION_AUTHORITY_DISABLED=1"

Invoke-NativeStep "Docker build and start" {
    docker compose up -d --build
}

$deadline = (Get-Date).AddSeconds(120)
do {
    $health = (
        docker inspect `
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' `
            ctcc-v2-api 2>$null
    ).Trim()
    if ($health -eq "healthy") {
        break
    }
    if ((Get-Date) -ge $deadline) {
        docker compose ps api
        docker compose logs --tail 120 api
        throw "API did not become healthy (last status=$health)"
    }
    Start-Sleep -Seconds 2
} while ($true)

Invoke-NativeStep "External benchmark authority boundary" {
    $probe = @'
from app.config.settings import get_settings
from app.research.external_benchmarks import REFERENCE_SOURCE_CATALOG

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
assert not active
assert all(
    source.reference_only
    and not source.execution_authority
    and source.terms_review_required
    for source in REFERENCE_SOURCE_CATALOG.values()
)
print("EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0")
'@
    $probe | docker compose exec -T api python -
}

Invoke-NativeStep "Alembic exact revision" {
    $probe = @'
import subprocess

expected = "0013 (head)"
heads = subprocess.check_output(["alembic", "heads"], text=True).strip()
current = subprocess.check_output(
    ["alembic", "current"], text=True
).strip().splitlines()[-1]
assert heads == expected, (heads, expected)
assert current == expected, (current, expected)
print("ALEMBIC_REVISION=0013")
'@
    $probe | docker compose exec -T api python -
}

Invoke-NativeStep "Alembic schema drift" {
    docker compose exec -T api alembic check
}

Invoke-NativeStep "External benchmark targeted tests" {
    docker compose exec -T api python scripts/hermetic_pytest.py `
        -q -p no:cacheprovider `
        tests/unit/research `
        tests/integration/test_external_benchmark_acquisition_flow.py `
        tests/integration/test_external_benchmark_reference_flow.py
}

Invoke-NativeStep "Full regression" {
    docker compose exec -T api python scripts/hermetic_pytest.py `
        -q -p no:cacheprovider
}

Invoke-NativeStep "Git whitespace check" {
    git diff --check
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    git diff --cached --check
}

Invoke-NativeStep "Canonical source manifest" {
    docker compose run --rm --no-deps `
        --volume "$($sourceRoot):/source:ro" `
        api python /source/scripts/manifest.py `
        --root /source `
        --manifest /source/MANIFEST.sha256 `
        --check
}

$head = (git rev-parse HEAD).Trim()
$health = (
    docker inspect --format '{{.State.Health.Status}}' ctcc-v2-api
).Trim()
Write-Host "EXTERNAL_BENCHMARK_PACK_V1_VERIFIED=1"
Write-Host "EXTERNAL_BENCHMARK_PACK_V2_VERIFIED=1"
Write-Host "EXTERNAL_BENCHMARK_RUNTIME_CONSUMERS=0"
Write-Host "EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0"
Write-Host "HEAD=$head"
Write-Host "ALEMBIC_HEAD=0013"
Write-Host "API_HEALTH=$health"
