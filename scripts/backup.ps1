param(
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$container = (docker compose ps -q postgres).Trim()
if (-not $container) { throw "PostgreSQL container is not running." }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$destination = Join-Path $OutputDirectory "ctcc-v1.6.8-$timestamp.dump"

docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/ctcc.dump'
docker cp "${container}:/tmp/ctcc.dump" $destination | Out-Null
docker compose exec -T postgres rm -f /tmp/ctcc.dump

Write-Host "Backup created: $destination"
