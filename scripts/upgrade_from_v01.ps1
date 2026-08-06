$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    throw ".env is missing. Copy .env.example to .env and configure it first."
}

Write-Host "Stopping old containers without deleting PostgreSQL/Redis volumes..."
docker compose down

Write-Host "Building v0.2 and applying Alembic migrations..."
docker compose up -d --build

Write-Host "Waiting for API health..."
Start-Sleep -Seconds 8

powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
