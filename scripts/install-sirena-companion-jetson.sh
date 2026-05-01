#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# One-shot Jetson setup for the Android **Sirena UI** companion:
#   1) Installs nina-link (venv + deps + systemd + AP-on-boot policy)
#   2) Enables HTTP bridges (drive, actions, record, vision, static media)
#   3) Optionally opens UFW port 8787
#   4) Prints URL hints for the tablet Setup screen
#
# Run on the Jetson from the repo root (after git clone or copy):
#   chmod +x scripts/install-sirena-companion-jetson.sh
#   ./scripts/install-sirena-companion-jetson.sh
#
# Requires: same prerequisites as install-nina-link-jetson.sh (python3-venv, nmcli, sudo).
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL="${REPO_ROOT}/scripts/install-nina-link-jetson.sh"
UPDATE="${REPO_ROOT}/scripts/update-nina-link-jetson.sh"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }

cd "${REPO_ROOT}"

if [[ ! -f "${INSTALL}" ]]; then
    echo "Missing ${INSTALL}" >&2
    exit 1
fi
chmod +x "${INSTALL}" "${UPDATE}" 2>/dev/null || true

say "Step 1/3 — Install nina-link (venv, pip, systemd)"
"${INSTALL}" --all

say "Step 2/3 — Enable HTTP bridges + restart (companion features)"
if [[ ! -f "${UPDATE}" ]]; then
    warn "Missing ${UPDATE} — install bridges manually: docs/COMPANION_APP.md"
    exit 1
fi
"${UPDATE}" --install-dropin --restart --verify

say "Step 3/3 — Firewall (optional)"
if command -v ufw >/dev/null 2>&1; then
    SUDO=(sudo)
    if [[ "$(id -u)" -eq 0 ]]; then
        SUDO=()
    fi
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        warn "ufw present but sudo missing — open port 8787 manually if needed"
    else
        if "${SUDO[@]}" ufw status 2>/dev/null | grep -q "Status: active"; then
            "${SUDO[@]}" ufw allow 8787/tcp comment 'nina-link Sirena companion' >/dev/null || true
            ok "ufw: allowed 8787/tcp"
        else
            warn "ufw not active — if you enable it later, run: sudo ufw allow 8787/tcp"
        fi
    fi
else
    warn "ufw not installed — ensure nothing blocks TCP 8787 (iptables / vendor firewall)"
fi

say "Tablet connection"
JETSON_IP="$( (hostname -I 2>/dev/null || true) | awk '{print $1}')"
if [[ -n "${JETSON_IP}" ]]; then
    echo "  Try on the same LAN:  http://${JETSON_IP}:8787"
fi
echo "  Nina AP / hotspot gateway is often:  http://10.42.0.1:8787  (see tablet Wi‑Fi details)"
echo "  In the app: Setup → Daemon URL → Save & test"
echo ""
echo "  Docs: ${REPO_ROOT}/docs/COMPANION_APP.md"
echo ""

exit 0
