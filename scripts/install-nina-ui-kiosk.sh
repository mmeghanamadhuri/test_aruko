#!/usr/bin/env bash
# Install the Sirena Nina kiosk-mode systemd user unit so the GUI
# auto-starts fullscreen on the Jetson's 10.1" 1024x600 panel after
# every reboot.
#
# Usage:
#   ./scripts/install-nina-ui-kiosk.sh
#
# Re-run this any time you move the repo to a new path.
#
# What it does:
#   1. chmod +x the launcher the unit will call.
#   2. Substitute the launcher's absolute path into the unit template
#      and drop the result into ~/.config/systemd/user/.
#   3. systemctl --user daemon-reload + enable + (re)start the unit.
#   4. loginctl enable-linger so the unit survives reboots without
#      anyone typing a password (Jetson typically auto-logs in, but
#      this also covers the headless case).
#   5. Print the live log + journalctl commands so you can verify
#      the GUI came up.
#
# Stop / disable later with:
#   systemctl --user disable --now nina-ui-kiosk.service
#
# Edit env vars (e.g. swap UART port to /dev/ttyUSB0) without touching
# the repo:
#   systemctl --user edit nina-ui-kiosk

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_ROOT}/desktop/nina-ui-kiosk.service"
LAUNCHER="${REPO_ROOT}/scripts/launch-sirena.sh"

if [[ ! -f "${TEMPLATE}" ]]; then
    echo "kiosk unit template not found at ${TEMPLATE}" >&2
    exit 1
fi
if [[ ! -f "${LAUNCHER}" ]]; then
    echo "launcher script not found at ${LAUNCHER}" >&2
    exit 1
fi

chmod +x "${LAUNCHER}"

UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_DEST="${UNIT_DIR}/nina-ui-kiosk.service"
mkdir -p "${UNIT_DIR}"

sed "s|__EXEC__|${LAUNCHER}|g" "${TEMPLATE}" > "${UNIT_DEST}"

# Evict any conflicting autostart .desktop entries. If the operator
# previously installed a freedesktop autostart copy (manual `cp` to
# ~/.config/autostart/, or via a GUI "Startup Applications" tool), it
# would fire on login *in addition to* this systemd unit and the panel
# would launch two Nina windows. Move them aside (with a timestamped
# .disabled-by-kiosk-installer suffix) so the unit is the sole
# autostarter. The launcher itself also has a flock-based single-
# instance guard as a belt-and-braces second line of defence.
AUTOSTART_DIR="${HOME}/.config/autostart"
if [[ -d "${AUTOSTART_DIR}" ]]; then
    shopt -s nullglob
    for stale in "${AUTOSTART_DIR}"/sirena*.desktop \
                 "${AUTOSTART_DIR}"/nina*.desktop; do
        backup="${stale}.disabled-by-kiosk-installer.$(date +%s)"
        mv "${stale}" "${backup}"
        echo "[INSTALL] evicted ${stale}"
        echo "[INSTALL]   -> ${backup} (delete if you don't need it)"
    done
    shopt -u nullglob
fi

# loginctl enable-linger so the user systemd manager keeps running
# across reboots even if no one logs in. Required for kiosk.
# Best-effort - some restricted images don't allow this and the user
# can fall back to manual `systemctl --user start nina-ui-kiosk`.
if command -v loginctl >/dev/null 2>&1; then
    sudo loginctl enable-linger "$(whoami)" 2>/dev/null || \
        echo "[WARN] could not enable-linger; kiosk will only start once you log in" >&2
fi

systemctl --user daemon-reload
systemctl --user enable nina-ui-kiosk.service
# `restart` rather than `start` so re-running the installer after an
# edit picks up the new env vars / launcher path immediately.
systemctl --user restart nina-ui-kiosk.service || true

cat <<EOF

Installed Sirena Nina kiosk-mode autostart.

  unit file       : ${UNIT_DEST}
  launcher        : ${LAUNCHER}
  fullscreen flag : NINA_UI_FULLSCREEN=1 (set by the unit)

Useful commands:

  # tail unit log
  journalctl --user -u nina-ui-kiosk -f

  # tail launcher log (same one a desktop-icon launch uses)
  tail -f ~/.cache/sirena/launch.log

  # status
  systemctl --user status nina-ui-kiosk

  # stop / start / restart
  systemctl --user stop nina-ui-kiosk
  systemctl --user restart nina-ui-kiosk

  # disable autostart (one-off testing)
  systemctl --user disable --now nina-ui-kiosk

  # tweak env vars without editing the repo (e.g. switch UART port)
  systemctl --user edit nina-ui-kiosk

To verify on the panel right now: the GUI should already be up,
running fullscreen. Press F11 to toggle out of fullscreen, F10 to
quit. The unit will auto-restart F10 quits so use 'systemctl --user
stop' if you want it gone for the session.
EOF
