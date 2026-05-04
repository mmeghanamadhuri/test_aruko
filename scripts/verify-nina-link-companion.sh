#!/usr/bin/env bash
# Run on the Jetson after nina-link is installed — validates HTTP bridges and prints
# next-step hints for LiDAR port, venv, and logs. See docs/COMPANION_APP.md.
set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-8787}"
BASE="http://${HOST}:${PORT}"

say() { printf '%s\n' "$*"; }

say "=== Nina link companion verification (${BASE}) ==="
echo ""

if ! command -v curl >/dev/null 2>&1; then
  say "Install curl: sudo apt install -y curl"
  exit 1
fi

say "--- GET /health ---"
curl -sf --max-time 4 "${BASE}/health" | head -c 400 || {
  say "FAILED — is nina-link running? sudo systemctl status nina-link"
  exit 1
}
echo ""
echo ""

say "--- GET /v1/robot/capabilities ---"
curl -sf --max-time 4 "${BASE}/v1/robot/capabilities" | python3 -m json.tool 2>/dev/null || curl -sf --max-time 4 "${BASE}/v1/robot/capabilities"
echo ""
echo ""

say "--- USB serial nodes (LiDAR often ttyUSB* or ttyACM*) ---"
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || say "(none — plug LiDAR or fix udev)"
echo ""

say "--- Hint: default LiDAR port is /dev/ttyUSB0; set NINA_LIDAR_PORT in"
say "    /etc/systemd/system/nina-link.service.d/bridges.conf if yours differs."
say ""

say "--- Recent nina-link logs (last 25 lines) ---"
if command -v journalctl >/dev/null 2>&1; then
  sudo journalctl -u nina-link -n 25 --no-pager 2>/dev/null || journalctl -u nina-link -n 25 --no-pager 2>/dev/null || say "journalctl unavailable"
else
  say "journalctl not found"
fi
echo ""

say "Done. For full pip stack: ./scripts/update-nina-link-jetson.sh --sirena-headless --restart --verify"
