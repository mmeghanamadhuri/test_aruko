# Run in PowerShell on Windows when using Docker Desktop and a bind-mounted repo.
# Usage:  .\scripts\docker_refresh_after_edit.ps1  [-Container carbot_vision]

param(
    [string] $Container = $env:CARBOT_VISION_CONTAINER
)
if (-not $Container) { $Container = "carbot_vision" }

$exists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^$([regex]::Escape($Container))$"
if (-not $exists) {
    Write-Host "Container '$Container' not found."
    docker ps -a --format "{{.Names}}"
    exit 1
}

Write-Host "==> Mounts (confirm host path is the repo you edit)"
docker inspect -f '{{range .Mounts}}{{printf "%s -> %s\n" .Source .Destination}}{{end}}' $Container

Write-Host "==> Clear __pycache__ under /workspace"
docker exec $Container bash -lc "find /workspace -type d -name __pycache__ -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null; exit 0"

Write-Host "==> Stop vision Python processes (if any)"
docker exec $Container bash -lc "pkill -f vision.window_servo 2>/dev/null; pkill -f window_servo 2>/dev/null; exit 0"

Write-Host "==> Optional: docker restart $Container"
Write-Host "Then re-run your vision command inside the container."
