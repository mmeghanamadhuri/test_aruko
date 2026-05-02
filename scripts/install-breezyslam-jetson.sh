#!/usr/bin/env bash
# Installs `breezyslam` (the 2D occupancy-grid SLAM library Nina
# uses with the RPLIDAR A1) on a Jetson running Ubuntu 22.04 /
# JetPack 6.x.
#
# Why this script exists:
#   - `breezyslam` ships as a source-only PyPI package with a small
#     C extension. On a fresh Jetson without `python3-dev` the pip
#     install fails partway through compiling _breezyslam.cpython-...
#     and leaves the install half-broken; the next time the GUI
#     launches, the SLAM engine logs:
#         "breezyslam not installed (No module named 'breezyslam')"
#     and the Map / Perception lidar pane falls back to the rasteriser
#     (renders the latest scan only, no pose tracking, no map
#     accumulation). Operators reported this as
#         "no BreezySLAM Installed" on the screen
#     with no path to a fix.
#   - Ubuntu 22.04 / JetPack 6 also enforces PEP 668
#     ("externally-managed-environment") on `pip install`, so even
#     when the build deps ARE present the bare `pip install
#     breezyslam` from sirena_ui/requirements.txt errors out unless
#     the operator passes --break-system-packages or sets up a venv.
#     This script handles both cases automatically.
#
# What it does:
#   1. Verifies we're on aarch64 (script aborts on x86 / Mac with a
#      pointer at the regular `pip install breezyslam`).
#   2. apt-installs the build deps (build-essential, python3-dev,
#      python3-pip).
#   3. pip-installs breezyslam --user with --break-system-packages
#      when needed; falls back gracefully on older Jetsons that
#      don't enforce PEP 668.
#   4. Smoke-tests the import + RMHC_SLAM init so the operator sees
#      a green "ok" line, not just a silent "Successfully installed"
#      that may still be broken on Jetson.
#
# Compatibility:
#   - Tested against JetPack 6.x (Ubuntu 22.04) on Jetson Orin Nano /
#     Orin NX with Python 3.10.
#   - JetPack 5.x (Ubuntu 20.04) should also work - the install path
#     is identical, the PEP 668 fallback is harmless.
#   - x86 / Mac dev hosts: don't run this; just `pip install
#     breezyslam` from sirena_ui/requirements.txt.

set -euo pipefail

log()  { printf "\033[1;34m[breezyslam]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[breezyslam]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[breezyslam]\033[0m %s\n" "$*" >&2; exit 1; }

# --------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------

arch="$(uname -m)"
if [[ "${arch}" != "aarch64" ]]; then
    die "this script is for Jetson (aarch64); detected ${arch}.
On x86 / Mac just \`pip install breezyslam\` (sirena_ui's
requirements.txt already lists it)."
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
# ./c/* during pip install. Without python3-dev the compile fails
# with "Python.h: No such file or directory" and the install rolls
# back to a half-broken state - the source tree extracts to
# site-packages but the .so never lands.

log "Installing apt build deps (sudo password may be required)"
sudo apt update
sudo apt install -y \
    build-essential \
    python3-dev \
    python3-pip

# --------------------------------------------------------------------
# 2) pip install (with PEP 668 fallback)
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
    log "pip install --user ${extra[*]} breezyslam"
    "${PYTHON_EXEC}" -m pip install --user "${extra[@]}" \
        'breezyslam>=0.5.0'
}

if ! install_breezyslam ; then
    warn "pip install --user failed; retrying with
--break-system-packages (PEP 668 fallback - safe for user-only
installs on JetPack 6 / Ubuntu 22.04)."
    install_breezyslam --break-system-packages
fi

# --------------------------------------------------------------------
# 3) Smoke test
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

Installed breezyslam.

Re-launch the Nina UI and the Map / Perception lidar pane will now
build a real occupancy grid as the bot moves (was rendering
placeholder rasterised scans before).

If the Map screen still says "breezyslam not installed", confirm
the install landed where the GUI's Python looks:

  ${PYTHON_EXEC} -c "import breezyslam, os; print(breezyslam.__file__)"

The path should be under ~/.local/lib/python${PY_VERSION}/site-packages/
(or /usr/lib/python3/dist-packages/ if you ran this script as root).
EOF
