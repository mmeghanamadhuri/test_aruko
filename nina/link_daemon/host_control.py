"""Host (Jetson) power control from nina-link (optional, requires sudoers)."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import Any, Dict

log = logging.getLogger("nina.link_daemon.host_control")


def queue_poweroff() -> Dict[str, Any]:
    """Request OS poweroff in a background thread (HTTP returns immediately).

    Requires passwordless sudo for the nina-link user, e.g. in
    ``/etc/sudoers.d/nina-link``::

        nina ALL=(ALL) NOPASSWD: /sbin/poweroff, /usr/sbin/poweroff, /sbin/shutdown
    """
    def run() -> None:
        for cmd in (
            ("/usr/bin/sudo", "-n", "/sbin/poweroff"),
            ("/usr/bin/sudo", "-n", "/usr/sbin/poweroff"),
        ):
            try:
                r = subprocess.run(cmd, timeout=3, capture_output=True, text=True)
                if r.returncode == 0:
                    log.info("poweroff: %s", " ".join(cmd))
                    return
            except FileNotFoundError:
                continue
            except Exception as exc:
                log.warning("poweroff try %s: %s", cmd, exc)
        try:
            subprocess.Popen(
                ["/usr/bin/sudo", "-n", "/sbin/shutdown", "-h", "now"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("shutdown -h now requested")
        except Exception:
            log.exception("poweroff: all methods failed (configure sudo for poweroff)")

    threading.Thread(target=run, daemon=True, name="nina-poweroff").start()
    return {
        "ok": True,
        "queued": True,
        "message": "Poweroff requested. Host may go down in a few seconds.",
    }
