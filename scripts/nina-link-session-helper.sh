#!/usr/bin/env bash
# Stop/start the desktop Sirena kiosk user unit so nina-link can open USB/GPIO devices.
# Install: sudo cp scripts/nina-link-session-helper.sh /usr/local/bin/nina-link-session-helper
#          sudo chmod +x /usr/local/bin/nina-link-session-helper
#
# Environment (optional — set in nina-link.service.d/bridges.conf so the daemon inherits them):
#   NINA_SESSION_DESKTOP_USER   Login user that runs nina-ui-kiosk (required for user units)
#   NINA_SESSION_KIOSK_UNIT     systemd user unit name (default: nina-ui-kiosk.service)
#
# nina-link typically runs as root; user kiosk units require sudo -u + XDG_RUNTIME_DIR.
set -euo pipefail

UNIT="${NINA_SESSION_KIOSK_UNIT:-nina-ui-kiosk.service}"
DESKTOP_USER="${NINA_SESSION_DESKTOP_USER:-}"

verb="${1:-}"

user_ctl() {
  local cmd="$1"
  if [[ -z "$DESKTOP_USER" ]]; then
    echo "nina-link-session-helper: set NINA_SESSION_DESKTOP_USER to the graphical login (e.g. export in bridges.conf)" >&2
    exit 2
  fi
  local uid home rt
  uid="$(id -u "$DESKTOP_USER" 2>/dev/null)" || {
    echo "nina-link-session-helper: user not found: $DESKTOP_USER" >&2
    exit 2
  }
  home="$(getent passwd "$DESKTOP_USER" | cut -d: -f6)"
  rt="/run/user/${uid}"
  if [[ ! -d "$rt" ]]; then
    echo "nina-link-session-helper: $rt missing — is the desktop session logged in / linger enabled?" >&2
    exit 3
  fi
  sudo -u "$DESKTOP_USER" \
    XDG_RUNTIME_DIR="$rt" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=${rt}/bus" \
    systemctl --user "$cmd" "$UNIT"
}

case "$verb" in
  claim)
    # Tablet takes over — drop kiosk so nina-link owns devices.
    user_ctl stop || true
    exit 0
    ;;
  release)
    # Restore kiosk when tablet leaves the Nina console.
    user_ctl start || true
    exit 0
    ;;
  *)
    echo "usage: $0 claim|release" >&2
    exit 1
    ;;
esac
