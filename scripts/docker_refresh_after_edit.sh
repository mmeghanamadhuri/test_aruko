#!/usr/bin/env bash
# Run on the machine that hosts Docker (Jetson or dev PC).
# Fixes the usual "I changed code but the container still runs old behavior":
#   1) Bind mount not pointing at the repo you edit
#   2) Python bytecode cache
#   3) Long-running python process never restarted (still has old modules in memory)
#
# Usage:
#   ./scripts/docker_refresh_after_edit.sh [container_name]
#   CARBOT_VISION_CONTAINER=my_vision ./scripts/docker_refresh_after_edit.sh

set -euo pipefail
CONTAINER="${1:-${CARBOT_VISION_CONTAINER:-carbot_vision}}"

if ! sudo docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Container '$CONTAINER' not found. Known names:"
  sudo docker ps -a --format '{{.Names}}' || true
  exit 1
fi

echo "==> Bind mounts involving /workspace (verify this is YOUR edited tree)"
sudo docker inspect -f '{{range .Mounts}}{{printf "%s -> %s\n" .Source .Destination}}{{end}}' "$CONTAINER" | grep -E 'workspace|Workspace' || \
  sudo docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' "$CONTAINER"

echo "==> Dropping Python bytecode under /workspace in $CONTAINER"
sudo docker exec "$CONTAINER" bash -lc 'find /workspace -type d -name __pycache__ -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null || true'

echo "==> Stopping vision-related Python (edit the pattern if you use a different entrypoint)"
sudo docker exec "$CONTAINER" bash -lc 'pkill -f "vision.window_servo" 2>/dev/null || true; pkill -f "window_servo" 2>/dev/null || true; exit 0'

echo "==> Optional: full container restart (picks up bind-mounted files; does not rebuild image)"
echo "    sudo docker restart $CONTAINER"
echo
echo "Next: start vision again inside the container, e.g."
echo "    sudo docker exec -it $CONTAINER bash -lc 'cd /workspace && python3 -m vision.window_servo --preview'"
echo
echo "Auto-reload during dev (inside container, one-time: pip install watchdog):"
echo "    watchmedo auto-restart --patterns=\"*.py\" --recursive -- python3 -m vision.window_servo --preview"
