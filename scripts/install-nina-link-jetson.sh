#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Nina Link — one-shot Jetson install + diagnosis for the companion-app daemon
#
# Usage (from your laptop: copy the repo to the Jetson, then on the Jetson):
#   chmod +x scripts/install-nina-link-jetson.sh
#   ./scripts/install-nina-link-jetson.sh
#
# Options:
#   --with-systemd    Install and enable systemd unit (needs sudo; paths from repo)
#   --smoke           After install, briefly run daemon and curl /health (needs curl)
#   --venv PATH       Virtualenv directory (default: <repo>/.venv-link)
#
# This script does NOT install distro packages without asking when possible:
# it prints what's missing (python3-venv, nmcli, etc.) and exits non-zero.
# -----------------------------------------------------------------------------

set -u

WITH_SYSTEMD=0
SMOKE=0
VENV_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-systemd) WITH_SYSTEMD=1; shift ;;
        --smoke)        SMOKE=1; shift ;;
        --venv)
            VENV_PATH="${2:?}"
            shift 2
            ;;
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQ_FILE="${REPO_ROOT}/requirements-link.txt"
UNIT_DST="/etc/systemd/system/nina-link.service"

if [[ -z "${VENV_PATH}" ]]; then
    VENV_PATH="${REPO_ROOT}/.venv-link"
fi

PY="${VENV_PATH}/bin/python"
PIP="${VENV_PATH}/bin/pip"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }

EXIT=0

# ---------------------------------------------------------------------------
say "1. Paths"
echo "  Repo root: ${REPO_ROOT}"
if [[ ! -f "${REQ_FILE}" ]]; then
    bad "Missing ${REQ_FILE}"
    exit 1
fi
ok "requirements-link.txt found"

# ---------------------------------------------------------------------------
say "2. Host diagnosis (no changes)"

if command -v python3 >/dev/null 2>&1; then
    ok "python3: $(command -v python3) ($(python3 --version 2>&1))"
else
    bad "python3 not found — install: sudo apt install python3 python3-pip python3-venv"
    EXIT=1
fi

if command -v nmcli >/dev/null 2>&1; then
    ok "nmcli: $(command -v nmcli)"
else
    warn "nmcli not found — NetworkManager CLI missing (Wi-Fi control needs this)"
    EXIT=1
fi

if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    ok "NetworkManager service is active"
elif command -v systemctl >/dev/null 2>&1; then
    warn "NetworkManager not active — enable Wi-Fi stack on Jetson"
fi

if [[ $EXIT -ne 0 ]] && [[ ! -t 0 ]]; then
    say "Fix the issues above, then re-run."
    exit "$EXIT"
fi

# ---------------------------------------------------------------------------
say "3. Virtualenv + pip packages"

if [[ ! -d "${VENV_PATH}" ]]; then
    if ! python3 -m venv --help >/dev/null 2>&1; then
        bad "python3-venv not available — install: sudo apt install python3-venv"
        exit 1
    fi
    say "  Creating venv: ${VENV_PATH}"
    python3 -m venv "${VENV_PATH}" || { bad "venv creation failed"; exit 1; }
    ok "Virtualenv created"
else
    ok "Using existing venv: ${VENV_PATH}"
fi

if [[ ! -x "${PIP}" ]]; then
    bad "pip missing inside venv"
    exit 1
fi

"${PIP}" install -U pip setuptools wheel >/dev/null
"${PIP}" install -r "${REQ_FILE}" || { bad "pip install failed"; exit 1; }
ok "Installed packages from requirements-link.txt"

# ---------------------------------------------------------------------------
say "4. Import / package verification"

export PYTHONPATH="${REPO_ROOT}"
if "${PY}" -c "
from nina.link_daemon.config import load_config
from nina.link_daemon.nm import mock_backend
from nina.link_daemon.state import LinkCoordinator
from nina.link_daemon.api import create_app
c = load_config()
co = LinkCoordinator(c, mock_backend())
app = create_app(c, co)
print('import_ok', app.title)
" 2>/tmp/nina-link-import.err; then
    ok "nina.link_daemon imports successfully"
else
    bad "Import failed:"
    sed 's/^/    /' /tmp/nina-link-import.err >&2
    exit 1
fi

# ---------------------------------------------------------------------------
say "5. Optional smoke test (HTTP /health)"

if [[ "${SMOKE}" -eq 1 ]]; then
    if ! command -v curl >/dev/null 2>&1; then
        warn "curl not installed — skipping smoke (sudo apt install curl)"
    else
        export PYTHONPATH="${REPO_ROOT}"
        export NINA_LINK_MOCK=1
        export NINA_LINK_BOOT_AP=0
        export NINA_LINK_HOST=127.0.0.1
        export NINA_LINK_PORT=8788
        "${PY}" -m nina.link_daemon.main >/tmp/nina-link-smoke.log 2>&1 &
        DAEMON_PID=$!
        sleep 3
        if curl -sf "http://127.0.0.1:8788/health" | grep -q '"ok"'; then
            ok "HTTP /health responded (mock NM)"
        else
            bad "Smoke HTTP failed — log: /tmp/nina-link-smoke.log"
            EXIT=1
        fi
        kill "${DAEMON_PID}" 2>/dev/null || true
        wait "${DAEMON_PID}" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
say "6. systemd unit (optional)"

if [[ "${WITH_SYSTEMD}" -eq 1 ]]; then
    if [[ "$(id -u)" -ne 0 ]] && ! sudo -n true 2>/dev/null; then
        warn "Need sudo to install systemd unit — run: sudo $0 --with-systemd"
    else
        SUDO=(sudo)
        if [[ "$(id -u)" -eq 0 ]]; then SUDO=(); fi
        "${SUDO[@]}" tee "${UNIT_DST}" >/dev/null <<EOF
[Unit]
Description=Nina Link Daemon (Wi-Fi provisioning API for companion app)
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}
Environment=PYTHONPATH=${REPO_ROOT}
Environment=NINA_LINK_BOOT_AP=1
Environment=NINA_LINK_HOST=0.0.0.0
Environment=NINA_LINK_PORT=8787
ExecStart=${PY} -m nina.link_daemon.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        "${SUDO[@]}" systemctl daemon-reload
        "${SUDO[@]}" systemctl enable nina-link.service
        "${SUDO[@]}" systemctl restart nina-link.service
        if systemctl is-active --quiet nina-link.service 2>/dev/null; then
            ok "nina-link.service is active"
        else
            warn "Unit installed but not active — check: journalctl -u nina-link -e"
            EXIT=1
        fi
    fi
else
    echo "  Skipped (pass --with-systemd to install / enable systemd)"
fi

# ---------------------------------------------------------------------------
say "Done."

cat <<EOF

  Run the daemon manually (foreground):
    export PYTHONPATH=${REPO_ROOT}
    export NINA_LINK_MOCK=1    # only on a laptop without nmcli; omit on Jetson
    ${PY} -m nina.link_daemon.main

  Companion app default URL on hotspot is usually:
    http://192.168.4.1:8787

  Full notes: docs/COMPANION_APP.md

EOF

exit "${EXIT}"
