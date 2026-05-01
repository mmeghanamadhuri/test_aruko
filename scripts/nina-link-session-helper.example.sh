#!/usr/bin/env bash
# Example hook for NINA_LINK_SESSION_SCRIPT — copy to e.g. /usr/local/bin/nina-link-session-helper,
# chmod +x, and reference it from systemd (see docs/COMPANION_APP.md).
# The tablet calls POST /v1/session/claim → this script with argument "claim",
# and POST /v1/session/release → "release".
#
# Replace the bodies with your site policy (e.g. systemctl --user stop sirena-kiosk).
set -euo pipefail
verb="${1:-}"
case "$verb" in
  claim)
    echo "claim: install site-specific commands to pause the Sirena UI / kiosk when the tablet takes over."
    exit 0
    ;;
  release)
    echo "release: restore the Sirena UI / kiosk when the tablet releases the session."
    exit 0
    ;;
  *)
    echo "usage: $0 claim|release" >&2
    exit 1
    ;;
esac
