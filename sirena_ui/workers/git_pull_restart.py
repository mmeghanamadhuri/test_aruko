"""Discover the Nina repo root and restart the Control Center after a git pull.

The kiosk typically starts via ``scripts/launch-sirena.sh``, which sets
``PYTHONPATH``, Qt plugin paths, and CUDA ``LD_LIBRARY_PATH``. Those stay
in the process environment, so :func:`restart_application` normally
re-executes the same Python + ``sys.argv`` and keeps that environment.

For systemd user units where a full service restart is safer than exec,
set ``NINA_UI_RESTART_CMD`` (e.g. ``systemctl --user restart nina-ui-kiosk``).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("sirena_ui.git_pull_restart")


def repository_root() -> Path:
    """Checkout root (parent of ``sirena_ui/``)."""
    return Path(__file__).resolve().parents[2]


def restart_application() -> None:
    """Restart the Control Center.

    * If ``NINA_UI_RESTART_CMD`` is set, runs that shell command (e.g.
      ``systemctl --user restart nina-ui-kiosk``) and **returns** — the
      caller must quit the Qt event loop and exit so the service can
      replace this process.
    * Otherwise **replaces** this process with ``os.execv`` (same Python
      interpreter and ``sys.argv``; environment is preserved).
    """
    restart_cmd = (os.environ.get("NINA_UI_RESTART_CMD") or "").strip()
    if restart_cmd:
        log.info("Restart via NINA_UI_RESTART_CMD: %s", restart_cmd)
        subprocess.Popen(  # noqa: S603 - operator-configured kiosk hosts only
            restart_cmd,
            shell=True,
            close_fds=True,
            start_new_session=True,
        )
        return

    python = sys.executable
    argv = [python] + sys.argv[1:]
    if len(argv) == 1:
        argv = [python, "-m", "sirena_ui"]
    try:
        os.chdir(repository_root())
    except OSError as exc:
        log.warning("Could not chdir to repo root: %s", exc)
    log.info("Restarting: execv %s %s", python, argv)
    os.execv(python, argv)
