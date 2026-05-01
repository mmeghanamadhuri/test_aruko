#!/usr/bin/env bash
# Installs librealsense2 + the matching `pyrealsense2` Python bindings
# on a Jetson (aarch64) so the RealSense D435 depth camera works with
# the Nina autonomous-navigation stack.
#
# Why this script exists:
#   - Intel does not publish a `pyrealsense2` aarch64 wheel on PyPI,
#     so `pip install pyrealsense2` (which is what
#     sirena_ui/requirements.txt does on x86) silently no-ops on the
#     Jetson - except its platform marker excludes it cleanly. Either
#     way, the depth camera ends up disabled with the message
#     "pyrealsense2 not installed" and the autonomy stack runs lidar-
#     only.
#   - Building from source against the JetPack kernel works reliably
#     once you know the right CMake flags. This script captures those
#     flags so you don't have to chase them across librealsense
#     issues.
#
# What it does NOT do:
#   - Install the kernel-level patches that enable hardware-accel
#     metadata + USB performance tuning. Those are documented in
#     librealsense's `doc/installation_jetson.md` under "kernel patch"
#     and require rebooting into a custom kernel - way too invasive
#     for a default install. Without the patches the D435 still works
#     for our use case (autonomy reads depth at 15 fps which is well
#     within the user-mode budget). If you want max throughput, run
#     librealsense's `scripts/patch-realsense-ubuntu-L4T.sh`
#     separately.
#
# Compatibility:
#   - Tested against JetPack 6.x (Ubuntu 22.04) on Jetson Orin Nano /
#     Orin NX with the Nina stack's Python 3.10 venv.
#   - JetPack 5.x (Ubuntu 20.04) should work with the same flags but
#     hasn't been re-verified; the build is what matters and it picks
#     up the system Python automatically.
#   - The script aborts cleanly on x86 / Mac so it's safe to leave in
#     scripts/ - "wrong machine" never silently does the wrong thing.
#
# After it finishes:
#   - `python3 -c "import pyrealsense2 as rs; print(rs.__version__)"`
#     should print a version string.
#   - Re-launch the Nina UI; the Drive / Map screen "Autonomous mode"
#     toggle will now light up the Depth pill green when enabled.

set -euo pipefail

REALSENSE_VERSION="${REALSENSE_VERSION:-v2.55.1}"
BUILD_DIR="${BUILD_DIR:-/tmp/librealsense-${REALSENSE_VERSION}}"
JOBS="${JOBS:-$(nproc)}"

log()  { printf "\033[1;34m[realsense]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[realsense]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[realsense]\033[0m %s\n" "$*" >&2; exit 1; }

# --------------------------------------------------------------------
# Pre-flight
# --------------------------------------------------------------------

arch="$(uname -m)"
if [[ "${arch}" != "aarch64" ]]; then
    die "this script is for Jetson (aarch64); detected ${arch}.
On x86 / Mac just \`pip install pyrealsense2\` (sirena_ui's
requirements.txt already does that for non-aarch64 hosts)."
fi

if [[ "${EUID}" -eq 0 ]]; then
    warn "running as root - the Python bindings will install system-
wide instead of into your user venv. That's fine for a kiosk
but unusual for dev. Consider running as the regular user."
fi

if ! command -v cmake >/dev/null 2>&1; then
    die "cmake not installed. Run: sudo apt install -y cmake build-essential"
fi

if ! command -v git >/dev/null 2>&1; then
    die "git not installed. Run: sudo apt install -y git"
fi

# --------------------------------------------------------------------
# 1) Apt deps (build-time + udev rules + libusb)
# --------------------------------------------------------------------

log "Installing apt build deps (sudo password may be required)"
sudo apt update
sudo apt install -y \
    build-essential cmake git pkg-config \
    libusb-1.0-0-dev libudev-dev libssl-dev \
    libgtk-3-dev libglfw3-dev libgl1-mesa-dev libglu1-mesa-dev \
    python3-dev python3-pip

# --------------------------------------------------------------------
# 2) Source checkout
# --------------------------------------------------------------------

if [[ -d "${BUILD_DIR}" ]]; then
    log "Reusing existing checkout at ${BUILD_DIR}"
else
    log "Cloning librealsense ${REALSENSE_VERSION} into ${BUILD_DIR}"
    git clone --depth 1 --branch "${REALSENSE_VERSION}" \
        https://github.com/IntelRealSense/librealsense.git \
        "${BUILD_DIR}"
fi

# --------------------------------------------------------------------
# 3) udev rules so non-root processes can open the camera
# --------------------------------------------------------------------

log "Installing udev rules (puts USB device at perms 0666 for plugdev)"
pushd "${BUILD_DIR}" >/dev/null
sudo cp config/99-realsense-libusb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
popd >/dev/null

# --------------------------------------------------------------------
# 4) CMake build
# --------------------------------------------------------------------
#
# Flags chosen for the Nina autonomy use case:
#   BUILD_PYTHON_BINDINGS=ON  - we need pyrealsense2
#   BUILD_EXAMPLES=OFF        - saves ~5 min build time and we don't
#                                ship realsense-viewer on the kiosk
#   BUILD_GRAPHICAL_EXAMPLES=OFF - same
#   FORCE_RSUSB_BACKEND=ON    - skip the kernel UVC patches; pure
#                                user-mode backend is enough at 15 fps
#   PYTHON_EXECUTABLE         - force the right Python so the .so
#                                links against the same interp the
#                                Nina venv uses

build_dir="${BUILD_DIR}/build"
mkdir -p "${build_dir}"
pushd "${build_dir}" >/dev/null

PYTHON_EXEC="$(command -v python3)"
log "Configuring CMake (PYTHON_EXECUTABLE=${PYTHON_EXEC})"
cmake .. \
    -DBUILD_PYTHON_BINDINGS=ON \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_GRAPHICAL_EXAMPLES=OFF \
    -DBUILD_SHARED_LIBS=ON \
    -DFORCE_RSUSB_BACKEND=ON \
    -DPYTHON_EXECUTABLE="${PYTHON_EXEC}" \
    -DCMAKE_BUILD_TYPE=Release

log "Building (this takes ~10-20 min on Orin Nano with JOBS=${JOBS})"
make -j"${JOBS}"

log "Installing librealsense + pyrealsense2 system-wide"
sudo make install
sudo ldconfig

popd >/dev/null

# --------------------------------------------------------------------
# 4.5) Land the Python .so files where Python can actually find them
# --------------------------------------------------------------------
#
# librealsense's `make install` is buggy in a specific way for the
# Python bindings: it installs the C library (librealsense2.so) and
# the cmake config files cleanly, but on the BUILD_PYTHON_BINDINGS=ON
# path it leaves the actual Python .so files behind in the build
# tree. Worse, on JetPack 6 / Ubuntu 22.04 the install AT THE SAME
# TIME creates an empty `pyrealsense2/` directory on the Python
# search path, which Python 3.10 then treats as a NAMESPACE PACKAGE
# (PEP 420). `import pyrealsense2` succeeds, returns a package with
# `__file__: None` and zero attributes, and our driver's
# `_import_pyrealsense2()` correctly reports it as broken - but the
# user has no idea why.
#
# This step closes the loop: find the .so files the build produced,
# pick the directory where the (empty) pyrealsense2/ namespace
# package lives, copy the .so files in, and drop a re-export
# __init__.py so `from pyrealsense2 import pipeline` resolves
# correctly. Idempotent: re-running just overwrites with the same
# content.

log "Locating the Python bindings the build produced"
PY_BUILD_DIR="${build_dir}/Release"
mapfile -t PY_SO_FILES < <(
    find "${PY_BUILD_DIR}" -maxdepth 1 -type f \
        \( -name 'pyrealsense2*.so' -o -name 'pyrsutils*.so' \) 2>/dev/null
)
if [[ "${#PY_SO_FILES[@]}" -eq 0 ]]; then
    warn "No Python .so files found under ${PY_BUILD_DIR}.
The cmake build produced no Python bindings - likely python3-dev
headers were missing at configure time. Re-run with:
    sudo apt install -y python3-dev
    rm -rf ${BUILD_DIR}/build
    bash $0"
    # Don't `die` - let the smoke test surface the failure mode in
    # the operator's own words.
else
    PY_DEST="$(${PYTHON_EXEC} -c '
import sys, os
# Prefer an existing pyrealsense2/ directory on sys.path - that is
# where librealsense_make_install left a (probably empty) namespace
# package; landing the .so there is the least invasive fix.
for p in sys.path:
    if not p:
        continue
    cand = os.path.join(p, "pyrealsense2")
    if os.path.isdir(cand):
        print(cand)
        sys.exit(0)
# No existing dir - create one in the canonical Debian / Ubuntu
# system-Python dist-packages location so subsequent runs see it.
print("/usr/lib/python3/dist-packages/pyrealsense2")
')"
    log "Landing Python bindings into ${PY_DEST}"
    sudo mkdir -p "${PY_DEST}"
    for so in "${PY_SO_FILES[@]}"; do
        sudo cp "${so}" "${PY_DEST}/"
        log "  installed $(basename "${so}")"
    done

    # Write a re-export __init__.py if the directory doesn't already
    # have a non-empty one. We don't clobber an existing __init__.py
    # because some librealsense packagings (Debian's python3-
    # pyrealsense2, when that ever ships) might author a richer one;
    # if the file is empty (the namespace-package case the user
    # actually hit) we replace it.
    INIT_FILE="${PY_DEST}/__init__.py"
    if [[ ! -s "${INIT_FILE}" ]]; then
        sudo tee "${INIT_FILE}" >/dev/null <<'INITPY'
"""pyrealsense2 re-export shim.

Auto-generated by scripts/install-realsense-jetson.sh because
librealsense's `make install` doesn't ship a working __init__.py.
Re-exports every public symbol from the C-extension submodule so
`import pyrealsense2 as rs ; rs.pipeline()` works the way x86 wheel
users expect.
"""
from .pyrealsense2 import *  # noqa: F401,F403
INITPY
        log "Wrote re-export __init__.py at ${INIT_FILE}"
    else
        log "Existing ${INIT_FILE} preserved (already populated)"
    fi
fi

PY_VER_TAG="$(${PYTHON_EXEC} -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"

# Clean up the legacy .pth file an earlier version of this script may
# have written. With the .so files landed directly into the package
# dir above, the .pth shim is no longer needed - and the previous
# version had a bug where it wrote a useless python2.7 path on
# JetPack 6 systems that have both python2.7 (legacy) and python3.10
# (Nina venv). Removing it removes a bit of noise from `python3 -v`
# diagnostics later.
LEGACY_PTH="$(${PYTHON_EXEC} -c 'import site, os; print(os.path.join(site.getusersitepackages(), "pyrealsense2-system.pth"))')"
if [[ -f "${LEGACY_PTH}" ]]; then
    rm -f "${LEGACY_PTH}"
    log "Removed legacy .pth shim ${LEGACY_PTH} (no longer needed)"
fi

# --------------------------------------------------------------------
# 6) Smoke test
# --------------------------------------------------------------------
#
# Don't probe rs.__version__ - that attribute isn't exposed on every
# librealsense build and we'd report a false failure on installs that
# actually work (the previous version of this script did exactly that).
# Instead, confirm the module loads AND has the public symbols we
# actually use from it (rs.pipeline, rs.config, rs.stream) - if any
# are missing the bindings are broken in a way that matters for Nina.

log "Smoke-testing the import"
#
# pyrealsense2 ships in two package layouts depending on the
# librealsense / cmake version. Both are valid and both work for our
# driver (`nina/sensors/realsense_d435.py` calls _import_pyrealsense2()
# which tries each in turn) but the smoke test needs to recognise
# both so it doesn't false-FAIL when the bindings landed in the
# submodule layout.
#
#   Layout A (flat / re-exported): `import pyrealsense2 as rs` gives
#       direct access to rs.pipeline, rs.context, rs.stream, ...
#   Layout B (submodule-only):     `import pyrealsense2 as rs` gives
#       a near-empty package; the C symbols live at
#       `pyrealsense2.pyrealsense2`.
#
SMOKE_PY="$(cat <<'PY'
import importlib, sys

REQUIRED = ("pipeline", "config", "stream", "format")

def _probe(modname):
    try:
        m = importlib.import_module(modname)
    except Exception as exc:
        return None, f"import {modname}: {exc}"
    missing = [n for n in REQUIRED if not hasattr(m, n)]
    if missing:
        return None, f"{modname} imported, missing symbols: {missing}"
    return m, modname

mod, where = _probe("pyrealsense2")
if mod is None:
    inner, inner_where = _probe("pyrealsense2.pyrealsense2")
    if inner is not None:
        mod, where = inner, inner_where + " (submodule layout)"

if mod is None:
    print(f"FAIL: pyrealsense2 not usable. Last error: {where}",
          file=sys.stderr)
    sys.exit(1)

ver = getattr(mod, "__version__", None) or "unknown"
print(f"pyrealsense2 OK at {where} (version: {ver})")
PY
)"

if "${PYTHON_EXEC}" -c "${SMOKE_PY}"; then
    log "Done. Plug in the D435 (USB 3 port!) and re-launch the Nina UI."
    log "The Map / Drive screen Autonomous-mode toggle will now light"
    log "the Depth pill green when enabled."
else
    die "pyrealsense2 import or symbol check failed - see the message
above. Confirm /usr/local/lib/${PY_VER_TAG}/dist-packages contains a
pyrealsense2/ folder with EITHER an __init__.py that re-exports the
C symbols OR a pyrealsense2.cpython-*.so submodule. If only the .so
is there, our driver handles it via the submodule fallback - the
smoke test above just couldn't see it."
fi
