param(
    [Parameter(Mandatory = $true)][string]$RequestPath,
    [Parameter(Mandatory = $true)][string]$DatasetRoot,
    [long]$MaxBytes = 1073741824,
    [int]$MaxRedirects = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Test-TruthyValue {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return $false
    }
    return @("1", "true", "yes", "on") -contains (
        "$Value".Trim().ToLowerInvariant()
    )
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

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedRequest = (Resolve-Path -LiteralPath $RequestPath).Path
$resolvedDatasetRoot = (Resolve-Path -LiteralPath $DatasetRoot).Path
if (-not (Test-Path -LiteralPath $resolvedRequest -PathType Leaf)) {
    throw "RequestPath must identify an existing file"
}
if (-not (Test-Path -LiteralPath $resolvedDatasetRoot -PathType Container)) {
    throw "DatasetRoot must identify an existing directory"
}

Push-Location $repoRoot
try {
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
    $configuration = $composeJson | ConvertFrom-Json
    $apiEnvironment = $configuration.services.api.environment
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
    $enabled = @(
        foreach ($name in $authorityNames) {
            $property = $apiEnvironment.PSObject.Properties[$name]
            if ($null -ne $property -and (Test-TruthyValue $property.Value)) {
                $name
            }
        }
    )
    if ($enabled.Count -ne 0) {
        throw "Disable execution authority before acquisition: $($enabled -join ', ')"
    }

    Invoke-NativeStep "Build reviewed acquisition image" {
        docker compose build api
    }

    $requestDirectory = Split-Path -Parent $resolvedRequest
    $requestName = Split-Path -Leaf $resolvedRequest
    Invoke-NativeStep "Acquire reviewed external artifact" {
        docker compose run --rm --no-deps `
            --volume "${requestDirectory}:/requests:ro" `
            --volume "${resolvedDatasetRoot}:/datasets" `
            api python scripts/acquire_external_benchmark_artifact.py `
            --request "/requests/$requestName" `
            --dataset-root /datasets `
            --max-bytes $MaxBytes `
            --max-redirects $MaxRedirects
    }
    Write-Host "EXTERNAL_BENCHMARK_ARTIFACT_ACQUIRED=1"
    Write-Host "EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0"
}
finally {
    Pop-Location
}
