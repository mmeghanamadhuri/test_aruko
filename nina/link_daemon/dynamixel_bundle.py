"""Shared Dynamixel + ActionRunner singleton for link_daemon bridges.

Serial bus access must be serialized: only one of record / motion playback should run
at a time. Use ``bus_lock`` around entire sessions.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Tuple

log = logging.getLogger("nina.link_daemon.dynamixel_bundle")

_bundle: Optional[Tuple[Any, Any]] = None  # (ActionRunner, DynamixelManager)
_init_lock = threading.Lock()
bus_lock = threading.RLock()


def get_action_runner_bundle() -> Tuple[Any, Any]:
    """Lazy-init the same stack as ``python -m nina.app run-action``."""
    global _bundle
    with _init_lock:
        if _bundle is None:
            from nina.app.main import build_app, ensure_motors_ready

            _settings, dxl, action_runner, _ss = build_app()
            ensure_motors_ready(dxl)
            _bundle = (action_runner, dxl)
            log.info("Dynamixel bundle: ActionRunner ready")
        return _bundle
