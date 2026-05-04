#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Refresh nina-link on the Jetson after git pull or repo copy:
#   pip install -r requirements-link.txt, optional vision extras, optional systemd
#   restart, and curl checks.
#
# Usage (from repo root on the Jetson):
#   chmod +x scripts/update-nina-link-jetson.sh
#   ./scripts/update-nina-link-jetson.sh
#   ./scripts/update-nina-link-jetson.sh --pull --restart --verify
#   ./scripts/update-nina-link-jetson.sh --vision
#   ./scripts/update-nina-link-jetson.sh --sirena-headless   # OpenCV+gTTS+YOLO+sensors; no PyQt5 (Jetson-safe)
#   ./scripts/update-nina-link-jetson.sh --install-dropin   # sudo: writes bridges.conf
#
# See docs/COMPANION_APP.md for env vars and companion features.
# -----------------------------------------------------------------------------

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQ_FILE="${REPO_ROOT}/requirements-link.txt"
VENV_PATH="${REPO_ROOT}/.venv-link"

DO_PULL=0
DO_VISION=0
DO_SIRENA_HEADLESS=0
DO_RESTART=0
DO_VERIFY=0
INSTALL_DROPIN=0
SKIP_PIP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pull) DO_PULL=1; shift ;;
        --vision) DO_VISION=1; shift ;;
        --sirena-headless) DO_SIRENA_HEADLESS=1; shift ;;
        --restart) DO_RESTART=1; shift ;;
        --verify) DO_VERIFY=1; shift ;;
        --install-dropin) INSTALL_DROPIN=1; shift ;;
        --skip-pip) SKIP_PIP=1; shift ;;
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

SUDO=(sudo)
if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
fi

cd "${REPO_ROOT}" || exit 1

say "Repo: ${REPO_ROOT}"

# --- 1. Optional git pull ---
if [[ "${DO_PULL}" -eq 1 ]]; then
    if [[ -d "${REPO_ROOT}/.git" ]] && command -v git >/dev/null 2>&1; then
        say "git pull"
        if git -C "${REPO_ROOT}" pull --ff-only; then
            ok "git pull"
        else
            bad "git pull failed — resolve conflicts and retry"
            exit 1
        fi
    else
        warn "No .git here — skip --pull (copy/rsync new tree manually)"
    fi
fi

if [[ ! -f "${REQ_FILE}" ]]; then
    bad "Missing ${REQ_FILE}"
    exit 1
fi

PY="${VENV_PATH}/bin/python"
PIP="${VENV_PATH}/bin/pip"
if [[ ! -x "${PY}" ]]; then
    bad "No venv at ${VENV_PATH} — run once: ./scripts/install-nina-link-jetson.sh --all"
    exit 1
fi

# --- 2. pip refresh ---
if [[ "${SKIP_PIP}" -eq 0 ]]; then
    say "pip install -r requirements-link.txt"
    "${PIP}" install -U pip setuptools wheel >/dev/null
    if "${PIP}" install -r "${REQ_FILE}"; then
        ok "requirements-link.txt"
    else
        bad "pip install failed"
        exit 1
    fi
else
    say "Skipping pip (--skip-pip)"
fi

if [[ "${DO_VISION}" -eq 1 ]]; then
    say "optional vision deps (OpenCV + numpy; add YOLO/CUDA per your Jetson stack)"
    if "${PIP}" install 'opencv-python-headless' 'numpy>=1.19'; then
        ok "opencv + numpy in ${VENV_PATH}"
    else
        bad "vision pip install failed"
        exit 1
    fi
fi

HEADLESS_REQ="${REPO_ROOT}/sirena_ui/requirements-headless.txt"
if [[ "${DO_SIRENA_HEADLESS}" -eq 1 ]]; then
    if [[ ! -f "${HEADLESS_REQ}" ]]; then
        bad "Missing ${HEADLESS_REQ}"
        exit 1
    fi
    say "sirena_ui stack without PyQt5 (gTTS, OpenCV, ultralytics, optional sensors)"
    warn "On Jetson: if ultralytics/torch fails, install NVIDIA PyTorch first, then pip install --no-deps ultralytics"
    if "${PIP}" install -r "${HEADLESS_REQ}"; then
        ok "requirements-headless.txt"
    else
        bad "pip install sirena_ui/requirements-headless.txt failed — see file header / torch wheels"
        exit 1
    fi
fi

# --- 3. Import check (same as full installer) ---
say "import check"
export PYTHONPATH="${REPO_ROOT}"
IMPORT_ERR="$(mktemp "${TMPDIR:-/tmp}/nina-link-upd.XXXXXX.err")"
if "${PY}" -c "
from nina.link_daemon.config import load_config
from nina.link_daemon.nm import mock_backend
from nina.link_daemon.state import LinkCoordinator
from nina.link_daemon.api import create_app
c = load_config()
co = LinkCoordinator(c, mock_backend())
app = create_app(c, co)
print(app.title)
" 2>"${IMPORT_ERR}"; then
    rm -f "${IMPORT_ERR}"
    ok "nina.link_daemon imports OK"
else
    bad "Import failed:"
    sed 's/^/    /' "${IMPORT_ERR}" >&2
    rm -f "${IMPORT_ERR}"
    exit 1
fi

# --- 4. Optional systemd drop-in (companion bridges) ---
if [[ "${INSTALL_DROPIN}" -eq 1 ]]; then
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        bad "Need sudo to install drop-in"
        exit 1
    fi
    say "install /etc/systemd/system/nina-link.service.d/bridges.conf"
    "${SUDO[@]}" mkdir -p /etc/systemd/system/nina-link.service.d
    "${SUDO[@]}" tee /etc/systemd/system/nina-link.service.d/bridges.conf >/dev/null <<'EOF'
[Service]
Environment=NINA_LINK_ENABLE_ROBOT_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTION_BRIDGE=1
Environment=NINA_LINK_ENABLE_RECORD_BRIDGE=1
Environment=NINA_LINK_ENABLE_VISION_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTIONS_STATIC=1
Environment=NINA_LINK_ENABLE_SLAM_BRIDGE=1
Environment=NINA_LINK_ENABLE_DEPTH_BRIDGE=1
Environment=NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1
# Environment=NINA_LINK_SESSION_SCRIPT=/usr/local/bin/nina-link-session-helper
EOF
    "${SUDO[@]}" systemctl daemon-reload
    ok "bridges.conf installed — edit file to disable features you do not want"
fi

# --- 5. Restart service ---
if [[ "${DO_RESTART}" -eq 1 ]]; then
    if systemctl list-unit-files nina-link.service >/dev/null 2>&1; then
        say "systemctl restart nina-link"
        "${SUDO[@]}" systemctl restart nina-link.service
        sleep 2
        if systemctl is-active --quiet nina-link.service 2>/dev/null; then
            ok "nina-link.service active"
        else
            warn "nina-link not active — journalctl -u nina-link -e"
        fi
    else
        warn "nina-link.service not installed — run install-nina-link-jetson.sh --systemd-only"
    fi
fi

# --- 6. curl verify ---
if [[ "${DO_VERIFY}" -eq 1 ]]; then
    say "HTTP checks"
    if command -v curl >/dev/null 2>&1; then
        if curl -sf --max-time 3 "http://127.0.0.1:8787/health" | head -c 200; then
            echo ""
            ok "/health"
        else
            warn "/health failed — is the daemon running on 8787?"
        fi
        echo ""
        curl -sf --max-time 3 "http://127.0.0.1:8787/v1/robot/capabilities" | head -c 600 || warn "capabilities failed"
        echo ""
    else
        warn "curl not installed — sudo apt install curl"
    fi
fi

say "Done."
echo ""
echo "  Typical one-liner after code update:"
echo "    ./scripts/update-nina-link-jetson.sh --pull --restart --verify"
echo ""
echo "  First-time HTTP bridges on Jetson (needs sudo once):"
echo "    ./scripts/update-nina-link-jetson.sh --install-dropin --restart --verify"
echo ""
echo "  Full docs: docs/COMPANION_APP.md"
echo ""

exit 0
