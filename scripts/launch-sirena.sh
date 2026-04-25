#!/usr/bin/env bash
# Launcher for the Sirena Control Center, invoked by the .desktop entry.
#
# This script keeps the .desktop's Exec= line free of special characters
# (quotes, &&, $, etc.) which the freedesktop Exec spec does not allow
# and which silently break double-click launching from Nautilus / GNOME
# Shell on Ubuntu / Jetson.
#
# Stdout + stderr are appended to ~/.cache/sirena/launch.log so any
# error from the GUI is captured even when the desktop launcher
# discards the process output.
#
# You can override the Python interpreter:
#   SIRENA_PYTHON=/path/to/python3 ./scripts/launch-sirena.sh

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${HOME}/.cache/sirena"
LOG_FILE="${LOG_DIR}/launch.log"
mkdir -p "${LOG_DIR}"

PYTHON_BIN="${SIRENA_PYTHON:-/usr/bin/python3}"

# PyQt5 on Jetson / older Ubuntu builds doesn't always have a working
# Wayland plugin; force xcb when not already set so the GUI starts.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

{
    echo
    echo "===== launching $(date '+%Y-%m-%d %H:%M:%S') ====="
    echo "REPO_ROOT=${REPO_ROOT}"
    echo "PYTHON=${PYTHON_BIN}"
    echo "DISPLAY=${DISPLAY:-<unset>}"
    echo "QT_QPA_PLATFORM=${QT_QPA_PLATFORM}"
    cd "${REPO_ROOT}"
    PYTHONPATH="${REPO_ROOT}" "${PYTHON_BIN}" -m sirena_ui
    echo "exit code: $?"
} >> "${LOG_FILE}" 2>&1
