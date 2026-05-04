"""Headless autonomy stack for nina-link.

Mirrors ``sirena_ui.workers.AutonomyController`` so the Android
companion gets the same wander + goto behaviour over HTTP. Two pilots
are supported:

  * **wander**  - reactive obstacle-avoidance; the legacy `enabled`
                  toggle starts this one.
  * **goto**    - click-on-map navigation; armed by ``set_goal`` and
                  cancelled by ``clear_goal``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from nina.config.settings import AutonomySettings, GotoSettings
from nina.navigation.autonomous_pilot import AutonomousPilot, PilotState, SensorBundle
from nina.navigation.goto_pilot import (
    GotoPilot,
    GotoPose,
    GotoSensorBundle,
    GotoSnapshot,
    GotoState,
)
from nina.sensors.gp2y0e02b import GP2Y0E02B
from nina.sensors.hcsr04 import HCSR04Array
from nina.sensors.types import DepthFrame, IRReading, LidarScan, SensorHealth, UltrasonicReading

from nina.link_daemon import depth_bridge
from nina.link_daemon import slam_bridge

log = logging.getLogger("nina.link_daemon.autonomy_bridge")


MODE_IDLE = "idle"
MODE_WANDER = "wander"
MODE_GOTO = "goto"


_lock = threading.RLock()
_enabled = False
_mode: str = MODE_IDLE
_pilot: Optional[AutonomousPilot] = None
_goto_pilot: Optional[GotoPilot] = None
_goto_started_us = False
_health = SensorHealth()
_ultras = HCSR04Array()
_ir = GP2Y0E02B()
_last_pilot: Optional[Dict[str, Any]] = None
_last_goto: Optional[Dict[str, Any]] = None
_sensor_bundle: Optional[SensorBundle] = None
_sensor_open_flags: Dict[str, bool] = {"ultra": False, "ir": False, "depth": False}


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


def _on_goto_state(state: GotoState) -> None:
    global _last_goto, _mode, _enabled, _goto_pilot, _goto_started_us
    payload = state.as_dict()
    terminal = state.state in (
        "arrived", "unreachable", "stuck", "lost", "cancelled", "error",
    )
    with _lock:
        _last_goto = payload
        started_us = _goto_started_us

    if terminal and not state.running:
        # Stop-and-stay: clean up the goto pilot. If goto was the
        # reason autonomy turned on, also turn autonomy off; else
        # fall back to wander so the bot keeps avoiding obstacles
        # passively.
        with _lock:
            pilot = _goto_pilot
            _goto_pilot = None
        if pilot is not None:
            try:
                pilot.stop()
            except Exception:
                log.exception("goto pilot.stop")
        if started_us:
            _disable_internal()
        else:
            with _lock:
                _mode = MODE_WANDER
            _spawn_wander_pilot_internal()


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
    global _enabled, _pilot, _goto_pilot, _goto_started_us, _mode
    global _health, _last_pilot, _last_goto, _sensor_bundle
    from nina.link_daemon import robot_bridge

    with _lock:
        pilot = _pilot
        goto = _goto_pilot
        _pilot = None
        _goto_pilot = None
        _enabled = False
        _mode = MODE_IDLE
        _goto_started_us = False
        _sensor_bundle = None

    if pilot is not None:
        try:
            pilot.stop()
        except Exception:
            pass
    if goto is not None:
        try:
            goto.stop()
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
        _last_goto = None
        _sensor_open_flags["ultra"] = False
        _sensor_open_flags["ir"] = False
        _sensor_open_flags["depth"] = False


def _enable_internal(
    settings: AutonomySettings, *, initial_mode: str = MODE_WANDER,
) -> Tuple[bool, str]:
    global _enabled, _pilot, _health, _mode, _sensor_bundle
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
        _sensor_open_flags["ultra"] = ultra_ok
        _sensor_open_flags["ir"] = ir_ok
        _sensor_open_flags["depth"] = depth_ok

    bundle = SensorBundle(
        lidar=lambda: bridge.latest_scan(),
        ultrasonics=lambda: _safe_read_ultras(ultra_ok),
        ir=lambda: _safe_read_ir(ir_ok),
        depth=lambda: _safe_read_depth(depth_ok),
    )
    with _lock:
        _sensor_bundle = bundle
        _enabled = True
        _mode = initial_mode

    robot_bridge.set_autonomy_blocks_drive(True)

    if initial_mode == MODE_WANDER:
        _spawn_wander_pilot_internal()

    return True, "autonomy started"


def _spawn_wander_pilot_internal() -> None:
    """(Re)start the wander pilot using the cached sensor bundle."""
    global _pilot
    from nina.link_daemon import robot_bridge

    with _lock:
        bundle = _sensor_bundle
        if bundle is None:
            log.warning("_spawn_wander_pilot_internal: no bundle yet")
            return
    settings_obj = _load_settings_blob()
    pilot = AutonomousPilot(
        _NavDriveAdapter(robot_bridge.navigation_for_autonomy()),
        bundle, settings_obj.autonomy,
    )
    pilot.add_listener(_on_pilot_state)
    pilot.start()
    with _lock:
        _pilot = pilot


def _load_settings_blob():
    from nina.config.settings import load_settings
    return load_settings(_repo_root())


def _spawn_goto_pilot_internal(goal_x_mm: float, goal_y_mm: float) -> None:
    global _goto_pilot
    from nina.link_daemon import robot_bridge

    with _lock:
        wander_bundle = _sensor_bundle
        if wander_bundle is None:
            log.warning("_spawn_goto_pilot_internal: no bundle yet")
            return

    bridge = slam_bridge.get_bridge()

    def _pose_getter() -> Optional[GotoPose]:
        if bridge is None:
            return None
        snap = bridge.latest_snapshot()
        if snap is None:
            return None
        return GotoPose(
            x_mm=snap.pose.x_mm, y_mm=snap.pose.y_mm,
            theta_deg=snap.pose.theta_deg,
            updated_at=snap.updated_at,
        )

    def _snap_getter() -> Optional[GotoSnapshot]:
        if bridge is None:
            return None
        snap = bridge.latest_snapshot()
        if snap is None:
            return None
        return GotoSnapshot(
            grid_bytes=snap.grid_bytes,
            width=snap.width, height=snap.height,
            scale_mm_per_px=snap.scale_mm_per_px,
        )

    bundle = GotoSensorBundle(
        pose=_pose_getter,
        snapshot=_snap_getter,
        lidar=wander_bundle.lidar,
        ultrasonics=wander_bundle.ultrasonics,
        ir=wander_bundle.ir,
        depth=wander_bundle.depth,
    )
    settings_obj = _load_settings_blob()
    pilot = GotoPilot(
        _NavDriveAdapter(robot_bridge.navigation_for_autonomy()),
        bundle, settings_obj.goto, settings_obj.autonomy,
    )
    pilot.add_listener(_on_goto_state)
    pilot.start(goal_x_mm, goal_y_mm)
    with _lock:
        _goto_pilot = pilot


def status_dict() -> Dict[str, Any]:
    with _lock:
        pl = _pilot.state() if _pilot else None
        gp = _goto_pilot.state() if _goto_pilot else None
        return {
            "enabled": _enabled,
            "mode": _mode,
            "health": _health.as_dict(),
            "pilot": _pilot_state_dict(pl) if pl else None,
            "last_pilot": _last_pilot,
            "goto": gp.as_dict() if gp else _last_goto,
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


def set_goal(x_mm: float, y_mm: float) -> Dict[str, Any]:
    """Arm the goto pilot. Same semantics as
    ``AutonomyController.set_goal`` in the Qt facade.
    """
    global _goto_started_us, _mode
    try:
        x = float(x_mm)
        y = float(y_mm)
    except (TypeError, ValueError):
        return {"ok": False, "message": "goal coords must be numeric"}

    with _lock:
        was_enabled = _enabled
        already_goto = _goto_pilot is not None
        cur_mode = _mode

    if not was_enabled:
        from nina.config.settings import load_settings
        settings = load_settings(_repo_root())
        ok, msg = _enable_internal(settings.autonomy, initial_mode=MODE_GOTO)
        if not ok:
            _disable_internal()
            return {"ok": False, "enabled": False, "message": msg}
        with _lock:
            _goto_started_us = True
        _spawn_goto_pilot_internal(x, y)
        return {
            "ok": True, "enabled": True,
            "mode": MODE_GOTO, "message": "goto armed",
        }

    if cur_mode == MODE_WANDER and not already_goto:
        global _pilot
        with _lock:
            pilot = _pilot
            _pilot = None
        if pilot is not None:
            try:
                pilot.stop()
            except Exception:
                log.exception("wander stop on goto switch")
        with _lock:
            _mode = MODE_GOTO
            _goto_started_us = False
        _spawn_goto_pilot_internal(x, y)
        return {
            "ok": True, "enabled": True,
            "mode": MODE_GOTO, "message": "switched to goto",
        }

    if already_goto:
        _goto_pilot.start(x, y)  # type: ignore[union-attr]
        return {
            "ok": True, "enabled": True,
            "mode": MODE_GOTO, "message": "goal updated",
        }

    with _lock:
        _mode = MODE_GOTO
    _spawn_goto_pilot_internal(x, y)
    return {
        "ok": True, "enabled": True,
        "mode": MODE_GOTO, "message": "goto armed",
    }


def clear_goal() -> Dict[str, Any]:
    """Cancel an in-flight goto."""
    global _goto_pilot, _mode

    with _lock:
        goto = _goto_pilot
        started_us = _goto_started_us
        cur_mode = _mode
        _goto_pilot = None

    if goto is None and cur_mode != MODE_GOTO:
        return {"ok": True, "mode": cur_mode, "message": "no active goto"}

    if goto is not None:
        try:
            goto.stop()
        except Exception:
            log.exception("goto pilot.stop")

    if started_us:
        _disable_internal()
        return {
            "ok": True, "enabled": False,
            "mode": MODE_IDLE,
            "message": "goto cleared, autonomy off",
        }

    _spawn_wander_pilot_internal()
    with _lock:
        _mode = MODE_WANDER
    return {
        "ok": True, "enabled": True,
        "mode": MODE_WANDER,
        "message": "goto cleared, returned to wander",
    }


def shutdown() -> None:
    try:
        set_enabled(False)
    except Exception:
        pass
