#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Nina Link — one-shot Jetson install + diagnosis for the companion-app daemon
#
# The systemd unit enables **all HTTP bridges** used by the Android companion
# (drive, actions, record, vision, static media) so tablet ↔ Jetson comms match
# docs/COMPANION_APP.md without a separate drop-in. Optional vision ML deps:
#   ./scripts/update-nina-link-jetson.sh --sirena-headless --restart --verify
#
# Usage (on the Jetson, from repo root):
#   ./scripts/install-nina-link-jetson.sh --all
#   ./scripts/uninstall-nina-link-jetson.sh --purge   # remove service + venv + state
# Do NOT pass script flags to chmod (e.g. chmod +x foo.sh --smoke is wrong).
#
# Options:
#   --all                   Full Jetson setup: apt deps + venv + smoke test + systemd (AP restarts on boot)
#   --install-system-deps   sudo apt install python3-venv, pip, curl (Ubuntu/Debian Jetson)
#   --with-systemd          Install and enable systemd unit (needs sudo; paths from repo)
#   --systemd-only          Only install/enable nina-link.service (venv must exist); use after sudo password
#   --no-systemd            Skip systemd even if implied by --all (for dev laptops)
#   --smoke                 After install, briefly run daemon and curl /health (needs curl)
#   --venv PATH             Virtualenv directory (default: <repo>/.venv-link)
#
# If venv creation fails with "ensurepip is not available", run:
#   sudo apt install python3-venv
# or re-run with --install-system-deps
# -----------------------------------------------------------------------------

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQ_FILE="${REPO_ROOT}/requirements-link.txt"
UNIT_DST="/etc/systemd/system/nina-link.service"

WITH_SYSTEMD=0
SMOKE=0
INSTALL_SYSTEM_DEPS=0
SYSTEMD_ONLY=0
NO_SYSTEMD=0
VENV_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            INSTALL_SYSTEM_DEPS=1
            SMOKE=1
            WITH_SYSTEMD=1
            shift
            ;;
        --install-system-deps) INSTALL_SYSTEM_DEPS=1; shift ;;
        --with-systemd) WITH_SYSTEMD=1; shift ;;
        --systemd-only) SYSTEMD_ONLY=1; shift ;;
        --no-systemd) NO_SYSTEMD=1; shift ;;
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

if [[ "${NO_SYSTEMD}" -eq 1 ]]; then
    WITH_SYSTEMD=0
fi

if [[ -z "${VENV_PATH}" ]]; then
    VENV_PATH="${REPO_ROOT}/.venv-link"
fi

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }

# Writes /etc/systemd/system/nina-link.service — AP on boot via NINA_LINK_BOOT_AP in unit + daemon.
_install_nina_link_systemd() {
    local SUDO=(sudo)
    if [[ "$(id -u)" -eq 0 ]]; then
        SUDO=()
    fi
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        bad "sudo not installed — cannot register systemd unit"
        return 1
    fi
    # Interactive sudo password if needed
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! sudo -v; then
        warn "sudo authentication failed"
        return 1
    fi
    "${SUDO[@]}" tee "${UNIT_DST}" >/dev/null <<EOF
[Unit]
Description=Nina Link Daemon (Wi-Fi provisioning API for companion app)
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=simple
# Must exist at systemd parse time; PYTHONPATH + ExecStart pin the repo (avoid /opt vs home mismatches).
WorkingDirectory=/
# Match Sirena UI kiosk BLDC UART (see nina/systemd/nina-link-navigation.env.example).
EnvironmentFile=-/etc/nina-link/navigation.env
Environment=PYTHONPATH=${REPO_ROOT}
Environment=NINA_LINK_BOOT_AP=1
Environment=NINA_LINK_DISABLE_WIFI_AUTOCONNECT=1
Environment=NINA_LINK_WIFI_READY_TIMEOUT=240
Environment=NINA_LINK_WIFI_READY_POLL=2
Environment=NINA_LINK_HOTSPOT_ATTEMPTS=5
Environment=NINA_LINK_HOST=0.0.0.0
Environment=NINA_LINK_PORT=8787
# Companion app (tablet): expose full HTTP API — same set as update-nina-link-jetson.sh drop-in
Environment=NINA_LINK_ENABLE_ROBOT_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTION_BRIDGE=1
Environment=NINA_LINK_ENABLE_RECORD_BRIDGE=1
Environment=NINA_LINK_ENABLE_VISION_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTIONS_STATIC=1
Environment=NINA_LINK_ENABLE_SLAM_BRIDGE=1
Environment=NINA_LINK_ENABLE_DEPTH_BRIDGE=1
Environment=NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1
ExecStart=${PY} -m nina.link_daemon.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    NAV_ENV_EX="${REPO_ROOT}/nina/systemd/nina-link-navigation.env.example"
    NAV_ENV_DST="/etc/nina-link/navigation.env"
    if [[ -f "${NAV_ENV_EX}" ]]; then
        "${SUDO[@]}" mkdir -p /etc/nina-link
        if [[ ! -f "${NAV_ENV_DST}" ]]; then
            "${SUDO[@]}" cp "${NAV_ENV_EX}" "${NAV_ENV_DST}"
            ok "Created ${NAV_ENV_DST} from example (edit NINA_NAV_REMOTE_PORT if needed)"
        else
            ok "Keeping existing ${NAV_ENV_DST}"
        fi
    else
        warn "Missing ${NAV_ENV_EX} — create ${NAV_ENV_DST} manually for BLDC parity with Sirena UI"
    fi
    "${SUDO[@]}" systemctl daemon-reload
    "${SUDO[@]}" systemctl enable nina-link.service
    "${SUDO[@]}" systemctl restart nina-link.service
    if systemctl is-active --quiet nina-link.service 2>/dev/null; then
        ok "nina-link.service active — AP/restart policy enabled on boot"
        return 0
    fi
    warn "Unit installed but not active — journalctl -u nina-link -e"
    return 1
}

# systemd-only: register service and exit (run: sudo ./install-nina-link-jetson.sh --systemd-only)
if [[ "${SYSTEMD_ONLY}" -eq 1 ]]; then
    PY="${VENV_PATH}/bin/python"
    say "nina-link systemd-only"
    if [[ ! -x "${PY}" ]]; then
        bad "No Python at ${PY} — run full install first (without --systemd-only)."
        exit 1
    fi
    _install_nina_link_systemd || exit 1
    exit 0
fi

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

_sudo_apt() {
    local -a cmd=(sudo)
    if [[ "$(id -u)" -eq 0 ]]; then
        cmd=()
    fi
    if [[ "${#cmd[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        bad "Need sudo or root to install packages"
        return 1
    fi
    "${cmd[@]}" "$@"
}

if [[ "${INSTALL_SYSTEM_DEPS}" -eq 1 ]]; then
    say "  Installing distro packages (apt)"
    _sudo_apt apt-get update -qq || { bad "apt-get update failed"; exit 1; }
    PY_MINOR="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 10)"
    # python3.10-venv provides ensurepip on Ubuntu/Jetson images without full python3-venv metapackage
    _sudo_apt apt-get install -y \
        "python3.${PY_MINOR}-venv" \
        python3-venv \
        python3-pip \
        curl \
        || { bad "apt-get install failed"; exit 1; }
    ok "python3-venv, pip, curl (apt)"
fi

# Remove broken half-created venv from a previous failed run (no interpreter)
if [[ -d "${VENV_PATH}" ]] && [[ ! -x "${VENV_PATH}/bin/python" ]]; then
    warn "Removing incomplete venv: ${VENV_PATH}"
    rm -rf "${VENV_PATH}"
fi

_venv_ready() {
    python3 -c "import ensurepip" >/dev/null 2>&1
}

if ! _venv_ready; then
    if [[ "${INSTALL_SYSTEM_DEPS}" -eq 1 ]]; then
        bad "ensurepip still unavailable after apt — try: sudo apt install python3-venv"
        exit 1
    fi
    bad "ensurepip not available (python3-venv missing on Ubuntu/Debian)"
    echo ""
    echo "  Fix one of:"
    echo "    sudo apt install python3-venv"
    echo "    ./scripts/install-nina-link-jetson.sh --install-system-deps --smoke"
    echo ""
    exit 1
fi

if [[ ! -d "${VENV_PATH}" ]]; then
    if ! python3 -m venv --help >/dev/null 2>&1; then
        bad "python3 -m venv failed — install python3-venv (see above)"
        exit 1
    fi
    say "  Creating venv: ${VENV_PATH}"
    if ! python3 -m venv "${VENV_PATH}"; then
        bad "venv creation failed"
        rm -rf "${VENV_PATH}"
        exit 1
    fi
    ok "Virtualenv created"
else
    ok "Using existing venv: ${VENV_PATH}"
fi

PY="${VENV_PATH}/bin/python"
_venv_has_pip() {
    [[ -x "${VENV_PATH}/bin/pip" ]] || [[ -x "${VENV_PATH}/bin/pip3" ]]
}

# Older failed runs left a venv with python but no pip (ensurepip wasn't on the system yet).
if [[ -x "${PY}" ]] && ! _venv_has_pip; then
    say "  Bootstrapping pip inside venv (python -m ensurepip)"
    if ! "${PY}" -m ensurepip --upgrade; then
        warn "ensurepip failed — recreating venv from scratch"
        rm -rf "${VENV_PATH}"
        say "  Creating venv: ${VENV_PATH}"
        python3 -m venv "${VENV_PATH}" || { bad "venv recreate failed"; exit 1; }
        PY="${VENV_PATH}/bin/python"
    fi
fi

if [[ -x "${VENV_PATH}/bin/pip" ]]; then
    PIP="${VENV_PATH}/bin/pip"
elif [[ -x "${VENV_PATH}/bin/pip3" ]]; then
    PIP="${VENV_PATH}/bin/pip3"
else
    bad "pip missing inside venv after ensurepip — try: rm -rf ${VENV_PATH} && re-run this script"
    exit 1
fi

"${PIP}" install -U pip setuptools wheel >/dev/null
"${PIP}" install -r "${REQ_FILE}" || { bad "pip install failed"; exit 1; }
ok "Installed packages from requirements-link.txt"

# ---------------------------------------------------------------------------
say "4. Import / package verification"

# Use a user-owned temp file (fixed paths under /tmp can be root-owned after sudo runs).
IMPORT_ERR="$(mktemp "${TMPDIR:-/tmp}/nina-link-import.XXXXXX.err")"
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
" 2>"${IMPORT_ERR}"; then
    rm -f "${IMPORT_ERR}"
    ok "nina.link_daemon imports successfully"
else
    bad "Import failed:"
    sed 's/^/    /' "${IMPORT_ERR}" >&2
    rm -f "${IMPORT_ERR}"
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
        SMOKE_LOG="$(mktemp "${TMPDIR:-/tmp}/nina-link-smoke.XXXXXX.log")"
        "${PY}" -m nina.link_daemon.main >"${SMOKE_LOG}" 2>&1 &
        DAEMON_PID=$!
        sleep 3
        if curl -sf "http://127.0.0.1:8788/health" | grep -q '"ok"'; then
            rm -f "${SMOKE_LOG}"
            ok "HTTP /health responded (mock NM)"
        else
            bad "Smoke HTTP failed — log: ${SMOKE_LOG}"
            EXIT=1
        fi
        kill "${DAEMON_PID}" 2>/dev/null || true
        wait "${DAEMON_PID}" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
say "6. systemd unit (optional)"

if [[ "${WITH_SYSTEMD}" -eq 1 ]]; then
    if ! _install_nina_link_systemd; then
        EXIT=1
        warn "Install service separately (will prompt for sudo password):"
        echo "    sudo $(printf '%q' "${REPO_ROOT}/scripts/install-nina-link-jetson.sh") --systemd-only"
    fi
else
    echo "  Skipped (use --with-systemd or --all for AP + daemon on every boot)"
fi

# ---------------------------------------------------------------------------
say "Done."

cat <<EOF

  Companion app on hotspot (NetworkManager / Jetson typical): http://10.42.0.1:8787

  Manual foreground run (no systemd):
    export PYTHONPATH=${REPO_ROOT}
    export NINA_LINK_ENABLE_ROBOT_BRIDGE=1 NINA_LINK_ENABLE_ACTION_BRIDGE=1 NINA_LINK_ENABLE_RECORD_BRIDGE=1 NINA_LINK_ENABLE_VISION_BRIDGE=1 NINA_LINK_ENABLE_ACTIONS_STATIC=1 NINA_LINK_ENABLE_SLAM_BRIDGE=1 NINA_LINK_ENABLE_DEPTH_BRIDGE=1 NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1
    ${PY} -m nina.link_daemon.main

  Verify APIs after install (on Jetson):
    curl -s http://127.0.0.1:8787/v1/robot/capabilities | head

  Optional — vision / gTTS stack for MJPEG + detections (longer pip run):
    ./scripts/update-nina-link-jetson.sh --sirena-headless --restart --verify

  Remove link daemon from this machine:
    ./scripts/uninstall-nina-link-jetson.sh --purge

  Full notes: docs/COMPANION_APP.md

EOF

exit "${EXIT}"
