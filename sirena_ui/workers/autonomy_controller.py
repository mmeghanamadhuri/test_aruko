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
from typing import Callable, List, Optional, Tuple

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

        # Reference count for the depth sensor so the Perception screen
        # can keep it open for visualization while autonomy is OFF, and
        # so toggling autonomy off doesn't yank the depth feed out from
        # under a Perception screen the operator is still looking at.
        # The realsense pipeline doesn't tolerate two callers each
        # doing pipeline.start() on the same device, so the lifecycle
        # has to live in exactly one place - here.
        self._depth_refcount = 0
        self._depth_open_ok = False
        self._depth_open_msg = "not opened"

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

    # ------------------------------------------------------------------
    # Depth lifecycle (refcounted) + visualization passthrough
    # ------------------------------------------------------------------

    def acquire_depth(self) -> Tuple[bool, str]:
        """Open the D435 if it isn't already, increment the refcount.

        Returns ``(opened_ok, message)`` so the caller can surface the
        error in its own UI without having to ``read()`` and infer.
        Idempotent + thread-safe: a second call from the autonomy
        enable path while the Perception screen already opened the
        camera is a refcount bump, NOT a redundant
        ``pipeline.start()`` (which librealsense rejects).
        """
        with self._lock:
            self._depth_refcount += 1
            if self._depth_refcount > 1:
                return self._depth_open_ok, self._depth_open_msg
            try:
                self._depth.open()
                self._depth_open_ok = True
                self._depth_open_msg = "depth ready"
            except Exception as exc:
                log.warning("depth open failed: %s", exc)
                self._depth_open_ok = False
                self._depth_open_msg = f"depth: {exc}"
                # Don't keep a dangling refcount on a failed open or
                # the next release_depth would underflow our "close
                # only on the last release" check.
                self._depth_refcount = 0
            return self._depth_open_ok, self._depth_open_msg

    def release_depth(self) -> None:
        """Drop one reference to the depth sensor, close on the last.

        Safe to call when refcount is already zero (no-op) so a
        Perception screen on_leave handler doesn't have to track its
        own state.
        """
        with self._lock:
            if self._depth_refcount <= 0:
                return
            self._depth_refcount -= 1
            if self._depth_refcount > 0:
                return
            # Last reference - actually close. Disable colorization
            # first so the worker thread doesn't try to apply cv2 to a
            # frame that's about to be invalidated by close().
            try:
                self._depth.set_color_publish(False)
            except Exception:
                pass
            try:
                self._depth.close()
            except Exception as exc:
                log.warning("depth close failed: %s", exc)
            self._depth_open_ok = False
            self._depth_open_msg = "depth closed"

    def set_depth_visualization_enabled(self, enabled: bool) -> None:
        """Tell the depth driver to (stop) publishing colorized frames.

        The Perception screen flips this on while it's the visible
        screen and off when the operator navigates away. Off by
        default so the autonomy hot path never pays the colorize cost
        unless someone is actually watching. Safe to call even when
        the depth driver is closed - the underlying setter just
        toggles a flag the worker thread reads next time it gets a
        frame.
        """
        try:
            self._depth.set_color_publish(bool(enabled))
        except Exception as exc:
            log.debug(
                "set_depth_visualization_enabled(%s) failed: %s",
                enabled, exc,
            )

    def latest_depth_visualization(self):
        """Return ``(width, height, bgr_bytes, DepthFrame|None)`` for
        the most recent colorized depth frame, or ``None`` if depth
        visualization isn't producing yet.

        The DepthFrame is bundled so the Perception screen can render
        the same forward / left / right minima the autonomy stack is
        actually consuming, not a separate read that could disagree
        with the colorized image by a frame or two.
        """
        try:
            color = self._depth.latest_color_image()
        except Exception:
            return None
        if color is None:
            return None
        w, h, buf = color
        try:
            frame = self._depth.read()
        except Exception:
            frame = None
        return (w, h, buf, frame)

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
        # Depth goes through the refcount so a Perception screen that
        # already opened the camera for visualization doesn't get
        # double-opened (librealsense rejects that).
        depth_ok, depth_msg = self.acquire_depth()

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
        ):
            try:
                closer()
            except Exception as exc:
                log.warning("close %s: %s", label, exc)
        # Depth closes through the refcount so a still-open Perception
        # screen keeps its visualization alive after autonomy disables.
        self.release_depth()

        # Refresh health snapshot now that everything is closed.
        # Depth state mirrors the refcount - if a Perception screen
        # still holds a reference, the camera is in fact still open
        # and reporting "stopped" would be a lie that confuses the
        # Map screen pills.
        with self._lock:
            depth_state: Tuple[bool, str]
            if self._depth_refcount > 0 and self._depth_open_ok:
                depth_state = (True, "depth (visualization only)")
            else:
                depth_state = (False, "stopped")
            self._health = SensorHealth(
                lidar=(False, "stopped"),
                ultrasonic=[],
                ir=(False, "stopped"),
                depth=depth_state,
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
