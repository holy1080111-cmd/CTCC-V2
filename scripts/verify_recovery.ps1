$ErrorActionPreference = "Stop"

$baseUrl = "http://127.0.0.1:8100"

function Wait-ForRecoveryStatus {
    param([int]$Attempts = 30)

    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            $status = Invoke-RestMethod "$baseUrl/api/recovery/status" -TimeoutSec 5
            if ($status.initialized) {
                return $status
            }
        }
        catch {
            # API may still be starting after the container restart.
        }
        Start-Sleep -Seconds 2
    }

    throw "Recovery verification failed: API did not become ready in time."
}

# Freeze durable memory into PostgreSQL immediately before restart. The
# orchestrator must be stopped/disabled while running this acceptance test.
$persistBody = @{ action = "persist_memory" } | ConvertTo-Json
$before = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/recovery/reconcile" `
    -ContentType "application/json" `
    -Body $persistBody

Write-Host "Before restart memory checksum:   $($before.memory_checksum)"
Write-Host "Before restart database checksum: $($before.database_checksum)"

if (-not $before.consistent) {
    throw "Recovery verification failed before restart: memory and database checksums differ."
}

$expectedChecksum = $before.database_checksum

docker compose restart api
$after = Wait-ForRecoveryStatus

Write-Host "After restart memory checksum:    $($after.memory_checksum)"
Write-Host "After restart database checksum:  $($after.database_checksum)"

if (-not $after.consistent) {
    throw "Recovery verification failed after restart: memory and database checksums differ."
}

if ($after.memory_checksum -ne $expectedChecksum) {
    throw "Recovery verification failed: recovered durable checksum differs from the pre-restart checkpoint."
}

Write-Host "Restart recovery verification passed."
