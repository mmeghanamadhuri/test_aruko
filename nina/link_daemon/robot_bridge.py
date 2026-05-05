"""Optional BLDC control from nina-link HTTP (same NavigationManager as desktop UI).

Enable with ``NINA_LINK_ENABLE_ROBOT_BRIDGE=1``. Do not run Sirena UI Drive screen
simultaneously — both compete for GPIO / the navigation manager.

Momentary moves run on a worker thread so FastAPI returns immediately.
Navigation init is validated **synchronously** before queuing motion so HTTP clients
see ``ok: false`` when the UART/Pi bridge is down instead of a silent no-op.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("nina.link_daemon.robot_bridge")

_REPO_ROOT = Path(__file__).resolve().parents[2]

_motion_lock = threading.Lock()
_nav = None  # lazy NavigationManager
_nav_init_lock = threading.Lock()

# HTTP momentary drive refuses while autonomy holds the wheels (matches desktop expectation).
_autonomy_blocks_drive = False

_last_drive_error_lock = threading.Lock()
_last_drive_error: Optional[str] = None


def set_autonomy_blocks_drive(on: bool) -> None:
    global _autonomy_blocks_drive
    _autonomy_blocks_drive = bool(on)


def autonomy_blocks_drive() -> bool:
    return _autonomy_blocks_drive


def navigation_for_autonomy():
    """Same lazy NavigationManager singleton as ``momentary_drive`` / E-stop."""
    return _navigation()


def _set_last_drive_error(msg: Optional[str]) -> None:
    global _last_drive_error
    with _last_drive_error_lock:
        _last_drive_error = (msg or "").strip() or None


def peek_last_drive_error() -> Optional[str]:
    with _last_drive_error_lock:
        return _last_drive_error


def warmup_robot_navigation() -> None:
    """Background init so the first tablet tap does not pay UART/PING latency.

    Safe to call multiple times; failures are logged (companion still sees status).
    """

    def run() -> None:
        try:
            _navigation()
            log.info("Robot bridge: warmup navigation OK")
        except Exception:
            log.exception("Robot bridge: warmup navigation failed")

    threading.Thread(target=run, daemon=True, name="nina-nav-warmup").start()


def _navigation():
    global _nav
    with _nav_init_lock:
        if _nav is None:
            from nina.config.settings import load_settings
            from nina.controllers.navigation_factory import build_navigation_manager

            settings = load_settings(_REPO_ROOT)
            nm = build_navigation_manager(settings.navigation)
            nm.initialize()
            _nav = nm
            log.info("Robot bridge: NavigationManager ready")
    return _nav


def momentary_drive(
    *,
    direction: str,
    duration_ms: int,
    speed_percent: int,
) -> Dict[str, Any]:
    valid = frozenset({"forward", "back", "left", "right", "stop"})
    if direction not in valid:
        return {"ok": False, "error": f"invalid direction {direction!r}"}

    duration_ms = max(50, min(5000, int(duration_ms)))
    speed_percent = max(5, min(100, int(speed_percent)))
    d_sec = duration_ms / 1000.0

    if autonomy_blocks_drive():
        return {
            "ok": False,
            "error": "autonomy active — disable autonomy before HTTP drive",
        }

    try:
        _navigation()
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("momentary_drive: navigation init failed: %s", msg)
        _set_last_drive_error(msg)
        return {"ok": False, "error": msg}

    def run() -> None:
        try:
            nav = _navigation()
            with _motion_lock:
                if direction == "stop":
                    nav.stop()
                    _set_last_drive_error(None)
                    return
                if direction == "forward":
                    nav.forward(speed_percent=speed_percent)
                    time.sleep(d_sec)
                    nav.stop()
                elif direction == "back":
                    nav.backward(speed_percent=speed_percent)
                    time.sleep(d_sec)
                    nav.stop()
                elif direction == "left":
                    nav.turn_left(speed_percent=speed_percent, duration=d_sec)
                elif direction == "right":
                    nav.turn_right(speed_percent=speed_percent, duration=d_sec)
            _set_last_drive_error(None)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            log.exception("momentary_drive %s", direction)
            _set_last_drive_error(msg)

    threading.Thread(target=run, daemon=True, name=f"nina-drive-{direction}").start()
    return {"ok": True, "queued": True, "direction": direction, "duration_ms": duration_ms}


def navigation_hw_status() -> Dict[str, Any]:
    """Probe lazy NavigationManager init (same path as first drive command).

    Returns ``connected`` so the companion app can mirror Sirena UI's BLDC pill.
    When connected, includes ``invert_left`` / ``invert_right`` (mirrors Qt Drive).
    """
    err = peek_last_drive_error()
    try:
        nav = _navigation()
        body: Dict[str, Any] = {
            "ok": True,
            "connected": True,
            "message": "BLDC L+R connected",
            "invert_left": bool(nav.get_invert_left()),
            "invert_right": bool(nav.get_invert_right()),
        }
        if err:
            body["last_drive_error"] = err
        return body
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("navigation_hw_status: %s", msg)
        out: Dict[str, Any] = {
            "ok": True,
            "connected": False,
            "message": msg,
            "invert_left": False,
            "invert_right": False,
        }
        if err:
            out["last_drive_error"] = err
        return out


def set_wheel_invert(
    *,
    left: Optional[bool] = None,
    right: Optional[bool] = None,
) -> Dict[str, Any]:
    """Runtime per-wheel polarity (same calls as Sirena UI Drive Flip L/R)."""
    if left is None and right is None:
        return {"ok": False, "error": "no fields: set left and/or right"}

    def run() -> None:
        nav = _navigation()
        if left is not None:
            nav.set_invert_left(bool(left))
        if right is not None:
            nav.set_invert_right(bool(right))

    try:
        run()
        nav = _navigation()
        return {
            "ok": True,
            "invert_left": bool(nav.get_invert_left()),
            "invert_right": bool(nav.get_invert_right()),
        }
    except Exception as exc:
        log.exception("set_wheel_invert")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def emergency_stop() -> Dict[str, Any]:
    try:
        _navigation()
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("emergency_stop: navigation init failed: %s", msg)
        return {"ok": False, "error": msg}

    def run() -> None:
        try:
            nav = _navigation()
            with _motion_lock:
                nav.emergency_stop()
            _set_last_drive_error(None)
        except Exception:
            log.exception("emergency_stop")

    threading.Thread(target=run, daemon=True, name="nina-estop").start()
    return {"ok": True, "queued": True}
