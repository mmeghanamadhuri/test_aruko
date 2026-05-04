"""Headless autonomous pilot for nina-link (mirrors ``sirena_ui.workers.AutonomyController`` core behaviour)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from nina.config.settings import AutonomySettings
from nina.navigation.autonomous_pilot import AutonomousPilot, PilotState, SensorBundle
from nina.sensors.gp2y0e02b import GP2Y0E02B
from nina.sensors.hcsr04 import HCSR04Array
from nina.sensors.types import DepthFrame, IRReading, LidarScan, SensorHealth, UltrasonicReading

from nina.link_daemon import depth_bridge
from nina.link_daemon import slam_bridge

log = logging.getLogger("nina.link_daemon.autonomy_bridge")

_lock = threading.RLock()
_enabled = False
_pilot: Optional[AutonomousPilot] = None
_health = SensorHealth()
_ultras = HCSR04Array()
_ir = GP2Y0E02B()
_last_pilot: Optional[Dict[str, Any]] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _pilot_state_dict(state: PilotState) -> Dict[str, Any]:
    return {
        "running": state.running,
        "last_action": state.last_action,
        "last_reason": state.last_reason,
        "field": dict(state.field_snapshot),
        "ticks": state.ticks,
        "started_at": state.started_at,
    }


def _on_pilot_state(state: PilotState) -> None:
    global _last_pilot
    with _lock:
        _last_pilot = _pilot_state_dict(state)


class _NavDriveAdapter:
    """Structural match for ``AutonomousPilot`` drive seam using ``NavigationManager``."""

    def __init__(self, nav: Any) -> None:
        self._nav = nav
        self._brake = False

    def set_brake(self, on: bool) -> None:
        self._brake = bool(on)
        if on:
            try:
                self._nav.stop()
            except Exception:
                pass

    def stop(self) -> None:
        try:
            self._nav.stop()
        except Exception:
            pass

    def drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        if self._brake:
            return
        ldir = "forward" if left_dir == "forward" else "backward"
        rdir = "forward" if right_dir == "forward" else "backward"
        ls = max(0, min(100, int(left_speed)))
        rs = max(0, min(100, int(right_speed)))
        try:
            self._nav.set_wheels(
                left_dir=ldir,
                left_speed=ls,
                right_dir=rdir,
                right_speed=rs,
            )
        except Exception:
            log.exception("drive_wheels")


def _safe_open(fn: Callable[[], None], label: str) -> Tuple[bool, str]:
    try:
        fn()
        return True, f"{label} ready"
    except Exception as exc:
        log.warning("%s open failed: %s", label, exc)
        return False, f"{label}: {exc}"


def _safe_read_ultras(ok: bool) -> List[UltrasonicReading]:
    if not ok:
        return []
    try:
        return list(_ultras.read_all())
    except Exception:
        return []


def _safe_read_ir(ok: bool) -> Optional[IRReading]:
    if not ok:
        return None
    try:
        return _ir.read()
    except Exception:
        return None


def _safe_read_depth(ok: bool) -> Optional[DepthFrame]:
    if not ok:
        return None
    try:
        return depth_bridge.shared_camera().read()
    except Exception:
        return None


def _disable_internal() -> None:
    global _enabled, _pilot, _health, _last_pilot
    from nina.link_daemon import robot_bridge

    with _lock:
        pilot = _pilot
        _pilot = None
        _enabled = False

    if pilot is not None:
        try:
            pilot.stop()
        except Exception:
            pass

    try:
        nav = robot_bridge.navigation_for_autonomy()
        nav.emergency_stop()
    except Exception:
        pass

    for closer, label in (
        (_ultras.close, "ultrasonic"),
        (_ir.close, "ir"),
    ):
        try:
            closer()
        except Exception as exc:
            log.warning("close %s: %s", label, exc)

    depth_bridge.release("autonomy")
    robot_bridge.set_autonomy_blocks_drive(False)

    slam_st = slam_bridge.get_bridge()
    if slam_st is not None:
        st = slam_st.status()
        lidar_ok = bool(st.get("lidar_connected"))
        lidar_msg = str(st.get("lidar_message", ""))
    else:
        lidar_ok, lidar_msg = False, "stopped"

    with _lock:
        _health = SensorHealth(
            lidar=(lidar_ok, lidar_msg),
            ultrasonic=[],
            ir=(False, "stopped"),
            depth=(False, "stopped"),
        )
        _last_pilot = None


def _enable_internal(settings: AutonomySettings) -> Tuple[bool, str]:
    global _enabled, _pilot, _health
    from nina.link_daemon import robot_bridge

    slam_bridge.ensure_bridge_started()
    bridge = slam_bridge.get_bridge()
    if bridge is None:
        return False, "slam bridge unavailable"

    ultra_ok, ultra_msg = _safe_open(_ultras.open, "ultrasonic")
    ir_ok, ir_msg = _safe_open(_ir.open, "ir")
    depth_ok, depth_msg = depth_bridge.acquire("autonomy")

    slam_status = bridge.status()
    with _lock:
        _health = SensorHealth(
            lidar=(
                bool(slam_status.get("lidar_connected")),
                str(slam_status.get("lidar_message", "")),
            ),
            ultrasonic=_ultras.status() if ultra_ok else [
                ("front_left", False, ultra_msg),
                ("front_right", False, ultra_msg),
                ("rear_left", False, ultra_msg),
                ("rear_right", False, ultra_msg),
            ],
            ir=(ir_ok, ir_msg),
            depth=(depth_ok, depth_msg),
        )

    drive = _NavDriveAdapter(robot_bridge.navigation_for_autonomy())

    bundle = SensorBundle(
        lidar=lambda: bridge.latest_scan(),
        ultrasonics=lambda: _safe_read_ultras(ultra_ok),
        ir=lambda: _safe_read_ir(ir_ok),
        depth=lambda: _safe_read_depth(depth_ok),
    )

    pilot = AutonomousPilot(drive, bundle, settings)
    pilot.add_listener(_on_pilot_state)

    robot_bridge.set_autonomy_blocks_drive(True)
    pilot.start()

    with _lock:
        _pilot = pilot
        _enabled = True

    return True, "autonomy started"


def status_dict() -> Dict[str, Any]:
    with _lock:
        pl = _pilot.state() if _pilot else None
        return {
            "enabled": _enabled,
            "health": _health.as_dict(),
            "pilot": _pilot_state_dict(pl) if pl else None,
            "last_pilot": _last_pilot,
        }


def set_enabled(want: bool) -> Dict[str, Any]:
    """Turn autonomous wander on or off."""
    with _lock:
        cur = _enabled
    if want == cur:
        return {"ok": True, "enabled": want, "unchanged": True}

    if not want:
        _disable_internal()
        return {"ok": True, "enabled": False}

    from nina.config.settings import load_settings

    settings = load_settings(_repo_root())
    ok, msg = _enable_internal(settings.autonomy)
    if not ok:
        _disable_internal()
        return {"ok": False, "enabled": False, "error": msg}

    return {"ok": True, "enabled": True, "message": msg}


def shutdown() -> None:
    try:
        set_enabled(False)
    except Exception:
        pass
