"""Shared Dynamixel + ActionRunner singleton for link_daemon bridges.

Serial bus access must be serialized: only one of record / motion playback should run
at a time. Use ``bus_lock`` around entire sessions.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Tuple

log = logging.getLogger("nina.link_daemon.dynamixel_bundle")

_bundle: Optional[Tuple[Any, Any, Any]] = None  # (ActionRunner, DynamixelManager, NinaSettings)
_init_lock = threading.Lock()
bus_lock = threading.RLock()


def get_action_runner_bundle() -> Tuple[Any, Any, Any]:
    """Lazy-init the same stack as ``python -m nina.app run-action``.

    Returns ``(action_runner, dynamixel_manager, nina_settings)``.
    """
    global _bundle
    with _init_lock:
        if _bundle is None:
            from nina.app.main import build_app, ensure_motors_ready

            settings, dxl, action_runner, _ss = build_app()
            ensure_motors_ready(dxl)
            _bundle = (action_runner, dxl, settings)
            log.info("Dynamixel bundle: ActionRunner ready")
        return _bundle


def release_bundle_serial() -> None:
    """Close the Dynamixel USB handle and clear the cached bundle.

    nina-link runs as a **separate process** from Sirena UI. Linux USB-serial is typically
    exclusive: if this daemon keeps ``/dev/ttyUSB*`` open, the desktop UI can play audio
    (ALSA) but **cannot move motors**. Call after local playback/recording so Sirena can
    open the bus.

    Safe to call multiple times. Thread-safe with ``get_action_runner_bundle`` via
    ``_init_lock``.
    """
    global _bundle
    with _init_lock:
        if _bundle is None:
            return
        _ar, dxl, _settings = _bundle
        try:
            dxl.close()
        except Exception:
            log.exception("release_bundle_serial: close failed")
        _bundle = None
        log.info("Dynamixel bundle released (USB free for Sirena UI / next nina-link session)")
