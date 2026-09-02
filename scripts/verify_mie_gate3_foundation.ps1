param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceRoot = (
    Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
).Path
$safeProfile = Join-Path $sourceRoot "config/mie_gate3_offline.env.example"
$gate3ComposeFile = Join-Path $sourceRoot "config/mie_gate3.compose.yaml"
foreach ($requiredFile in @($safeProfile, $gate3ComposeFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "MIE Gate 3 verifier input is missing: $requiredFile"
    }
}

$verificationSuffix = (
    [Guid]::NewGuid().ToString("N")
).Substring(0, 12)
$projectName = "ctcc-v2-gate3-$verificationSuffix"
$managedEnvironmentNames = @(
    "CTCC_ENV_FILE",
    "CTCC_API_CONTAINER_NAME",
    "CTCC_POSTGRES_CONTAINER_NAME",
    "CTCC_REDIS_CONTAINER_NAME"
)
$savedEnvironment = @{}
foreach ($name in $managedEnvironmentNames) {
    $savedEnvironment[$name] = [pscustomobject]@{
        Exists = Test-Path -LiteralPath "Env:$name"
        Value = [Environment]::GetEnvironmentVariable(
            $name,
            [EnvironmentVariableTarget]::Process
        )
    }
}

try {
$env:CTCC_ENV_FILE = $safeProfile
$env:CTCC_API_CONTAINER_NAME = "ctcc-v2-gate3-api-$verificationSuffix"
$env:CTCC_POSTGRES_CONTAINER_NAME = "ctcc-v2-gate3-postgres-$verificationSuffix"
$env:CTCC_REDIS_CONTAINER_NAME = "ctcc-v2-gate3-redis-$verificationSuffix"

$composeArguments = @(
    "--project-name",
    $projectName,
    "--project-directory",
    $sourceRoot,
    "--env-file",
    $safeProfile,
    "--file",
    (Join-Path $sourceRoot "compose.yaml"),
    "--file",
    $gate3ComposeFile
)

function Test-TruthyValue {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return $false
    }
    return @("1", "true", "yes", "on") -contains (
        "$Value".Trim().ToLowerInvariant()
    )
}

function Get-RequiredMapValue {
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Map,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not $Map.Contains($Name)) {
        throw "Rendered Compose configuration is missing $Name"
    }
    return $Map[$Name]
}

Write-Host "== MIE Gate 3 isolated offline Compose preflight =="
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$composeJson = ""
$composeExit = -1
try {
    $composeJson = (
        & docker compose @composeArguments config --format json | Out-String
    )
    $composeExit = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPreference
}
if ($composeExit -ne 0) {
    throw "Docker Compose configuration failed (exit=$composeExit)"
}

$composeConfiguration = $composeJson | ConvertFrom-Json -AsHashtable
$apiService = $composeConfiguration["services"]["api"]
$postgresService = $composeConfiguration["services"]["postgres"]
$apiEnvironment = $apiService["environment"]
if ($null -eq $apiEnvironment) {
    throw "Docker Compose api environment is unavailable"
}

$expectedContainerNames = @{
    api = $env:CTCC_API_CONTAINER_NAME
    postgres = $env:CTCC_POSTGRES_CONTAINER_NAME
    redis = $env:CTCC_REDIS_CONTAINER_NAME
}
foreach ($serviceName in $expectedContainerNames.Keys) {
    $renderedName = $composeConfiguration["services"][$serviceName]["container_name"]
    if ($renderedName -ne $expectedContainerNames[$serviceName]) {
        throw "Gate 3 container isolation failed for $serviceName"
    }
}
if ($apiService.Contains("ports")) {
    $renderedPorts = $apiService["ports"]
    if ($null -ne $renderedPorts -and @($renderedPorts).Count -ne 0) {
        throw "MIE Gate 3 API must not publish a host port"
    }
}

$defaultNetwork = $composeConfiguration["networks"]["default"]
if (
    $null -eq $defaultNetwork -or
    -not (Test-TruthyValue (Get-RequiredMapValue -Map $defaultNetwork -Name "internal"))
) {
    throw "MIE Gate 3 runtime network must be internal"
}
if ($defaultNetwork["name"] -ne "${projectName}_default") {
    throw "MIE Gate 3 runtime network is not project-isolated"
}

$expectedVolumeNames = @(
    "ctcc_v2_postgres_data",
    "ctcc_v2_redis_data"
)
foreach ($volumeName in $expectedVolumeNames) {
    $volume = Get-RequiredMapValue `
        -Map $composeConfiguration["volumes"] `
        -Name $volumeName
    if ($volume["name"] -ne "${projectName}_${volumeName}") {
        throw "MIE Gate 3 volume is not project-isolated: $volumeName"
    }
    if ($volume.Contains("external") -and (Test-TruthyValue $volume["external"])) {
        throw "MIE Gate 3 volume must not be external: $volumeName"
    }
}

$expectedApiEnvironment = @{
    ENVIRONMENT = "test"
    TRADING_MODE = "analysis_only"
    DATABASE_URL = "postgresql+asyncpg://ctcc:ctcc_dev_password@postgres:5432/ctcc"
    REDIS_URL = "redis://redis:6379/0"
}
foreach ($name in $expectedApiEnvironment.Keys) {
    $actual = "$(Get-RequiredMapValue -Map $apiEnvironment -Name $name)"
    if ($actual -ne $expectedApiEnvironment[$name]) {
        throw "Unexpected Gate 3 environment value: $name"
    }
}

$expectedPostgresEnvironment = @{
    POSTGRES_USER = "ctcc"
    POSTGRES_PASSWORD = "ctcc_dev_password"
    POSTGRES_DB = "ctcc"
}
foreach ($name in $expectedPostgresEnvironment.Keys) {
    $actual = "$(Get-RequiredMapValue -Map $postgresService["environment"] -Name $name)"
    if ($actual -ne $expectedPostgresEnvironment[$name]) {
        throw "Unexpected Gate 3 PostgreSQL environment value: $name"
    }
}

$disabledCapabilityNames = @(
    "AUTO_TRADE",
    "LIVE_TRADING",
    "OKX_WS_ENABLED",
    "PAPER_AUTO_TICKS",
    "PAPER_AUTO_EXECUTION",
    "OKX_LIVE_ENABLED",
    "OKX_LIVE_ALLOW_ORDER_WRITES",
    "OKX_LIVE_AUTO_RECONCILE_ON_START",
    "OKX_LIVE_AUTO_EXECUTION",
    "OKX_DEMO_ENABLED",
    "OKX_DEMO_ALLOW_ORDER_WRITES",
    "OKX_DEMO_AUTO_RECONCILE_ON_START",
    "OKX_DEMO_AUTO_EXECUTION",
    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED",
    "OKX_DEMO_OBSERVABILITY_ENABLED",
    "OKX_DEMO_SOAK_ENABLED",
    "OKX_DEMO_SOAK_ALLOW_EXECUTE"
)
foreach ($name in $disabledCapabilityNames) {
    $actual = "$(Get-RequiredMapValue -Map $apiEnvironment -Name $name)"
    if ($actual.Trim().ToLowerInvariant() -ne "false") {
        throw "Gate 3 capability must be exactly false: $name"
    }
}

$credentialNames = @(
    "OKX_LIVE_API_KEY",
    "OKX_LIVE_API_SECRET",
    "OKX_LIVE_API_PASSPHRASE",
    "OKX_DEMO_API_KEY",
    "OKX_DEMO_API_SECRET",
    "OKX_DEMO_API_PASSPHRASE"
)
foreach ($name in $credentialNames) {
    $actual = "$(Get-RequiredMapValue -Map $apiEnvironment -Name $name)"
    if (-not [string]::IsNullOrWhiteSpace($actual)) {
        throw "Gate 3 offline profile contains exchange credentials: $name"
    }
}

$clearedProxyNames = @(
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "SOCKS_PROXY",
    "WS_PROXY",
    "WSS_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
    "socks_proxy",
    "ws_proxy",
    "wss_proxy"
)
foreach ($name in $clearedProxyNames) {
    $actual = "$(Get-RequiredMapValue -Map $apiEnvironment -Name $name)"
    if (-not [string]::IsNullOrWhiteSpace($actual)) {
        throw "Gate 3 runtime proxy must be empty: $name"
    }
}
foreach ($name in @("NO_PROXY", "no_proxy")) {
    $actual = "$(Get-RequiredMapValue -Map $apiEnvironment -Name $name)"
    if ($actual -ne "*") {
        throw "Gate 3 runtime proxy bypass must be wildcarded: $name"
    }
}

Write-Host "MIE_GATE3_ISOLATED_COMPOSE=1"
Write-Host "MIE_GATE3_NO_PUBLISHED_PORTS=1"
Write-Host "MIE_GATE3_INTERNAL_NETWORK=1"
Write-Host "MIE_GATE3_ISOLATED_VOLUMES=1"
Write-Host "MIE_GATE3_EXTERNAL_CONNECTORS_DISABLED=1"
Write-Host "MIE_GATE3_EXCHANGE_CREDENTIALS=0"
Write-Host "MIE_GATE3_RUNTIME_PROXIES_DISABLED=1"

$cleanupExit = -1
try {
    $gate2Script = Join-Path $PSScriptRoot "verify_mie_gate2.ps1"
    & $gate2Script `
        -ComposeProjectName $projectName `
        -ComposeEnvironmentFile $safeProfile `
        -AdditionalComposeFiles @($gate3ComposeFile)
    if (-not $?) {
        throw "MIE Gate 2 platform verification failed"
    }

    Write-Host "== MIE Gate 3 runtime isolation probe =="
    $runtimeNetworkName = "${projectName}_default"
    $runtimeNetworkInternal = (
        docker network inspect `
            --format '{{.Internal}}' `
            $runtimeNetworkName
    ).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $runtimeNetworkInternal -ne "true") {
        throw "Gate 3 runtime network is not internally isolated"
    }
    foreach ($containerName in $expectedContainerNames.Values) {
        $runtimeProject = (
            docker inspect `
                --format '{{ index .Config.Labels "com.docker.compose.project" }}' `
                $containerName
        ).Trim()
        if ($LASTEXITCODE -ne 0 -or $runtimeProject -ne $projectName) {
            throw "Gate 3 container has an unexpected Compose project label"
        }
        $runtimeNetworksJson = (
            docker inspect `
                --format '{{json .NetworkSettings.Networks}}' `
                $containerName
        ).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect Gate 3 container networks"
        }
        $runtimeNetworks = $runtimeNetworksJson | ConvertFrom-Json
        $attachedNetworkNames = @(
            $runtimeNetworks.PSObject.Properties | ForEach-Object { $_.Name }
        )
        if (
            $attachedNetworkNames.Count -ne 1 -or
            $attachedNetworkNames[0] -ne $runtimeNetworkName
        ) {
            throw "Gate 3 container is attached outside its internal network"
        }
    }
    $portBindingsJson = (
        docker inspect `
            --format '{{json .HostConfig.PortBindings}}' `
            $env:CTCC_API_CONTAINER_NAME
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Gate 3 API port bindings"
    }
    $portBindings = $portBindingsJson | ConvertFrom-Json
    if (
        $null -ne $portBindings -and
        @($portBindings.PSObject.Properties).Count -ne 0
    ) {
        throw "MIE Gate 3 API unexpectedly published a runtime port"
    }
    $runtimeEnvironmentJson = (
        docker inspect `
            --format '{{json .Config.Env}}' `
            $env:CTCC_API_CONTAINER_NAME
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Gate 3 API environment"
    }
    $runtimeEnvironment = @{}
    foreach ($entry in ($runtimeEnvironmentJson | ConvertFrom-Json)) {
        $parts = $entry.Split("=", 2)
        $runtimeEnvironment[$parts[0]] = if ($parts.Count -eq 2) {
            $parts[1]
        }
        else {
            ""
        }
    }
    foreach ($name in $clearedProxyNames) {
        if (
            -not $runtimeEnvironment.ContainsKey($name) -or
            -not [string]::IsNullOrWhiteSpace($runtimeEnvironment[$name])
        ) {
            throw "Gate 3 runtime proxy injection detected: $name"
        }
    }
    foreach ($name in @("NO_PROXY", "no_proxy")) {
        if (
            -not $runtimeEnvironment.ContainsKey($name) -or
            $runtimeEnvironment[$name] -ne "*"
        ) {
            throw "Gate 3 runtime proxy bypass changed: $name"
        }
    }
    Write-Host "MIE_GATE3_RUNTIME_ISOLATION_VERIFIED=1"

    Write-Host "== MIE Gate 3 offline foundation boundary =="
    $boundaryProbe = @'
from app.mie.validation import (
    ForwardDirectionLabel,
    Gate3EvidenceArtifact,
    Gate3Preregistration,
    PointInTimeReplaySnapshot,
)

for contract_type in (
    Gate3Preregistration,
    Gate3EvidenceArtifact,
    PointInTimeReplaySnapshot,
    ForwardDirectionLabel,
):
    assert contract_type.model_fields["runtime_consumers"].default == 0
    assert contract_type.model_fields["execution_authority"].default is False

print("MIE_GATE3_CONTRACT_RUNTIME_CONSUMERS=0")
print("MIE_GATE3_CONTRACT_EXECUTION_AUTHORITY=0")
'@
    $boundaryProbe | docker compose @composeArguments exec -T api python -
    if ($LASTEXITCODE -ne 0) {
        throw "MIE Gate 3 offline boundary probe failed (exit=$LASTEXITCODE)"
    }

    Write-Host "== MIE Gate 3 foundation tests =="
    docker compose @composeArguments exec -T api python scripts/hermetic_pytest.py `
        -q -p no:cacheprovider `
        tests/unit/mie/test_gate3_contracts.py `
        tests/unit/mie/test_gate3_replay.py `
        tests/unit/mie/test_gate3_splits.py `
        tests/unit/mie/test_gate3_metrics.py `
        tests/unit/mie/test_gate3_costs.py `
        tests/unit/mie/test_package_boundary.py
    if ($LASTEXITCODE -ne 0) {
        throw "MIE Gate 3 foundation tests failed (exit=$LASTEXITCODE)"
    }
}
finally {
    Write-Host "== Remove isolated Gate 3 stack =="
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        docker compose @composeArguments down --volumes --remove-orphans --rmi local
        $cleanupExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

if ($cleanupExit -ne 0) {
    throw "Unable to remove the isolated Gate 3 stack (exit=$cleanupExit)"
}

Write-Host "MIE_GATE3_FOUNDATION_VERIFIED=1"
Write-Host "MIE_GATE3_CURRENT_CLAIM=computational"
Write-Host "MIE_GATE3_REAL_HOLDOUT_READS=0"
Write-Host "MIE_GATE3_RUNTIME_CONSUMERS=0"
Write-Host "MIE_GATE3_EXECUTION_AUTHORITY=0"
}
finally {
    foreach ($name in $managedEnvironmentNames) {
        $saved = $savedEnvironment[$name]
        $restoredValue = if ($saved.Exists) {
            $saved.Value
        }
        else {
            $null
        }
        [Environment]::SetEnvironmentVariable(
            $name,
            $restoredValue,
            [EnvironmentVariableTarget]::Process
        )
    }
}
