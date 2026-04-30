#!/usr/bin/env bash
# Launcher for the Sirena Control Center, invoked by the .desktop entry.
#
# A double-clicked .desktop entry runs in a *very* sparse environment
# (no shell rc files sourced, PATH ~= /usr/local/bin:/usr/bin:/bin, no
# LD_LIBRARY_PATH for CUDA/cuDNN). This script bridges that gap so the
# GUI behaves identically whether you launch it from a terminal with
# ``python3 -m sirena_ui`` or by clicking the desktop icon:
#
#   1. Source ~/.profile and ~/.bashrc when present so PATH /
#      LD_LIBRARY_PATH / PYTHONPATH that the user normally has in
#      a terminal are inherited here too.
#   2. Add Jetson's standard CUDA / cuDNN / TensorRT lib paths to
#      LD_LIBRARY_PATH so PyTorch + Ultralytics + TensorRT can find
#      their .so files. This is what /etc/profile.d/cuda.sh does
#      interactively but is missing for non-login shells.
#   3. Force ``QT_QPA_PLATFORM=xcb`` because PyQt5 on Jetson / older
#      Ubuntu builds doesn't always have a working Wayland plugin.
#   4. Append all stdout + stderr to ~/.cache/sirena/launch.log so
#      any error is captured even when the desktop launcher discards
#      the process output. The file is rotated to ~50 KB to stop it
#      growing forever on a long-running install.
#   5. If python exits non-zero, pop a zenity / notify-send / xmessage
#      dialog so the operator gets *something* visible instead of
#      "double-clicked the icon and nothing happened".
#
# You can override the Python interpreter:
#   SIRENA_PYTHON=/path/to/python3 ./scripts/launch-sirena.sh

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${HOME}/.cache/sirena"
LOG_FILE="${LOG_DIR}/launch.log"
mkdir -p "${LOG_DIR}"

# Trim the log so it doesn't grow without bound between runs.
if [[ -f "${LOG_FILE}" ]]; then
    LOG_BYTES=$(stat -c %s "${LOG_FILE}" 2>/dev/null || echo 0)
    if [[ "${LOG_BYTES}" -gt 51200 ]]; then
        tail -c 32768 "${LOG_FILE}" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
    fi
fi

# Bring across whatever the operator's interactive shell normally
# exposes (PATH, LD_LIBRARY_PATH for CUDA, PYTHONPATH if the user has
# any custom dirs). ``set +u`` first because both .profile and .bashrc
# routinely reference unset vars.
set +u
# shellcheck disable=SC1091
[[ -r "${HOME}/.profile" ]]      && . "${HOME}/.profile"
# shellcheck disable=SC1091
[[ -r "${HOME}/.bash_profile" ]] && . "${HOME}/.bash_profile"
# shellcheck disable=SC1091
[[ -r "${HOME}/.bashrc" ]]       && . "${HOME}/.bashrc"
set -u

# Belt-and-braces: explicitly add Jetson CUDA / cuDNN / TensorRT lib
# paths so PyTorch + Ultralytics can find libcudart, libcublas,
# libnvinfer etc. Harmless on hosts that don't have these dirs.
for _dir in \
    "/usr/local/cuda/lib64" \
    "/usr/lib/aarch64-linux-gnu/tegra" \
    "/usr/lib/aarch64-linux-gnu" \
    "${HOME}/.local/lib"
do
    if [[ -d "${_dir}" ]]; then
        case ":${LD_LIBRARY_PATH:-}:" in
            *":${_dir}:"*) : ;; # already on path
            *) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${_dir}" ;;
        esac
    fi
done

# Always make sure ``~/.local/bin`` is on PATH so user-installed
# console scripts (e.g. mpg123 if pip-installed) are reachable.
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) : ;;
    *) export PATH="${HOME}/.local/bin:${PATH}" ;;
esac

PYTHON_BIN="${SIRENA_PYTHON:-/usr/bin/python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    # Fall back to whatever is on PATH if the hard-coded interpreter
    # is missing (some Orin images ship python3 only as a symlink in
    # /usr/bin without an /etc-pinned full path).
    PYTHON_BIN="$(command -v python3 || true)"
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
# Ensure the repo is importable even if the user has nuked PYTHONPATH.
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# ---------------------------------------------------------------------
# Kiosk-mode panel: force 1024 x 600 on the connected display.
#
# Symptom this fixes: when launched by the systemd user unit on boot
# the GUI comes up huge / stretched, with parts running off the edges
# of the 10.1" panel. Same code launched manually from a terminal looks
# correct. Root cause is that the cheap HDMI panel reports a generic
# EDID (often 1920x1080) and X11 happily renders into that virtual
# surface; ``showFullScreen()`` then sizes our window to that surface,
# but every screen in sirena_ui/ is laid out for 1024 x 600 design
# pixels - hence the overflow.
#
# Fix: ask xrandr to put the panel into a real 1024 x 600 mode before
# we hand off to Qt. Only runs in kiosk mode (``NINA_UI_FULLSCREEN=1``)
# so dev workflows on a normal 1920x1080 / 4K monitor aren't downscaled
# behind the operator's back.
#
# The whole block is best-effort: missing xrandr / unsupported mode /
# locked panel all fall through silently with a log line; the GUI still
# launches, just at whatever resolution the panel was already in.
# ---------------------------------------------------------------------
_force_panel_resolution_1024x600() {
    case "${NINA_UI_FULLSCREEN:-}" in
        1|true|TRUE|yes|YES|y|Y|on|ON) ;;
        *) return 0 ;;
    esac
    if ! command -v xrandr >/dev/null 2>&1; then
        echo "[panel] xrandr not installed - skipping resolution forcing" >&2
        return 0
    fi
    if [[ -z "${DISPLAY:-}" ]]; then
        echo "[panel] DISPLAY unset - skipping resolution forcing" >&2
        return 0
    fi

    local output
    output="$(xrandr --query 2>/dev/null \
              | awk '/ connected/ {print $1; exit}')"
    if [[ -z "${output}" ]]; then
        echo "[panel] no connected output reported by xrandr" >&2
        return 0
    fi

    # Try the existing mode first - if the panel's EDID already exposes
    # a 1024x600 mode, this is the one xrandr trusts most.
    if xrandr --output "${output}" --mode 1024x600 >/dev/null 2>&1; then
        echo "[panel] forced ${output} -> 1024x600 (existing mode)"
        return 0
    fi

    # Otherwise inject a CVT-derived 1024x600 modeline and retry. Values
    # come from ``cvt 1024 600 60`` and are stable across xrandr
    # versions; the ``|| true`` lets the call no-op if the mode is
    # already registered from a previous run.
    local mode_name="1024x600_60.00"
    local modeline="49.00 1024 1072 1168 1312 600 603 613 624 -hsync +vsync"
    xrandr --newmode "${mode_name}" ${modeline} 2>/dev/null || true
    xrandr --addmode "${output}" "${mode_name}" 2>/dev/null || true
    if xrandr --output "${output}" --mode "${mode_name}" >/dev/null 2>&1; then
        echo "[panel] forced ${output} -> ${mode_name} (custom CVT modeline)"
        return 0
    fi

    echo "[panel] WARNING: could not force ${output} to 1024x600 - GUI may overflow" >&2
}

EXIT=0
{
    echo
    echo "===== launching $(date '+%Y-%m-%d %H:%M:%S') ====="
    echo "REPO_ROOT=${REPO_ROOT}"
    echo "PYTHON=${PYTHON_BIN}"
    echo "DISPLAY=${DISPLAY:-<unset>}"
    echo "QT_QPA_PLATFORM=${QT_QPA_PLATFORM}"
    echo "NINA_UI_FULLSCREEN=${NINA_UI_FULLSCREEN:-<unset>}"
    echo "PATH=${PATH}"
    echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
    echo "PYTHONPATH=${PYTHONPATH}"
    _force_panel_resolution_1024x600
    if [[ -z "${PYTHON_BIN}" ]]; then
        echo "FATAL: no python3 interpreter found on PATH" >&2
        exit 127
    fi
    cd "${REPO_ROOT}"
    "${PYTHON_BIN}" -m sirena_ui
    EXIT=$?
    echo "exit code: ${EXIT}"
} >> "${LOG_FILE}" 2>&1
EXIT=${EXIT:-$?}

# When launched from a terminal the operator sees the traceback. When
# launched from the desktop they see a dead icon, so try every dialog
# tool in turn until one of them sticks.
if [[ "${EXIT}" -ne 0 ]]; then
    MSG="Sirena Control Center failed to start (exit ${EXIT}).

See the last 50 lines of the launch log:
  tail -n 50 \"${LOG_FILE}\""
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title "Sirena" --text "${MSG}" --no-wrap >/dev/null 2>&1 || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send -u critical "Sirena failed to start" "${MSG}" >/dev/null 2>&1 || true
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center "${MSG}" >/dev/null 2>&1 || true
    fi
fi

exit "${EXIT}"
