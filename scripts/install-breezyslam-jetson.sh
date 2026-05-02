#!/usr/bin/env bash
# Installs `breezyslam` (the 2D occupancy-grid SLAM library Nina
# uses with the RPLIDAR A1) on a Jetson running Ubuntu 22.04 /
# JetPack 6.x.
#
# Why this script exists:
#   - BreezySLAM is GitHub-only; there is NO `breezyslam` package on
#     PyPI. A bare `pip install breezyslam` fails with
#         "Could not find a version that satisfies the requirement
#          breezyslam"
#     because pip's PyPI search returns zero matches. Operators who
#     followed the obvious sirena_ui/requirements.txt hint
#     (`breezyslam>=0.5.0`) hit this and reported it as
#         "no BreezySLAM Installed" on the screen.
#   - The canonical install (per the upstream README) is:
#         git clone https://github.com/simondlevy/BreezySLAM.git
#         cd BreezySLAM/python
#         sudo python3 setup.py install
#     But on JetPack 6 / Ubuntu 22.04 that runs into TWO extra issues
#     a fresh operator doesn't know about: PEP 668's
#     EXTERNALLY-MANAGED block on system-Python pip installs, AND
#     missing `python3-dev` so the C extension build silently fails.
#   - This script captures the working incantation so you don't have
#     to re-derive it.
#
# What it does:
#   1. Verifies we're on aarch64 (script aborts on x86 / Mac with a
#      pointer at the equivalent `pip install` git URL).
#   2. apt-installs the build deps (build-essential, python3-dev,
#      python3-pip, git).
#   3. Clones (or refreshes) BreezySLAM into /tmp.
#   4. pip-installs from the python/ subdir as --user, with the
#      --break-system-packages PEP 668 escape hatch on JetPack 6.
#   5. Smoke-tests the import + RMHC_SLAM init so the operator sees
#      a green "ok" line, not just a silent "Successfully installed"
#      that may still have a half-broken C extension.

set -euo pipefail

BREEZYSLAM_REPO="${BREEZYSLAM_REPO:-https://github.com/simondlevy/BreezySLAM.git}"
BREEZYSLAM_REF="${BREEZYSLAM_REF:-master}"
BUILD_DIR="${BUILD_DIR:-/tmp/BreezySLAM}"

log()  { printf "\033[1;34m[breezyslam]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[breezyslam]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[breezyslam]\033[0m %s\n" "$*" >&2; exit 1; }

# --------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------

arch="$(uname -m)"
if [[ "${arch}" != "aarch64" ]]; then
    die "this script is for Jetson (aarch64); detected ${arch}.
On x86 / Mac install BreezySLAM directly from GitHub with:
    pip install 'git+${BREEZYSLAM_REPO}#subdirectory=python'
(BreezySLAM is not on PyPI; the bare \`pip install breezyslam\`
that you may have tried fails with 'no matching distribution')."
fi

if [[ "${EUID}" -eq 0 ]]; then
    warn "running as root - breezyslam will install system-wide
instead of into your user site-packages. That's fine for a kiosk
unit but unusual for dev. Consider running as the regular user."
fi

PYTHON_EXEC="$(command -v python3)"
if [[ -z "${PYTHON_EXEC}" ]]; then
    die "python3 not on PATH; install it first (sudo apt install -y python3)."
fi

PY_VERSION="$("${PYTHON_EXEC}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Using ${PYTHON_EXEC} (Python ${PY_VERSION})"

# --------------------------------------------------------------------
# 1) Apt build deps
# --------------------------------------------------------------------
#
# breezyslam compiles a small C extension (`_breezyslam`) from
# ./c/* during install. Without python3-dev the compile fails with
# "Python.h: No such file or directory" and the install rolls back
# to a half-broken state - the source tree extracts to
# site-packages but the .so never lands and `import breezyslam`
# raises ImportError on the C extension.

log "Installing apt build deps (sudo password may be required)"
sudo apt update
sudo apt install -y \
    build-essential \
    python3-dev \
    python3-pip \
    git

# --------------------------------------------------------------------
# 2) Clone BreezySLAM (or refresh existing checkout)
# --------------------------------------------------------------------

if [[ -d "${BUILD_DIR}/.git" ]]; then
    log "Reusing existing checkout at ${BUILD_DIR} (git pull to refresh)"
    git -C "${BUILD_DIR}" fetch --depth 1 origin "${BREEZYSLAM_REF}"
    git -C "${BUILD_DIR}" reset --hard FETCH_HEAD
else
    log "Cloning ${BREEZYSLAM_REPO} (${BREEZYSLAM_REF}) into ${BUILD_DIR}"
    rm -rf "${BUILD_DIR}"
    git clone --depth 1 --branch "${BREEZYSLAM_REF}" \
        "${BREEZYSLAM_REPO}" "${BUILD_DIR}"
fi

PY_PKG_DIR="${BUILD_DIR}/python"
if [[ ! -d "${PY_PKG_DIR}" ]]; then
    die "expected ${PY_PKG_DIR} to exist after clone; upstream layout
may have changed. Inspect: ls ${BUILD_DIR}"
fi

# --------------------------------------------------------------------
# 3) pip install (with PEP 668 fallback)
# --------------------------------------------------------------------
#
# Ubuntu 22.04 / JetPack 6 marks the system Python interpreter as
# "externally managed" (PEP 668) so `pip install --user` errors out
# with EXTERNALLY-MANAGED. We retry with --break-system-packages,
# which is the freedesktop-blessed escape hatch for this exact case
# (we're not modifying the system distro packages, only the user's
# site-packages). On older Jetsons that don't enforce PEP 668 the
# first attempt succeeds and we never hit the retry.

install_breezyslam() {
    local extra=("$@")
    log "pip install --user ${extra[*]} ${PY_PKG_DIR}"
    (
        cd "${PY_PKG_DIR}"
        "${PYTHON_EXEC}" -m pip install --user "${extra[@]}" .
    )
}

if ! install_breezyslam ; then
    warn "pip install --user failed; retrying with
--break-system-packages (PEP 668 fallback - safe for user-only
installs on JetPack 6 / Ubuntu 22.04)."
    install_breezyslam --break-system-packages
fi

# --------------------------------------------------------------------
# 4) Smoke test
# --------------------------------------------------------------------
#
# Simply checking `import breezyslam` isn't enough - the C extension
# can fail to import even when the Python package is on disk (wrong
# ABI, missing libstdc++, etc). Try to actually instantiate
# RMHC_SLAM with a tiny map; that's what nina/slam/engine.py does
# at GUI startup, so if this works the GUI works.

log "Smoke-testing the install"
"${PYTHON_EXEC}" - <<'PY'
import sys
try:
    from breezyslam.algorithms import RMHC_SLAM
    from breezyslam.sensors import RPLidarA1
except Exception as exc:
    print(f"  IMPORT FAILED: {exc}")
    sys.exit(1)

try:
    laser = RPLidarA1()
    slam = RMHC_SLAM(laser, 200, 5.0)
    print(f"  RMHC_SLAM constructed: map=200x200 px, world=5 m")
except Exception as exc:
    print(f"  CONSTRUCT FAILED: {exc}")
    sys.exit(1)

print("  OK - breezyslam ready for the Nina SLAM engine")
PY

cat <<EOF

Installed breezyslam from ${BREEZYSLAM_REPO}.

Re-launch the Nina UI and the Map / Perception lidar pane will now
build a real occupancy grid as the bot moves (was rendering
placeholder rasterised scans before).

If the Map screen still says "breezyslam not installed", confirm
the install landed where the GUI's Python looks. Run this WITHOUT
sudo (sudo switches the user to root and root cannot see the
user-site install we just made):

  ${PYTHON_EXEC} -c "import breezyslam; print(breezyslam.__file__)"

Expected path: ~/.local/lib/python${PY_VERSION}/site-packages/breezyslam/...
(or /usr/lib/python3/dist-packages/breezyslam/... if you ran
this script as root in the first place).

The Nina kiosk systemd service runs as the regular user (nina),
not root, so this user-site install is exactly what it needs.
EOF
