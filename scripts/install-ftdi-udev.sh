#!/usr/bin/env bash
#
# install-ftdi-udev.sh - one-shot setup so the Nina app can lower the FTDI
# latency_timer to 1ms without sudo every time.
#
# The kernel default (16ms) causes intermittent Dynamixel read failures,
# because motor response bytes can sit in the FTDI internal FIFO long
# enough to leak into the next request's response window. The Nina app
# tries to write 1ms to /sys/.../latency_timer at startup, but that file
# is root-owned. This rule installs an ACL-based fixup that drops the
# value to 1ms automatically whenever an FTDI USB-serial device is
# plugged in - no sudo needed at runtime.
#
# Run once:   sudo bash scripts/install-ftdi-udev.sh
# Then unplug + replug the FTDI dongle (or reboot) to take effect.
#
# Verify:     cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer
# Should print: 1
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root. Try: sudo bash $0" >&2
  exit 1
fi

RULES_DIR="/etc/udev/rules.d"
RULE_FILE="${RULES_DIR}/99-nina-ftdi-latency.rules"

mkdir -p "${RULES_DIR}"

# Match common FTDI VID:PID pairs (FT232R/FT232H/FT2232/FT4232 etc.).
cat > "${RULE_FILE}" <<'EOF'
# Nina: drop FTDI USB-serial latency_timer to 1ms so Dynamixel reads are
# reliable. Without this the kernel default of 16ms causes random missing
# motors on every health check.
SUBSYSTEM=="usb-serial", DRIVERS=="ftdi_sio", ATTR{latency_timer}="1"

# Belt-and-braces: also chmod the latency_timer attribute so any user in
# the dialout group can re-tune it later if needed.
ACTION=="add", SUBSYSTEM=="usb-serial", DRIVERS=="ftdi_sio", \
    RUN+="/bin/sh -c 'chmod g+w /sys%p/latency_timer; chgrp dialout /sys%p/latency_timer'"
EOF

echo "Wrote ${RULE_FILE}:"
sed 's/^/    /' "${RULE_FILE}"

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb-serial --attr-match=latency_timer || true

echo
echo "Done. Unplug + replug the FTDI dongle (or reboot) for the rule to"
echo "take effect, then verify with:"
echo "    cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer"
echo "It should print: 1"
