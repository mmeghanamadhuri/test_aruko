#!/usr/bin/env bash
# Install motor_bridge.py as a systemd service on the Raspberry Pi so it
# auto-starts on boot. Run on the Pi (NOT the Jetson):
#
#   cd pi_motor_bridge
#   sudo bash install_service.sh
#
# After install:
#   sudo systemctl status motor-bridge        # check it's running
#   sudo journalctl -u motor-bridge -f        # tail the logs
#   sudo systemctl stop motor-bridge          # one-shot stop
#   sudo systemctl disable motor-bridge       # no auto-start on boot

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[ERROR] run as root: sudo bash install_service.sh"
  exit 1
fi

INSTALL_DIR=/opt/sirena/pi_motor_bridge
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[INSTALL] Source dir : $SCRIPT_DIR"
echo "[INSTALL] Install dir: $INSTALL_DIR"

mkdir -p "$INSTALL_DIR"
install -m 0755 "$SCRIPT_DIR/motor_bridge.py"      "$INSTALL_DIR/motor_bridge.py"
install -m 0644 "$SCRIPT_DIR/navigation_bldc.py"   "$INSTALL_DIR/navigation_bldc.py"
install -m 0755 "$SCRIPT_DIR/serial_test.py"       "$INSTALL_DIR/serial_test.py"

install -m 0644 "$SCRIPT_DIR/motor-bridge.service" /etc/systemd/system/motor-bridge.service

# Make sure pigpiod is enabled so the bridge has something to talk to.
systemctl enable pigpiod || true
systemctl start pigpiod || true

systemctl daemon-reload
systemctl enable motor-bridge.service
systemctl restart motor-bridge.service

sleep 1
echo
echo "[INSTALL] Done. Status:"
systemctl --no-pager status motor-bridge.service || true
echo
echo "[INSTALL] Live log: sudo journalctl -u motor-bridge -f"
