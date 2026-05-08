"""Qt facade over `AutonomousPilot` (wander) and `GotoPilot` (goto).

Owns the short-range sensors (HC-SR04 ring, GP2Y0E02B IR, RealSense
D435 depth) and exactly one pilot at a time. Lidar scans come from
the running `SlamWorker` so we don't open the serial port twice.

Public surface:

    enabled_changed(bool)
    pilot_state_changed(dict)   # PilotState as a dict (wander mode)
    goto_state_changed(dict)    # GotoState as a dict (goto mode)
    sensor_health_changed(dict) # SensorHealth dict for the Map screen pills

    set_enabled(on: bool)
    is_enabled() -> bool
    state() -> dict
    set_goal(x_mm, y_mm)        # arms goto mode (turns autonomy on if off)
    clear_goal()                # back to wander, or off if started by goto
    current_mode() -> 'wander' | 'goto' | 'idle'

`set_enabled(True)` opens whichever sensors are available, starts the
wander pilot by default, and disables any subsystems that fail to come
up (the pilot keeps running on whatever sensors did open).
`set_enabled(False)` stops the active pilot, closes the sensors, and
parks the wheels.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import QObject, pyqtSignal

from nina.config.settings import AutonomySettings, GotoSettings
from nina.navigation.autonomous_pilot import (
    AutonomousPilot,
    PilotState,
    SensorBundle,
)
from nina.navigation.goto_pilot import (
    GotoPilot,
    GotoPose,
    GotoSensorBundle,
    GotoSnapshot,
    GotoState,
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


# Mode constants (string values so they round-trip cleanly through
# Qt signals + the link-daemon HTTP surface without enum gymnastics).
MODE_IDLE = "idle"
MODE_WANDER = "wander"
MODE_GOTO = "goto"


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
    goto_state_changed = pyqtSignal(dict)
    sensor_health_changed = pyqtSignal(dict)
    mode_changed = pyqtSignal(str)        # 'idle' | 'wander' | 'goto'
    # Depth lifecycle. RealSense pipeline.start() blocks 1-3 s on
    # the calling thread; on Jetson over USB-3 it can occasionally
    # hang for tens of seconds if librealsense is mid-recovery from
    # a previous unclean shutdown. We open it on a worker thread
    # and emit this signal when the open completes (success or
    # failure) so screens can keep their UI thread responsive while
    # the pipeline initialises.
    #
    # Payload: {"ok": bool, "message": str, "in_progress": bool}
    depth_open_changed = pyqtSignal(dict)

    def __init__(
        self,
        drive: DriveController,
        slam: SlamWorker,
        settings: AutonomySettings,
        goto_settings: Optional[GotoSettings] = None,
        ultrasonics: Optional[HCSR04Array] = None,
        ir: Optional[GP2Y0E02B] = None,
        depth: Optional[RealSenseD435] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._drive = drive
        self._slam = slam
        self._settings = settings
        # Goto settings are optional so older tests / CLI tools that
        # build an AutonomyController without a NinaSettings don't
        # have to know the goto knobs exist. When omitted we fall
        # back to whatever load_settings() picks up from env vars.
        if goto_settings is None:
            from nina.config.settings import load_settings
            from pathlib import Path
            try:
                goto_settings = load_settings(
                    Path(__file__).resolve().parents[2]
                ).goto
            except Exception:
                # Last-resort defaults so a controller can still
                # construct in a hostile environment (no repo root,
                # no env vars). Tests stub this out anyway.
                goto_settings = GotoSettings(
                    arrival_radius_mm=250,
                    footprint_radius_mm=250,
                    min_passage_width_mm=610,
                    cruise_speed_pct=8,
                    turn_speed_pct=9,
                    heading_deadband_deg=18.0,
                    lookahead_mm=600,
                    replan_period_sec=3.0,
                    stuck_window_sec=5.0,
                    stuck_motion_mm=50,
                    tick_hz=8.0,
                    unknown_pixel_cost=1.5,
                    forward_clear_mm=700,
                    emergency_stop_mm=580,
                )
        self._goto_settings = goto_settings
        self._ultras = ultrasonics or HCSR04Array()
        self._ir = ir or GP2Y0E02B()
        self._depth = depth or RealSenseD435()

        self._lock = threading.RLock()
        self._enabled = False
        self._mode: str = MODE_IDLE
        self._pilot: Optional[AutonomousPilot] = None
        self._goto_pilot: Optional[GotoPilot] = None
        self._goto_started_autonomy = False  # if True, clear_goal also disables autonomy
        self._sensor_bundle: Optional[SensorBundle] = None
        self._health = SensorHealth()
        self._opened: List[Callable[[], None]] = []
        self._last_goto_state: Optional[dict] = None

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
        self._depth_open_in_progress = False
        self._depth_open_thread: Optional[threading.Thread] = None
        # Set true while a release_depth() is waiting for an in-flight
        # open. The open thread checks this on completion and closes
        # the pipeline immediately if the caller has already released
        # their reference.
        self._depth_close_pending = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self._enabled

    def current_mode(self) -> str:
        with self._lock:
            return self._mode

    def state(self) -> dict:
        with self._lock:
            pilot = self._pilot
            goto = self._goto_pilot
            mode = self._mode
            last_goto = (
                dict(self._last_goto_state)
                if self._last_goto_state else None
            )
            payload = {
                "enabled": self._enabled,
                "mode": mode,
                "health": self._health.as_dict(),
                "pilot": _pilot_state_to_dict(pilot.state()) if pilot else None,
                "goto": (
                    goto.state().as_dict() if goto else last_goto
                ),
                "sim": self._is_simulation_summary(),
            }
        return payload

    # ------------------------------------------------------------------
    # Depth lifecycle (refcounted) + visualization passthrough
    # ------------------------------------------------------------------

    def acquire_depth(self) -> Tuple[bool, str]:
        """Open the D435 if it isn't already, increment the refcount.

        **Non-blocking**: returns immediately. If this is the first
        reference, a worker thread is spawned to call
        ``RealSense.open()`` (which blocks 1-3 s on
        ``pipeline.start()`` and can occasionally stall for tens of
        seconds during USB recovery). The actual outcome arrives via
        the ``depth_open_changed`` Qt signal once the open completes.

        Return contract:

          * ``(True,  "depth ready")``         - already open from a
                                                  previous acquire
                                                  (refcount > 1).
          * ``(True,  "depth opening...")``    - first acquire; open
                                                  is now running on
                                                  a worker thread.
                                                  Wait for
                                                  ``depth_open_changed``
                                                  for the real result.
          * ``(False, "depth: <error>")``      - a previous open
                                                  failed and we're
                                                  not retrying. Call
                                                  ``release_depth()``
                                                  + ``acquire_depth()``
                                                  to retry.
        """
        with self._lock:
            self._depth_refcount += 1
            if self._depth_refcount > 1:
                # Refcount > 1 -> someone else already opened (or is
                # opening) the camera. Just bump the count.
                return self._depth_open_ok, self._depth_open_msg

            # First reference. If a previous open already failed and
            # left _depth_open_ok = False (and refcount went to 0),
            # we'd have returned to here with refcount = 1 from the
            # increment above. Be explicit about retrying: clear the
            # error flag so we can attempt a fresh open.
            self._depth_open_ok = False
            self._depth_open_msg = "depth opening..."
            self._depth_open_in_progress = True
            self._depth_close_pending = False
            self._depth_open_thread = threading.Thread(
                target=self._depth_open_worker,
                name="DepthOpen",
                daemon=True,
            )
            self._depth_open_thread.start()
            return True, self._depth_open_msg

    def _depth_open_worker(self) -> None:
        """Background-thread body for ``acquire_depth()``.

        Holds NO locks across the actual ``_depth.open()`` call
        (which is the blocking part). Locks are taken only to
        publish state + decide whether to close immediately.
        """
        try:
            self._depth.open()
            ok = True
            msg = "depth ready"
        except Exception as exc:
            log.warning("depth open failed: %s", exc)
            ok = False
            msg = f"depth: {exc}"

        close_now = False
        with self._lock:
            self._depth_open_ok = ok
            self._depth_open_msg = msg
            self._depth_open_in_progress = False
            self._depth_open_thread = None
            if not ok:
                # Failed open: drop the speculative refcount so the
                # next acquire_depth() will retry instead of just
                # bumping into the already-False cached state.
                self._depth_refcount = 0
                self._depth_close_pending = False
            elif self._depth_close_pending or self._depth_refcount <= 0:
                # Open succeeded but everyone released while we were
                # mid-open. Tear it back down.
                close_now = True
                self._depth_refcount = 0
                self._depth_open_ok = False
                self._depth_open_msg = "depth closed"
                self._depth_close_pending = False
        if close_now:
            try:
                self._depth.set_color_publish(False)
            except Exception:
                pass
            try:
                self._depth.close()
            except Exception as exc:
                log.warning("depth close after racy release failed: %s", exc)

        # Refresh sensor health if autonomy is currently enabled -
        # otherwise the Map screen's "Depth: opening..." pill stays
        # stale until the next enable cycle.
        with self._lock:
            if self._enabled:
                final_ok = ok and not close_now
                final_msg = msg if not close_now else "depth closed"
                self._health = SensorHealth(
                    lidar=self._health.lidar,
                    ultrasonic=self._health.ultrasonic,
                    ir=self._health.ir,
                    depth=(final_ok, final_msg),
                )
                health_payload = self._health.as_dict()
            else:
                health_payload = None

        # Always notify, even on close-now, so any listener that
        # cares ("we tried and failed" or "we transiently opened")
        # can update its UI.
        self.depth_open_changed.emit({
            "ok": ok and not close_now,
            "message": msg if not close_now else "depth closed",
            "in_progress": False,
        })
        if health_payload is not None:
            self.sensor_health_changed.emit(health_payload)

    def release_depth(self) -> None:
        """Drop one reference to the depth sensor, close on the last.

        Safe to call when refcount is already zero (no-op) so a
        Perception screen on_leave handler doesn't have to track its
        own state. If an open is currently in flight, sets a flag so
        the open-thread closes the pipeline immediately on completion
        instead of leaving it spinning with no consumers.
        """
        with self._lock:
            if self._depth_refcount <= 0:
                return
            self._depth_refcount -= 1
            if self._depth_refcount > 0:
                return
            # Last reference - actually close. If an open is in
            # flight, defer the close to the open-worker (it'll see
            # _depth_close_pending and tear down on completion).
            if self._depth_open_in_progress:
                self._depth_close_pending = True
                return
            # Disable colorization first so the worker thread doesn't
            # try to apply cv2 to a frame that's about to be
            # invalidated by close().
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

    def set_goal(self, x_mm: float, y_mm: float) -> dict:
        """Arm the goto pilot to drive to (x_mm, y_mm).

        Behaviour:
          * Autonomy off  -> opens the sensor stack, switches to
                             goto mode, starts the goto pilot.
                             ``clear_goal`` will then also turn
                             autonomy off (mirrors the operator's
                             original "tap to start" intent).
          * Autonomy on, mode=wander
                          -> stops wander, switches to goto, starts
                             the goto pilot. ``clear_goal`` returns
                             to wander.
          * Autonomy on, mode=goto
                          -> updates the in-flight goal. The pilot
                             discards its old waypoints and replans.

        Returns a dict with ``ok`` + ``mode`` + ``message`` so the
        caller (Map screen / link daemon / Android) can surface a
        precise reason for any refusal.
        """
        try:
            x = float(x_mm)
            y = float(y_mm)
        except (TypeError, ValueError):
            return {"ok": False, "message": "goal coords must be numeric"}

        with self._lock:
            was_enabled = self._enabled
            already_goto = self._goto_pilot is not None
            mode = self._mode

        if not was_enabled:
            # We started this autonomy session because of the goto
            # request, so clear_goal should also unwind it.
            try:
                self._enable(initial_mode=MODE_GOTO)
            except Exception as exc:
                log.exception("set_goal: enable failed")
                return {"ok": False, "message": f"enable failed: {exc}"}
            with self._lock:
                self._goto_started_autonomy = True
            self._spawn_goto_pilot(x, y)
            return {"ok": True, "mode": MODE_GOTO, "message": "goto armed"}

        # Already on. If currently in wander, swap pilots.
        if mode == MODE_WANDER and not already_goto:
            self._stop_wander_pilot()
            with self._lock:
                self._mode = MODE_GOTO
                self._goto_started_autonomy = False
            self.mode_changed.emit(MODE_GOTO)
            self._spawn_goto_pilot(x, y)
            return {"ok": True, "mode": MODE_GOTO, "message": "switched to goto"}

        # Already in goto: update in flight (the pilot handles this
        # internally via start() being idempotent).
        if already_goto:
            self._goto_pilot.start(x, y)  # type: ignore[union-attr]
            return {"ok": True, "mode": MODE_GOTO, "message": "goal updated"}

        # Defensive: enabled but no pilot at all (mid-handoff race).
        with self._lock:
            self._mode = MODE_GOTO
        self.mode_changed.emit(MODE_GOTO)
        self._spawn_goto_pilot(x, y)
        return {"ok": True, "mode": MODE_GOTO, "message": "goto armed"}

    def clear_goal(self) -> dict:
        """Cancel an in-flight goto.

        If goto was the reason autonomy turned on (``set_goal`` while
        autonomy was off), this also disables autonomy. Otherwise we
        revert to the wander pilot.
        """
        with self._lock:
            goto = self._goto_pilot
            started_us = self._goto_started_autonomy
            mode = self._mode

        if goto is None and mode != MODE_GOTO:
            return {
                "ok": True,
                "mode": mode,
                "message": "no active goto",
            }

        self._stop_goto_pilot()

        if started_us:
            self._disable()
            return {
                "ok": True,
                "mode": MODE_IDLE,
                "message": "goto cleared, autonomy off",
            }

        # Fall back to wander.
        self._spawn_wander_pilot()
        with self._lock:
            self._mode = MODE_WANDER
        self.mode_changed.emit(MODE_WANDER)
        return {
            "ok": True,
            "mode": MODE_WANDER,
            "message": "goto cleared, returned to wander",
        }

    def shutdown(self) -> None:
        try:
            self.set_enabled(False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal lifecycle
    # ------------------------------------------------------------------

    def _enable(self, initial_mode: str = MODE_WANDER) -> None:
        log.info("Autonomy enabling - opening sensors (mode=%s)", initial_mode)
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

        # Bundles get cached so set_goal mid-flight can build a goto
        # pilot from the same already-open sensor stack without
        # reaching back into the open flags.
        wander_bundle = SensorBundle(
            lidar=lambda: self._slam.latest_scan(),
            ultrasonics=lambda: self._safe_read_ultras(ultra_ok),
            ir=lambda: self._safe_read_ir(ir_ok),
            depth=lambda: self._safe_read_depth(depth_ok),
        )
        with self._lock:
            self._sensor_bundle = wander_bundle

        # Make sure the brake is released before either pilot starts
        # issuing wheel commands.
        try:
            self._drive.set_brake(False)
            self._drive.ensure_hardware()
        except Exception as exc:
            log.warning("drive.ensure_hardware: %s", exc)

        with self._lock:
            self._enabled = True
            self._mode = initial_mode

        if initial_mode == MODE_WANDER:
            self._spawn_wander_pilot()

        self.sensor_health_changed.emit(self._health.as_dict())
        self.mode_changed.emit(initial_mode)
        self.enabled_changed.emit(True)

    def _disable(self) -> None:
        log.info("Autonomy disabling - stopping pilot + sensors")
        with self._lock:
            wander = self._pilot
            goto = self._goto_pilot
            self._pilot = None
            self._goto_pilot = None
            self._enabled = False
            self._mode = MODE_IDLE
            self._goto_started_autonomy = False
            self._sensor_bundle = None

        if wander is not None:
            try:
                wander.stop()
            except Exception:
                pass
        if goto is not None:
            try:
                goto.stop()
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
        self.mode_changed.emit(MODE_IDLE)
        self.enabled_changed.emit(False)

    # ------------------------------------------------------------------
    # Pilot lifecycle (wander + goto helpers)
    # ------------------------------------------------------------------

    def _spawn_wander_pilot(self) -> None:
        with self._lock:
            bundle = self._sensor_bundle
        if bundle is None:
            log.warning("_spawn_wander_pilot: no sensor bundle yet")
            return
        pilot = AutonomousPilot(self._drive, bundle, self._settings)
        pilot.add_listener(self._on_pilot_state)
        pilot.start()
        with self._lock:
            self._pilot = pilot

    def _stop_wander_pilot(self) -> None:
        with self._lock:
            pilot = self._pilot
            self._pilot = None
        if pilot is not None:
            try:
                pilot.stop()
            except Exception:
                log.exception("wander pilot.stop")
        try:
            self._drive.stop()
        except Exception:
            pass

    def _spawn_goto_pilot(self, goal_x_mm: float, goal_y_mm: float) -> None:
        with self._lock:
            wander_bundle = self._sensor_bundle
        if wander_bundle is None:
            log.warning("_spawn_goto_pilot: no sensor bundle yet")
            return

        # Goto pilot needs SLAM pose + grid AND the same reactive
        # sensor stream the wander pilot uses. We build a thin
        # GotoSensorBundle that wraps both.
        def _pose_getter() -> Optional[GotoPose]:
            p = self._slam.latest_pose()
            if p is None:
                return None
            return GotoPose(
                x_mm=p["x_mm"], y_mm=p["y_mm"],
                theta_deg=p["theta_deg"],
                updated_at=p.get("updated_at", 0.0),
            )

        def _snap_getter() -> Optional[GotoSnapshot]:
            v = self._slam.latest_grid_view()
            if v is None:
                return None
            return GotoSnapshot(
                grid_bytes=v["grid_bytes"],
                width=v["width"], height=v["height"],
                scale_mm_per_px=v["scale_mm_per_px"],
            )

        bundle = GotoSensorBundle(
            pose=_pose_getter,
            snapshot=_snap_getter,
            lidar=wander_bundle.lidar,
            ultrasonics=wander_bundle.ultrasonics,
            ir=wander_bundle.ir,
            depth=wander_bundle.depth,
        )

        pilot = GotoPilot(
            self._drive, bundle, self._goto_settings, self._settings
        )
        pilot.add_listener(self._on_goto_state)
        pilot.start(goal_x_mm, goal_y_mm)
        with self._lock:
            self._goto_pilot = pilot

    def _stop_goto_pilot(self) -> None:
        with self._lock:
            pilot = self._goto_pilot
            self._goto_pilot = None
        if pilot is not None:
            try:
                pilot.stop()
            except Exception:
                log.exception("goto pilot.stop")
        try:
            self._drive.stop()
        except Exception:
            pass

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

    def try_read_depth_for_avoidance(self) -> Optional[DepthFrame]:
        """Latest depth frame if the RealSense pipeline is open, else None."""
        with self._lock:
            ok = bool(self._depth_open_ok)
        return self._safe_read_depth(ok)

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

    def _on_goto_state(self, state: GotoState) -> None:
        payload = state.as_dict()
        with self._lock:
            self._last_goto_state = payload
            terminal = state.state in (
                "arrived", "unreachable", "stuck", "lost",
                "cancelled", "error",
            )
            started_us = self._goto_started_autonomy
        try:
            self.goto_state_changed.emit(payload)
        except Exception:
            pass

        if terminal and not state.running:
            # Stop-and-stay semantics (operator confirmed): on
            # arrival (or any other terminal), we DON'T auto-resume
            # wander. We do clean up so the controller is ready for
            # another set_goal / set_enabled cycle.
            self._stop_goto_pilot()
            if started_us:
                # The goto turned autonomy on; wind it back down.
                self._disable()
            else:
                # Was wander before goto; stay enabled but idle the
                # mode so the operator decides "back to wander or
                # off". We mirror this in the UI by reopening the
                # wander pilot only if the operator explicitly
                # toggles autonomy (they can also re-tap to set a
                # new goal from here).
                with self._lock:
                    self._mode = MODE_WANDER
                self.mode_changed.emit(MODE_WANDER)
                self._spawn_wander_pilot()


def lidar_scan_signature(scan: Optional[LidarScan]) -> str:
    if scan is None:
        return "no scan"
    return f"{scan.num_points()} returns @ {scan.rpm:.1f} rpm"
