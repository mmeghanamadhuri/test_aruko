"""Optional Dynamixel playback via nina-link (same stack as ``python -m nina.app run-action``).

Enable with ``NINA_LINK_ENABLE_ACTION_BRIDGE=1``. Do not run while Sirena UI or another
process holds the serial bus.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from nina.link_daemon.dynamixel_bundle import bus_lock, get_action_runner_bundle

log = logging.getLogger("nina.link_daemon.actions_bridge")


def play_named_action(action_name: str) -> Dict[str, Any]:
    name = action_name.strip()
    if not name:
        return {"ok": False, "error": "empty action name"}
    ar, _dxl = get_action_runner_bundle()

    def run() -> None:
        try:
            with bus_lock:
                # Match sirena_ui PlaybackWorker defaults (smooth path, half speed like desktop UI).
                ar.run_named_action(
                    name,
                    smooth=True,
                    sub_hz=50.0,
                    max_speed=1023,
                    speed=0.5,
                )
        except Exception:
            log.exception("play_named_action %s", name)

    threading.Thread(target=run, daemon=True, name=f"nina-action-{name}").start()
    return {"ok": True, "queued": True, "action": name}
