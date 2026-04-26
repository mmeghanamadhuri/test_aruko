"""Qt facade over `AutonomousPilot`.

Owns the short-range sensors (HC-SR04 ring, GP2Y0E02B IR, RealSense
D435 depth) and the pilot itself. Lidar scans come from the running
`SlamWorker` so we don't open the serial port twice.

Public surface:

    enabled_changed(bool)
    pilot_state_changed(dict)   # PilotState as a dict
    sensor_health_changed(dict) # SensorHealth dict for the Map screen pills

    set_enabled(on: bool)
    is_enabled() -> bool
    state() -> dict

`set_enabled(True)` opens whichever sensors are available, starts the
pilot, and disables any subsystems that fail to come up (the pilot
keeps running on whatever sensors did open). `set_enabled(False)`
stops the pilot, closes the sensors, and parks the wheels.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from nina.config.settings import AutonomySettings
from nina.navigation.autonomous_pilot import (
    AutonomousPilot,
    PilotState,
    SensorBundle,
)
from nina.sensors.gp2y0e02b import GP2Y0E02B
from nina.sensors.hcsr04 import HCSR04Array
from nina.sensors.realsense_d435 import RealSenseD435
from nina.sensors.types import (
    DepthFrame,
    IRReading,
    LidarScan,
    SensorHealth,
    UltrasonicReading,
)
from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.slam_worker import SlamWorker


log = logging.getLogger("sirena_ui.autonomy")


def _pilot_state_to_dict(state: PilotState) -> dict:
    return {
        "running": state.running,
        "last_action": state.last_action,
        "last_reason": state.last_reason,
        "field": dict(state.field_snapshot),
        "ticks": state.ticks,
        "started_at": state.started_at,
    }


class AutonomyController(QObject):
    enabled_changed = pyqtSignal(bool)
    pilot_state_changed = pyqtSignal(dict)
    sensor_health_changed = pyqtSignal(dict)

    def __init__(
        self,
        drive: DriveController,
        slam: SlamWorker,
        settings: AutonomySettings,
        ultrasonics: Optional[HCSR04Array] = None,
        ir: Optional[GP2Y0E02B] = None,
        depth: Optional[RealSenseD435] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._drive = drive
        self._slam = slam
        self._settings = settings
        self._ultras = ultrasonics or HCSR04Array()
        self._ir = ir or GP2Y0E02B()
        self._depth = depth or RealSenseD435()

        self._lock = threading.RLock()
        self._enabled = False
        self._pilot: Optional[AutonomousPilot] = None
        self._health = SensorHealth()
        self._opened: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self._enabled

    def state(self) -> dict:
        with self._lock:
            pilot = self._pilot
            payload = {
                "enabled": self._enabled,
                "health": self._health.as_dict(),
                "pilot": _pilot_state_to_dict(pilot.state()) if pilot else None,
                "sim": self._is_simulation_summary(),
            }
        return payload

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            target = bool(on)
            current = self._enabled
        if target == current:
            return
        if target:
            self._enable()
        else:
            self._disable()

    def shutdown(self) -> None:
        try:
            self.set_enabled(False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal lifecycle
    # ------------------------------------------------------------------

    def _enable(self) -> None:
        log.info("Autonomy enabling - opening sensors")
        self._opened.clear()

        # SLAM worker (and lidar) - if not already running, kick it on.
        try:
            self._slam.start()
        except Exception as exc:
            log.warning("slam.start failed: %s", exc)

        # Short-range sensors - each open is independent so a missing
        # sensor doesn't disable the others.
        ultra_ok, ultra_msg = self._safe_open(
            self._ultras.open, "ultrasonic"
        )
        ir_ok, ir_msg = self._safe_open(self._ir.open, "ir")
        depth_ok, depth_msg = self._safe_open(self._depth.open, "depth")

        slam_status = self._slam.status()
        with self._lock:
            self._health = SensorHealth(
                lidar=(
                    bool(slam_status.get("lidar_connected")),
                    str(slam_status.get("lidar_message", "")),
                ),
                ultrasonic=self._ultras.status() if ultra_ok else [
                    ("front_left", False, ultra_msg),
                    ("front_right", False, ultra_msg),
                    ("rear_left", False, ultra_msg),
                    ("rear_right", False, ultra_msg),
                ],
                ir=(ir_ok, ir_msg),
                depth=(depth_ok, depth_msg),
            )

        bundle = SensorBundle(
            lidar=lambda: self._slam.latest_scan(),
            ultrasonics=lambda: self._safe_read_ultras(ultra_ok),
            ir=lambda: self._safe_read_ir(ir_ok),
            depth=lambda: self._safe_read_depth(depth_ok),
        )

        # Make sure the brake is released before the pilot starts
        # issuing wheel commands.
        try:
            self._drive.set_brake(False)
            self._drive.ensure_hardware()
        except Exception as exc:
            log.warning("drive.ensure_hardware: %s", exc)

        pilot = AutonomousPilot(self._drive, bundle, self._settings)
        pilot.add_listener(self._on_pilot_state)
        pilot.start()

        with self._lock:
            self._pilot = pilot
            self._enabled = True

        self.sensor_health_changed.emit(self._health.as_dict())
        self.enabled_changed.emit(True)

    def _disable(self) -> None:
        log.info("Autonomy disabling - stopping pilot + sensors")
        with self._lock:
            pilot = self._pilot
            self._pilot = None
            self._enabled = False

        if pilot is not None:
            try:
                pilot.stop()
            except Exception:
                pass

        # Park the wheels regardless of pilot.stop() outcome.
        try:
            self._drive.stop()
        except Exception:
            pass
        try:
            self._drive.set_brake(True)
        except Exception:
            pass

        for closer, label in (
            (self._ultras.close, "ultrasonic"),
            (self._ir.close, "ir"),
            (self._depth.close, "depth"),
        ):
            try:
                closer()
            except Exception as exc:
                log.warning("close %s: %s", label, exc)

        # Refresh health snapshot now that everything is closed.
        with self._lock:
            self._health = SensorHealth(
                lidar=(False, "stopped"),
                ultrasonic=[],
                ir=(False, "stopped"),
                depth=(False, "stopped"),
            )

        self.sensor_health_changed.emit(self._health.as_dict())
        self.enabled_changed.emit(False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_open(self, fn: Callable[[], None], label: str):
        try:
            fn()
            return True, f"{label} ready"
        except Exception as exc:
            log.warning("%s open failed: %s", label, exc)
            return False, f"{label}: {exc}"

    def _safe_read_ultras(self, available: bool) -> List[UltrasonicReading]:
        if not available:
            return []
        try:
            return list(self._ultras.read_all())
        except Exception:
            return []

    def _safe_read_ir(self, available: bool) -> Optional[IRReading]:
        if not available:
            return None
        try:
            return self._ir.read()
        except Exception:
            return None

    def _safe_read_depth(self, available: bool) -> Optional[DepthFrame]:
        if not available:
            return None
        try:
            return self._depth.read()
        except Exception:
            return None

    def _is_simulation_summary(self) -> str:
        bits: List[str] = []
        if not self._health.lidar[0]:
            bits.append("lidar")
        if not self._health.ir[0]:
            bits.append("ir")
        if not self._health.depth[0]:
            bits.append("depth")
        if not self._health.ultrasonic or not any(c for _, c, _ in self._health.ultrasonic):
            bits.append("ultrasonic")
        if not bits:
            return ""
        return f"simulating: {', '.join(bits)}"

    def _on_pilot_state(self, state: PilotState) -> None:
        try:
            self.pilot_state_changed.emit(_pilot_state_to_dict(state))
        except Exception:
            pass


def lidar_scan_signature(scan: Optional[LidarScan]) -> str:
    if scan is None:
        return "no scan"
    return f"{scan.num_points()} returns @ {scan.rpm:.1f} rpm"
