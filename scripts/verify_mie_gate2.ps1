param(
    [string]$ComposeProjectName = "",
    [string]$ComposeEnvironmentFile = "",
    [string[]]$AdditionalComposeFiles = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceRoot = (
    Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
).Path
$composeArguments = @(
    "--project-directory",
    $sourceRoot,
    "--file",
    (Join-Path $sourceRoot "compose.yaml")
)
if (-not [string]::IsNullOrWhiteSpace($ComposeEnvironmentFile)) {
    $resolvedEnvironmentFile = (
        Resolve-Path -LiteralPath $ComposeEnvironmentFile
    ).Path
    $composeArguments = @(
        "--env-file",
        $resolvedEnvironmentFile
    ) + $composeArguments
}
foreach ($additionalComposeFile in $AdditionalComposeFiles) {
    $resolvedComposeFile = (
        Resolve-Path -LiteralPath $additionalComposeFile
    ).Path
    $composeArguments += @("--file", $resolvedComposeFile)
}
if (-not [string]::IsNullOrWhiteSpace($ComposeProjectName)) {
    $composeArguments = @(
        "--project-name",
        $ComposeProjectName.Trim()
    ) + $composeArguments
}
$apiContainerName = if (
    [string]::IsNullOrWhiteSpace($env:CTCC_API_CONTAINER_NAME)
) {
    "ctcc-v2-api"
}
else {
    $env:CTCC_API_CONTAINER_NAME.Trim()
}

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

Write-Host "== Host Compose execution-authority preflight =="
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$composeJson = ""
$composeExit = -1
try {
    $composeJson = (& docker compose @composeArguments config --format json | Out-String)
    $composeExit = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPreference
}
if ($composeExit -ne 0) {
    throw "Docker Compose configuration failed (exit=$composeExit)"
}
$composeConfiguration = $composeJson | ConvertFrom-Json -AsHashtable
$apiEnvironment = $composeConfiguration["services"]["api"]["environment"]
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
        if ($apiEnvironment.Contains($name) -and (Test-TruthyValue $apiEnvironment[$name])) {
            $name
        }
    }
)
if ($enabledAuthority.Count -ne 0) {
    throw "Disable execution authority before startup: $($enabledAuthority -join ', ')"
}
Write-Host "MIE_GATE2_HOST_EXECUTION_AUTHORITY_DISABLED=1"

Invoke-NativeStep "Docker build and start" {
    docker compose @composeArguments up -d --build
}

$deadline = (Get-Date).AddSeconds(120)
do {
    $health = (
        docker inspect `
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' `
            $apiContainerName 2>$null
    ).Trim()
    if ($health -eq "healthy") {
        break
    }
    if ((Get-Date) -ge $deadline) {
        docker compose @composeArguments ps api
        docker compose @composeArguments logs --tail 120 api
        throw "API did not become healthy (last status=$health)"
    }
    Start-Sleep -Seconds 2
} while ($true)

Invoke-NativeStep "MIE Gate 2 execution-authority preflight" {
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
    "before MIE Gate 2 verification"
)
print("MIE_GATE2_EXECUTION_AUTHORITY_DISABLED=1")
'@
    $authorityProbe | docker compose @composeArguments exec -T api python -
}

Invoke-NativeStep "Alembic heads" {
    docker compose @composeArguments exec -T api alembic heads
}
Invoke-NativeStep "Alembic current" {
    docker compose @composeArguments exec -T api alembic current
}
Invoke-NativeStep "Alembic exact revision" {
    $revisionProbe = @'
import subprocess

expected = "0016 (head)"
heads = subprocess.check_output(
    ["alembic", "heads"],
    text=True,
).strip()
current = subprocess.check_output(
    ["alembic", "current"],
    text=True,
).strip().splitlines()[-1]
assert heads == expected, (heads, expected)
assert current == expected, (current, expected)
print("ALEMBIC_REVISION=0016")
'@
    $revisionProbe | docker compose @composeArguments exec -T api python -
}
Invoke-NativeStep "Alembic schema drift" {
    docker compose @composeArguments exec -T api alembic check
}
Invoke-NativeStep "MIE Gate 2 targeted tests" {
    docker compose @composeArguments exec -T api python scripts/hermetic_pytest.py `
        -q -p no:cacheprovider `
        tests/unit/test_hermetic_pytest.py `
        tests/unit/mie `
        tests/integration/test_mie_shadow_contract_integration.py
}
Invoke-NativeStep "Full regression" {
    docker compose @composeArguments exec -T api python scripts/hermetic_pytest.py `
        -q -p no:cacheprovider
}
Invoke-NativeStep "Git whitespace check" {
    git -C $sourceRoot diff --check
    if ($LASTEXITCODE -ne 0) {
        throw "Git working-tree whitespace check failed"
    }
    git -C $sourceRoot diff --cached --check
}
Invoke-NativeStep "Canonical source manifest" {
    docker compose @composeArguments run --rm --no-deps `
        --volume "$($sourceRoot):/source:ro" `
        api python /source/scripts/manifest.py `
        --root /source `
        --manifest /source/MANIFEST.sha256 `
        --check
}

$head = (git -C $sourceRoot rev-parse HEAD).Trim()
$health = (
    docker inspect --format '{{.State.Health.Status}}' $apiContainerName
).Trim()
Write-Host "MIE_GATE2_VERIFIED=1"
Write-Host "MIE_GATE2_EXECUTION_AUTHORITY=0"
Write-Host "MIE_GATE2_RUNTIME_CONSUMERS=0"
Write-Host "HEAD=$head"
Write-Host "ALEMBIC_HEAD=0016"
Write-Host "API_HEALTH=$health"
