"""
Translate raw runtime errors from the workers into actionable hints
that surface in the GUI status / dialog.

The Jetson rarely fails because something is *broken*; it usually
fails because the user isn't in `dialout`, or the repo was cloned
with `sudo` so manifest/recording writes hit `EACCES`. Both have
one-line fixes - we just need to tell the user which one.
"""

from __future__ import annotations

import getpass
import grp
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nina.config.settings import NinaSettings


def _user_in_dialout(user: str) -> bool:
    try:
        return "dialout" in [g.gr_name for g in grp.getgrall() if user in g.gr_mem]
    except Exception:
        return False


def explain_error(exc: Exception, settings: "NinaSettings") -> str:
    raw = str(exc)
    user = getpass.getuser()
    serial_port = settings.serial_port

    looks_like_serial = (
        serial_port in raw or "ttyUSB" in raw or "ttyACM" in raw or "/dev/tty" in raw
    )

    # 1) FTDI cable missing / different device name
    no_such = (
        "No such file or directory" in raw
        or "could not open port" in raw
        or "FileNotFound" in raw
    )
    if no_such and looks_like_serial:
        return (
            f"Cannot open {serial_port} - the kernel does not see that device.\n\n"
            "Most likely the FTDI cable to the Dynamixel bus is unplugged or "
            "enumerated under a different name. Diagnose on the Jetson:\n\n"
            "    lsusb | grep -i ftdi          # does the OS see the adapter?\n"
            "    ls /dev/ttyUSB* /dev/ttyACM*  # which serial nodes exist?\n"
            "    dmesg | tail -20              # last USB events\n\n"
            "If it came up as a different name (e.g. /dev/ttyUSB1), tell the "
            "app to use it before launching:\n"
            "    export NINA_DXL_PORT=/dev/ttyUSB1"
        )

    # 2) Permission denied on the serial port -> dialout group
    if "Permission" in raw and looks_like_serial:
        hint = (
            f"Permission denied on {serial_port}. Add your user to the "
            f"'dialout' group (no sudo at runtime needed) and log out / log "
            f"back in:\n"
            f"    sudo usermod -aG dialout {user}\n"
            f"Verify after re-login:  groups | grep dialout"
        )
        if _user_in_dialout(user):
            hint += (
                "\n\nYour user is already in 'dialout', so the desktop session "
                "just hasn't picked up the new group yet - log out and back in "
                "(or reboot) and try again."
            )
        return hint

    # 3) Permission denied writing the recording / manifest -> chown
    if isinstance(exc, PermissionError):
        target = raw
        for path in (settings.recordings_dir, settings.manifest_path):
            if str(path) in raw:
                target = str(path)
                break
        repo_root = settings.actions_dir.parent.parent
        return (
            f"Permission denied while writing {target}.\n"
            f"The repo is probably owned by another user (often 'root' if it "
            f"was cloned with sudo). Fix the ownership with:\n"
            f"    sudo chown -R {user}:{user} {repo_root}"
        )

    return raw
