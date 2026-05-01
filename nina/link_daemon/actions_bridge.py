"""Optional Dynamixel playback via nina-link (same stack as ``python -m nina.app run-action``).

Enable with ``NINA_LINK_ENABLE_ACTION_BRIDGE=1``. Do not run while Sirena UI or another
process holds the serial bus.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("nina.link_daemon.actions_bridge")

_bundle: Optional[Tuple[Any, Any]] = None  # (ActionRunner, DynamixelManager)
_lock = threading.Lock()


def _runner_bundle() -> Tuple[Any, Any]:
    global _bundle
    with _lock:
        if _bundle is None:
            from nina.app.main import build_app, ensure_motors_ready

            _settings, dxl, action_runner, _ss = build_app()
            ensure_motors_ready(dxl)
            _bundle = (action_runner, dxl)
            log.info("Action bridge: ActionRunner ready")
        return _bundle


def play_named_action(action_name: str) -> Dict[str, Any]:
    name = action_name.strip()
    if not name:
        return {"ok": False, "error": "empty action name"}
    ar, _dxl = _runner_bundle()

    def run() -> None:
        try:
            ar.run_named_action(name)
        except Exception:
            log.exception("play_named_action %s", name)

    threading.Thread(target=run, daemon=True, name=f"nina-action-{name}").start()
    return {"ok": True, "queued": True, "action": name}
