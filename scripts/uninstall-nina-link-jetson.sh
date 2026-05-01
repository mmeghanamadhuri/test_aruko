#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Remove Nina Link daemon from this Jetson (systemd unit + optional local data).
#
# Usage (from repo root):
#   ./scripts/uninstall-nina-link-jetson.sh
#   ./scripts/uninstall-nina-link-jetson.sh --purge   # also remove .venv-link + state JSON
#
# Does NOT delete source code under nina/link_daemon/ (that stays in the git repo).
# -----------------------------------------------------------------------------

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_DST="/etc/systemd/system/nina-link.service"
VENV_PATH="${REPO_ROOT}/.venv-link"
STATE_HOME="${HOME}/.cache/sirena/link_state.json"
STATE_VAR="/var/lib/nina/link_state.json"

PURGE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=1; shift ;;
        -h|--help)
            grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }

_sudo() {
    if [[ "$(id -u)" -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

say "1. systemd: nina-link.service"

_sudo systemctl stop nina-link.service 2>/dev/null || true
_sudo systemctl disable nina-link.service 2>/dev/null || true
if [[ -f "${UNIT_DST}" ]]; then
    _sudo rm -f "${UNIT_DST}"
fi
_sudo systemctl daemon-reload 2>/dev/null || true
ok "Stopped / disabled nina-link (unit file removed if present)"

if [[ "${PURGE}" -eq 1 ]]; then
    say "2. Purge local data (--purge)"
    if [[ -d "${VENV_PATH}" ]]; then
        rm -rf "${VENV_PATH}"
        ok "Removed ${VENV_PATH}"
    fi
    if [[ -f "${STATE_HOME}" ]]; then
        rm -f "${STATE_HOME}"
        ok "Removed ${STATE_HOME}"
    fi
    if [[ -f "${STATE_VAR}" ]]; then
        _sudo rm -f "${STATE_VAR}" 2>/dev/null && ok "Removed ${STATE_VAR}" || warn "Could not remove ${STATE_VAR} (sudo)"
    fi
else
    echo ""
    echo "  Virtualenv and Wi-Fi state files were kept."
    echo "  To remove them too: $0 --purge"
fi

say "Done."
echo ""
echo "  Repo sources under nina/link_daemon/ are unchanged."
echo "  Re-install with: ./scripts/install-nina-link-jetson.sh --all"
echo ""
