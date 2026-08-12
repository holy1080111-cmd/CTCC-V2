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

Invoke-NativeStep "Docker build and start" {
    docker compose up -d --build
}

$deadline = (Get-Date).AddSeconds(120)
do {
    $health = (docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' ctcc-v2-api 2>$null).Trim()
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

Invoke-NativeStep "MIE write-authority preflight" {
    docker compose exec -T api python -c 'from app.config.settings import get_settings; s = get_settings(); active = any((s.auto_trade, s.paper_auto_execution, s.live_trading, s.okx_live_allow_order_writes, s.okx_live_auto_execution, s.okx_demo_allow_order_writes, s.okx_demo_auto_execution, s.okx_demo_soak_allow_execute)); assert not active, "Disable every Paper, Demo, and Live execution-authority switch before MIE verification"; print("MIE_EXECUTION_AUTHORITY_DISABLED=1")'
}

Invoke-NativeStep "Alembic heads" {
    docker compose exec -T api alembic heads
}
Invoke-NativeStep "Alembic current" {
    docker compose exec -T api alembic current
}
Invoke-NativeStep "Alembic exact revision" {
    docker compose exec -T api python -c 'import subprocess; expected = "0013 (head)"; heads = subprocess.check_output(["alembic", "heads"], text=True).strip(); current = subprocess.check_output(["alembic", "current"], text=True).strip().splitlines()[-1]; assert heads == expected, (heads, expected); assert current == expected, (current, expected); print("ALEMBIC_REVISION=0013")'
}
Invoke-NativeStep "Alembic schema drift" {
    docker compose exec -T api alembic check
}
Invoke-NativeStep "MIE Gate 1 targeted tests" {
    docker compose exec -T api python scripts/hermetic_pytest.py -q -p no:cacheprovider tests/unit/test_hermetic_pytest.py tests/unit/mie tests/integration/test_mie_shadow_contract_integration.py
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
    docker compose run --rm --no-deps --volume "$($sourceRoot):/source:ro" api python /source/scripts/manifest.py --root /source --manifest /source/MANIFEST.sha256 --check
}

$head = (git rev-parse HEAD).Trim()
$health = (docker inspect --format '{{.State.Health.Status}}' ctcc-v2-api).Trim()
Write-Host "MIE_GATE1_VERIFIED=1"
Write-Host "MIE_EXECUTION_AUTHORITY=0"
Write-Host "HEAD=$head"
Write-Host "ALEMBIC_HEAD=0013"
Write-Host "API_HEALTH=$health"
