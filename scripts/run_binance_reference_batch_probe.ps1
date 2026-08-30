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
    $name = "CTCC-V2-binance-batch-" + (
        Get-Date -Format "yyyyMMdd-HHmmss"
    )
    $DatasetRoot = Join-Path $env:USERPROFILE $name
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
        throw "Disable execution authority before batch probe: $($enabled -join ', ')"
    }
    Write-Host "EXTERNAL_BENCHMARK_HOST_EXECUTION_AUTHORITY_DISABLED=1"

    Invoke-NativeStep "Build reviewed research image" {
        docker compose build api
    }

    $volume = "${resolvedDatasetRoot}:/datasets"
    Invoke-NativeStep "Prepare frozen Binance batch identities" {
        docker compose run --rm --no-deps `
            --volume $volume `
            api python scripts/prepare_binance_kline_batch.py `
            --dataset-root /datasets `
            --terms-review `
                /app/docs/external_sources/binance_public_data_review_2026-08-17.md `
            --max-concurrency 4
    }

    $planPath = Join-Path $resolvedDatasetRoot `
        "evidence\binance-reference-batch-v1-plan.json"
    $preparationPath = Join-Path $resolvedDatasetRoot `
        "evidence\binance-reference-batch-v1-preparation.json"
    if (
        -not (Test-Path -LiteralPath $planPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $preparationPath -PathType Leaf)
    ) {
        throw "Prepared batch plan or identity evidence is missing"
    }
    $plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
    $preparation = (
        Get-Content -LiteralPath $preparationPath -Raw | ConvertFrom-Json
    )
    if (
        $preparation.expected_artifact_count -ne 180 -or
        @($preparation.entries).Count -ne 180 -or
        $preparation.reference_only -ne $true -or
        $preparation.promotion_eligible -ne $false -or
        $preparation.execution_authority -ne $false
    ) {
        throw "Prepared batch does not match the frozen 180-artifact plan"
    }

    $requestSummaries = @(
        foreach ($entry in $preparation.entries) {
            $requestPath = Join-Path $resolvedDatasetRoot (
                $entry.request_relative_path.Replace("/", "\")
            )
            $request = (
                Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
            )
            $uri = [Uri]$request.download_url
            if (
                $uri.Scheme -ne "https" -or
                $uri.Host -ne "data.binance.vision" -or
                -not $uri.IsDefaultPort -or
                -not [string]::IsNullOrEmpty($uri.Query) -or
                -not [string]::IsNullOrEmpty($uri.Fragment) -or
                $request.reference_only -ne $true -or
                $request.promotion_eligible -ne $false -or
                $request.execution_authority -ne $false
            ) {
                throw "Prepared request left the reviewed reference boundary"
            }
            [pscustomobject]@{
                request_id = $request.request_id
                download_url = $request.download_url
                expected_sha256 = $request.expected_sha256
                expected_byte_size = $request.expected_byte_size
            }
        }
    )

    Write-Host "== Review frozen batch before artifact GETs =="
    [pscustomobject]@{
        plan_id = $plan.plan_id
        plan_sha256 = $preparation.plan_sha256
        symbols = @($plan.symbols)
        interval = $plan.interval
        windows = @($plan.windows)
        expected_artifact_count = $preparation.expected_artifact_count
        total_expected_bytes = $preparation.total_expected_bytes
        exact_reviewed_host_count = $requestSummaries.Count
        first_request = $requestSummaries[0]
        last_request = $requestSummaries[-1]
        holdout_semantics = $plan.holdout_semantics
        reference_only = $preparation.reference_only
        promotion_eligible = $preparation.promotion_eligible
        execution_authority = $preparation.execution_authority
    } | ConvertTo-Json -Depth 8

    $confirmation = Read-Host (
        "輸入 ACQUIRE_BINANCE_BATCH_REFERENCE_ONLY 才會下載 180 個公開 ZIP"
    )
    if ($confirmation -cne "ACQUIRE_BINANCE_BATCH_REFERENCE_ONLY") {
        throw "Exact batch reference-only confirmation was not supplied"
    }

    Invoke-NativeStep "Acquire and profile frozen Binance batch" {
        docker compose run --rm --no-deps `
            --volume $volume `
            api python scripts/acquire_profile_binance_kline_batch.py `
            --dataset-root /datasets `
            --max-concurrency 4
    }

    $evidencePath = Join-Path $resolvedDatasetRoot `
        "evidence\binance-reference-batch-v1-evidence.json"
    if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
        throw "Final Binance batch evidence is missing"
    }
    $evidence = (
        Get-Content -LiteralPath $evidencePath -Raw | ConvertFrom-Json
    )
    if (
        $evidence.passed -ne $true -or
        $evidence.expected_artifact_count -ne 180 -or
        $evidence.completed_artifact_count -ne 180 -or
        $evidence.total_minute_rows -ne 259200 -or
        @($evidence.partition_summaries).Count -ne 6 -or
        $evidence.runtime_consumers -ne 0 -or
        $evidence.strategy_evaluated -ne $false -or
        $evidence.costs_evaluated -ne $false -or
        $evidence.promotion_eligible -ne $false -or
        $evidence.execution_authority -ne $false
    ) {
        throw "Binance batch evidence did not pass final acceptance"
    }

    Write-Host "== Descriptive partition summaries =="
    @(
        $evidence.partition_summaries |
        Select-Object `
            partition,
            symbol,
            start_day,
            end_day,
            day_count,
            observed_direction,
            theil_sen_log_slope_per_day,
            path_efficiency,
            @{Name="total_return"; Expression={
                $_.close_path_metrics.total_return
            }},
            @{Name="max_close_drawdown"; Expression={
                $_.close_path_metrics.max_drawdown
            }}
    ) | ConvertTo-Json -Depth 6

    Write-Host ""
    Write-Host "BINANCE_REFERENCE_BATCH_PROBE_VERIFIED=1"
    Write-Host "BINANCE_BATCH_ARTIFACTS=180"
    Write-Host "BINANCE_BATCH_MINUTE_ROWS=259200"
    Write-Host "BINANCE_BATCH_PARTITION_SUMMARIES=6"
    Write-Host "DATASET_ROOT=$resolvedDatasetRoot"
    Write-Host "RETROSPECTIVE_HOLDOUT=1"
    Write-Host "STRATEGY_EVALUATED=0"
    Write-Host "COSTS_EVALUATED=0"
    Write-Host "REFERENCE_ONLY=1"
    Write-Host "PROMOTION_ELIGIBLE=0"
    Write-Host "RUNTIME_CONSUMERS=0"
    Write-Host "EXECUTION_AUTHORITY=0"
    Write-Host "REAL_ORDER_TESTED=0"
}
finally {
    Pop-Location
}
