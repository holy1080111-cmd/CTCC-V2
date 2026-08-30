param(
    [string]$DatasetRoot = ""
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
        $output = @(& $Command 2>&1)
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
if ([string]::IsNullOrWhiteSpace($DatasetRoot)) {
    $DatasetRoot = Join-Path $env:USERPROFILE "CTCC-V2-benchmark-data"
}
if (-not (Test-Path -LiteralPath $DatasetRoot)) {
    New-Item -ItemType Directory -Path $DatasetRoot | Out-Null
}
$datasetItem = Get-Item -LiteralPath $DatasetRoot -Force
if (-not $datasetItem.PSIsContainer) {
    throw "DatasetRoot must be a directory"
}
if (
    ($datasetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
) {
    throw "DatasetRoot cannot be a reparse point or symlink"
}
$resolvedDatasetRoot = (Resolve-Path -LiteralPath $DatasetRoot).Path
$separator = [IO.Path]::DirectorySeparatorChar
$repoPrefix = $repoRoot.TrimEnd($separator) + $separator
if ($resolvedDatasetRoot.StartsWith(
    $repoPrefix,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "DatasetRoot must remain outside the Git repository"
}

Push-Location $repoRoot
try {
    Write-Host "== Execution-authority preflight =="
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
        throw "Disable execution authority before the probe: $($enabled -join ', ')"
    }
    Write-Host "EXTERNAL_BENCHMARK_HOST_EXECUTION_AUTHORITY_DISABLED=1"

    Invoke-NativeStep "Build reviewed research image" {
        docker compose build api
    }

    $volume = "${resolvedDatasetRoot}:/datasets"
    Invoke-NativeStep "Prepare official Binance identity" {
        docker compose run --rm --no-deps `
            --volume $volume `
            api python scripts/prepare_binance_kline_reference.py `
            --dataset-root /datasets `
            --terms-review `
                /app/docs/external_sources/binance_public_data_review_2026-08-17.md `
            --symbol BTCUSDT `
            --interval 1m `
            --day 2024-01-01
    }

    $requestPath = Join-Path $resolvedDatasetRoot `
        "evidence\btcusdt-1m-2024-01-01-request.json"
    $identityPath = Join-Path $resolvedDatasetRoot `
        "evidence\btcusdt-1m-2024-01-01-identity.json"
    if (
        -not (Test-Path -LiteralPath $requestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $identityPath -PathType Leaf)
    ) {
        throw "Prepared identity or request evidence is missing"
    }
    $request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
    $identity = Get-Content -LiteralPath $identityPath -Raw | ConvertFrom-Json
    Write-Host "== Review pinned artifact before its GET =="
    [pscustomobject]@{
        request_id = $request.request_id
        download_url = $request.download_url
        expected_sha256 = $request.expected_sha256
        expected_byte_size = $request.expected_byte_size
        provider_last_modified_at = $identity.provider_last_modified_at
        revision_policy = $identity.revision_policy
        reference_only = $request.reference_only
        promotion_eligible = $request.promotion_eligible
        execution_authority = $request.execution_authority
    } | ConvertTo-Json

    $confirmation = Read-Host (
        "輸入 ACQUIRE_REFERENCE_ONLY 才會下載這個 2024-01-01 公開 ZIP"
    )
    if ($confirmation -cne "ACQUIRE_REFERENCE_ONLY") {
        throw "Exact reference-only confirmation was not supplied"
    }

    Invoke-NativeStep "Acquire pinned Binance ZIP" {
        docker compose run --rm --no-deps `
            --volume $volume `
            api python scripts/acquire_external_benchmark_artifact.py `
            --request `
                /datasets/evidence/btcusdt-1m-2024-01-01-request.json `
            --dataset-root /datasets `
            --receipt-relative-path `
                evidence/btcusdt-1m-2024-01-01-receipt.json `
            --max-bytes 1048576 `
            --max-redirects 0 `
            --max-archive-members 2 `
            --max-uncompressed-bytes 4194304 `
            --max-single-member-bytes 4194304 `
            --max-expansion-ratio 20
    }

    Invoke-NativeStep "Profile Binance kline quality" {
        docker compose run --rm --no-deps `
            --volume $volume `
            api python scripts/profile_binance_kline_reference.py `
            --dataset-root /datasets `
            --symbol BTCUSDT `
            --interval 1m `
            --day 2024-01-01
    }

    $evidencePath = Join-Path $resolvedDatasetRoot `
        "evidence\btcusdt-1m-2024-01-01-evidence.json"
    $qualityPath = Join-Path $resolvedDatasetRoot `
        "evidence\btcusdt-1m-2024-01-01-binance-quality.json"
    $evidence = Get-Content -LiteralPath $evidencePath -Raw | ConvertFrom-Json
    $quality = Get-Content -LiteralPath $qualityPath -Raw | ConvertFrom-Json
    if (
        $evidence.passed -ne $true -or
        $evidence.execution_authority -ne $false -or
        $quality.passed -ne $true -or
        $quality.observed_row_count -ne 1440
    ) {
        throw "Binance reference evidence did not pass final acceptance"
    }

    Write-Host ""
    Write-Host "BINANCE_BTCUSDT_REFERENCE_PROBE_VERIFIED=1"
    Write-Host "BINANCE_KLINE_ROWS=1440"
    Write-Host "BINANCE_ARTIFACT_SHA256=$($request.expected_sha256)"
    Write-Host "DATASET_ROOT=$resolvedDatasetRoot"
    Write-Host "REFERENCE_ONLY=1"
    Write-Host "PROMOTION_ELIGIBLE=0"
    Write-Host "EXECUTION_AUTHORITY=0"
    Write-Host "REAL_ORDER_TESTED=0"
}
finally {
    Pop-Location
}
