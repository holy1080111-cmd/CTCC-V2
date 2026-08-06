$ErrorActionPreference = "Stop"
docker compose up -d --build
docker compose ps
Write-Host "Open http://127.0.0.1:8100/docs"
