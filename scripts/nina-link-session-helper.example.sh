#!/usr/bin/env bash
# Minimal stub — for production use [scripts/nina-link-session-helper.sh] instead (user kiosk stop/start).
# Copy to /usr/local/bin/nina-link-session-helper, chmod +x, set NINA_LINK_SESSION_SCRIPT on nina-link.
set -euo pipefail
verb="${1:-}"
case "$verb" in
  claim)
    echo "claim: replace this stub with scripts/nina-link-session-helper.sh (stop nina-ui-kiosk for tablet)."
    exit 0
    ;;
  release)
    echo "release: restore kiosk when tablet releases (see nina-link-session-helper.sh)."
    exit 0
    ;;
  *)
    echo "usage: $0 claim|release" >&2
    exit 1
    ;;
esac
