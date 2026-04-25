#!/usr/bin/env bash
# Install the Sirena Control Center launcher on the current user's
# Jetson Nano (or any Linux desktop) so the icon shows up in the
# applications menu and on the Desktop.
#
# Usage:
#   ./scripts/install-sirena-desktop.sh
#
# Re-run this any time you move the repo to a new path.
#
# What it does:
#   1. chmod +x the launcher script that the .desktop entry will call.
#   2. Generate ~/.local/share/applications/sirena.desktop pointing at
#      that launcher (no quotes / && / pipes - the freedesktop Exec
#      spec rejects those silently and double-click stops working).
#   3. Copy the same .desktop onto the Desktop folder when present and
#      mark it trusted so GNOME / Nautilus will run it without
#      complaining.
#   4. Print where the launch log lives so you can debug any
#      double-click failure (~/.cache/sirena/launch.log).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER_SRC="${REPO_ROOT}/scripts/launch-sirena.sh"
ICON_SRC="${REPO_ROOT}/sirena_ui/assets/sirena_app_icon.png"
TEMPLATE="${REPO_ROOT}/desktop/sirena.desktop"

if [[ ! -f "${LAUNCHER_SRC}" ]]; then
    echo "launcher not found at ${LAUNCHER_SRC}" >&2
    exit 1
fi
if [[ ! -f "${ICON_SRC}" ]]; then
    echo "icon not found at ${ICON_SRC}" >&2
    exit 1
fi
if [[ ! -f "${TEMPLATE}" ]]; then
    echo "desktop template not found at ${TEMPLATE}" >&2
    exit 1
fi

chmod +x "${LAUNCHER_SRC}"

ICON_DIR="${HOME}/.local/share/icons"
APP_DIR="${HOME}/.local/share/applications"
DESKTOP_DIR="${HOME}/Desktop"

mkdir -p "${ICON_DIR}" "${APP_DIR}"

ICON_DEST="${ICON_DIR}/sirena.png"
DESKTOP_DEST="${APP_DIR}/sirena.desktop"

cp -f "${ICON_SRC}" "${ICON_DEST}"

sed \
    -e "s|__EXEC__|${LAUNCHER_SRC}|g" \
    -e "s|__PATH__|${REPO_ROOT}|g" \
    -e "s|__ICON__|${ICON_DEST}|g" \
    "${TEMPLATE}" > "${DESKTOP_DEST}"

chmod +x "${DESKTOP_DEST}"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true
fi

if [[ -d "${DESKTOP_DIR}" ]]; then
    cp -f "${DESKTOP_DEST}" "${DESKTOP_DIR}/sirena.desktop"
    chmod +x "${DESKTOP_DIR}/sirena.desktop"
    if command -v gio >/dev/null 2>&1; then
        gio set "${DESKTOP_DIR}/sirena.desktop" "metadata::trusted" true >/dev/null 2>&1 || true
    fi
    if command -v dbus-launch >/dev/null 2>&1; then
        # Older GNOME (Ubuntu 18.04 / JetPack 4) also reads the older
        # gvfs metadata key; setting it is harmless on newer systems.
        gio set "${DESKTOP_DIR}/sirena.desktop" "metadata::xfce-exe-checksum" "" >/dev/null 2>&1 || true
    fi
fi

cat <<EOF

Installed Sirena launcher.

  launcher script : ${LAUNCHER_SRC}
  app entry       : ${DESKTOP_DEST}
  desktop icon    : ${DESKTOP_DIR}/sirena.desktop  (if Desktop exists)
  icon image      : ${ICON_DEST}

Generated Exec line:
  $(grep ^Exec= "${DESKTOP_DEST}")

If double-clicking still does nothing, check:
  tail -n 50 ~/.cache/sirena/launch.log

You can also right-click the desktop icon and pick
"Allow Launching" if the file manager warns about an untrusted file.

Run from a terminal at any time:
  ${LAUNCHER_SRC}
EOF
