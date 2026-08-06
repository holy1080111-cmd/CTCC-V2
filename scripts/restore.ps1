param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $BackupPath)) { throw "Backup file not found: $BackupPath" }

$confirmation = Read-Host "This replaces the CTCC V2 database. Type RESTORE to continue"
if ($confirmation -ne "RESTORE") {
    Write-Host "Restore cancelled."
    exit 1
}

$container = (docker compose ps -q postgres).Trim()
if (-not $container) { throw "PostgreSQL container is not running." }

Write-Host "Stopping API before database restore..."
docker compose stop api

docker cp $BackupPath "${container}:/tmp/ctcc-restore.dump" | Out-Null
docker compose exec -T postgres sh -c 'dropdb --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB" && pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/ctcc-restore.dump'
docker compose exec -T postgres rm -f /tmp/ctcc-restore.dump

Write-Host "Starting API and applying migrations..."
docker compose up -d api
Start-Sleep -Seconds 10
Invoke-RestMethod http://127.0.0.1:8100/api/recovery/status | ConvertTo-Json -Depth 6
