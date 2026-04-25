#!/usr/bin/env bash
# Install the Sirena Control Center launcher on the current user's
# Jetson Nano (or any Linux desktop) so the icon shows up in the
# applications menu and on the Desktop.
#
# Usage:
#   ./scripts/install-sirena-desktop.sh
#
# Re-run this any time you move the repo to a new path.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ICON_SRC="${REPO_ROOT}/sirena_ui/assets/sirena_app_icon.png"
TEMPLATE="${REPO_ROOT}/desktop/sirena.desktop"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

if [[ ! -f "${ICON_SRC}" ]]; then
    echo "icon not found at ${ICON_SRC}" >&2
    exit 1
fi
if [[ ! -f "${TEMPLATE}" ]]; then
    echo "desktop template not found at ${TEMPLATE}" >&2
    exit 1
fi

ICON_DIR="${HOME}/.local/share/icons"
APP_DIR="${HOME}/.local/share/applications"
DESKTOP_DIR="${HOME}/Desktop"

mkdir -p "${ICON_DIR}" "${APP_DIR}"

ICON_DEST="${ICON_DIR}/sirena.png"
DESKTOP_DEST="${APP_DIR}/sirena.desktop"

cp -f "${ICON_SRC}" "${ICON_DEST}"

EXEC_LINE="bash -lc 'cd \"${REPO_ROOT}\" && PYTHONPATH=\"${REPO_ROOT}\" ${PYTHON_BIN} -m sirena_ui'"

sed \
    -e "s|__EXEC__|${EXEC_LINE}|g" \
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
fi

echo "Installed:"
echo "  app entry : ${DESKTOP_DEST}"
echo "  desktop   : ${DESKTOP_DIR}/sirena.desktop (if Desktop folder exists)"
echo "  icon      : ${ICON_DEST}"
echo
echo "Launch from the Activities menu, or double-click the Sirena icon"
echo "on the Desktop. From a terminal you can also run:"
echo "  PYTHONPATH=\"${REPO_ROOT}\" ${PYTHON_BIN} -m sirena_ui"
