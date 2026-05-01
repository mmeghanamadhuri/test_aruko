"""Reactive autonomous pilot for Nina (V1: safe wander).

Behaviour each tick (default 5 Hz):

    1. Build an ObstacleField from the latest sensor readings.
    2. If any cliff / emergency condition fires:
         - command an immediate stop, hold for one tick, then back off.
    3. Else if forward is clear (>= forward_clear_mm) AND both side
       margins are OK (>= side_clear_mm) - drive straight at cruise
       speed.
    4. Else - pick the side with more clearance, commit a brief turn
       (turn_duration_ms), and re-evaluate next tick.

The pilot only ever talks to the wheels through `DriveController` so
it inherits the existing simulation fallback when no Jetson hardware
is present.

`SensorBundle` is a small dependency-injection seam: the pilot pulls
its readings through this object so the Qt facade above can wire it
to either real drivers or a recorded fixture without touching the
control logic.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from nina.config.settings import AutonomySettings
from nina.navigation.obstacle_field import (
    SECTOR_FORWARD,
    SECTOR_LEFT,
    SECTOR_RIGHT,
    ObstacleField,
    fuse,
)
from nina.sensors.types import (
    DepthFrame,
    IRReading,
    LidarScan,
    UltrasonicReading,
)


log = logging.getLogger("nina.autonomy")


_DIR_FORWARD = "forward"
_DIR_BACK = "back"


@dataclass
class SensorBundle:
    """Lightweight bundle of latest-reading getters.

    The pilot calls each getter once per tick. Any of them may return
    `None` and the pilot will keep going on whatever evidence remains.
    """
    lidar: Callable[[], Optional[LidarScan]] = lambda: None
    ultrasonics: Callable[[], List[UltrasonicReading]] = lambda: []
    ir: Callable[[], Optional[IRReading]] = lambda: None
    depth: Callable[[], Optional[DepthFrame]] = lambda: None


@dataclass
class PilotState:
    running: bool = False
    last_action: str = "idle"          # 'forward', 'turn_left', 'turn_right', 'reverse', 'stop', 'idle'
    last_reason: str = ""
    field_snapshot: dict = field(default_factory=dict)
    ticks: int = 0
    started_at: float = 0.0


class _DriveLike:
    """Structural type matching `DriveController` so the pilot stays
    framework-agnostic in tests. Required methods:
        drive_wheels(left_dir, left_speed, right_dir, right_speed)
        stop()
        set_brake(on)
    """
    def drive_wheels(self, left_dir: str, left_speed: int,
                     right_dir: str, right_speed: int) -> None: ...
    def stop(self) -> None: ...
    def set_brake(self, on: bool) -> None: ...


class AutonomousPilot:
    """Background-thread reactive controller.

    Listeners can subscribe to state changes via `add_listener()` to
    drive UI updates. The Qt facade in `sirena_ui` owns the listener
    that fans these out as Qt signals.
    """

    def __init__(
        self,
        drive: _DriveLike,
        sensors: SensorBundle,
        settings: AutonomySettings,
    ) -> None:
        self._drive = drive
        self._sensors = sensors
        self._settings = settings

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._state = PilotState()
        self._listeners: List[Callable[[PilotState], None]] = []

        # Turn-commit memory: when we pick a side we commit to it for
        # at least `turn_duration_ms` so we don't oscillate on the spot.
        self._commit_until: float = 0.0
        self._commit_action: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_listener(self, fn: Callable[[PilotState], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def state(self) -> PilotState:
        with self._lock:
            return PilotState(
                running=self._state.running,
                last_action=self._state.last_action,
                last_reason=self._state.last_reason,
                field_snapshot=dict(self._state.field_snapshot),
                ticks=self._state.ticks,
                started_at=self._state.started_at,
            )

    def start(self) -> None:
        with self._lock:
            if self._state.running:
                return
            self._state = PilotState(
                running=True,
                last_action="idle",
                last_reason="starting",
                started_at=time.monotonic(),
            )
            self._stop_evt.clear()

        # Make sure the BLDCs are armed.
        try:
            self._drive.set_brake(False)
        except Exception:
            pass

        self._thread = threading.Thread(
            target=self._run, name="AutonomousPilot", daemon=True
        )
        self._thread.start()
        log.info("AutonomousPilot started")
        self._notify()

    def stop(self) -> None:
        with self._lock:
            if not self._state.running:
                return
            self._state.running = False
            self._state.last_action = "stop"
            self._state.last_reason = "operator stop"
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        try:
            self._drive.stop()
        except Exception as exc:
            log.exception("drive.stop() in pilot.stop(): %s", exc)
        log.info("AutonomousPilot stopped")
        self._notify()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        period = 1.0 / max(0.5, float(self._settings.tick_hz))
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self._tick()
            except Exception as exc:
                log.exception("pilot tick failed: %s", exc)
                self._set_action("stop", f"tick error: {exc}")
                try:
                    self._drive.stop()
                except Exception:
                    pass
            elapsed = time.monotonic() - t0
            if elapsed < period:
                self._stop_evt.wait(period - elapsed)

    def _tick(self) -> None:
        s = self._sensors
        obstacle = fuse(
            lidar=s.lidar(),
            ultrasonics=s.ultrasonics(),
            ir=s.ir(),
            depth=s.depth(),
            cliff_min_mm=self._settings.cliff_min_mm,
        )
        with self._lock:
            self._state.field_snapshot = obstacle.as_dict()
            self._state.ticks += 1

        # Layer 0: total sensor blindness. If NO sector has any reading
        # we refuse to move - autonomy without sensors is just a runaway
        # robot. The Map screen surfaces sensor health pills so the
        # operator can see what's missing.
        if (obstacle.forward_mm is None
                and obstacle.left_mm is None
                and obstacle.right_mm is None
                and obstacle.rear_mm is None
                and not obstacle.sources):
            self._set_action("stop", "all sensors blind")
            try:
                self._drive.stop()
            except Exception:
                pass
            return

        # Layer 1: emergency / cliff
        if obstacle.cliff_alarm:
            self._set_action("reverse", "cliff alarm")
            self._reverse_briefly()
            return

        # Layer 2: any-direction critical proximity
        emin = self._settings.emergency_stop_mm
        forward = obstacle.forward_mm
        if forward is not None and forward < emin:
            self._set_action("reverse", f"forward {forward} mm < {emin}")
            self._reverse_briefly()
            return

        # Layer 3: respect an in-flight turn commit
        now = time.monotonic()
        if now < self._commit_until and self._commit_action in ("turn_left", "turn_right"):
            self._apply_action(self._commit_action)
            return

        # Layer 4: forward if clear
        fwd_clear = self._settings.forward_clear_mm
        side_clear = self._settings.side_clear_mm
        forward_ok = obstacle.is_clear(SECTOR_FORWARD, fwd_clear)
        if forward_ok:
            self._set_action(
                "forward",
                f"forward >= {fwd_clear} mm",
            )
            self._apply_action("forward")
            return

        # Layer 5: pick the clearer side and commit a brief turn
        left = obstacle.min_mm(SECTOR_LEFT) or 0
        right = obstacle.min_mm(SECTOR_RIGHT) or 0
        choice = "turn_left" if left >= right else "turn_right"
        self._commit_action = choice
        self._commit_until = now + (self._settings.turn_duration_ms / 1000.0)
        # Per-source breakdown so logs spell out "depth=480 mm even
        # though lidar=2100 mm" - the most common mis-mount pattern
        # is the D435 reading the floor as a phantom forward wall.
        breakdown = ", ".join(
            f"{src}={mm}mm" for src, mm in sorted(obstacle.forward_by_source.items())
        ) or "no forward sensor data"
        forward_str = f"{forward}mm" if forward is not None else "<no data>"
        self._set_action(
            choice,
            f"forward {forward_str} < {fwd_clear}mm clear "
            f"(l={left}, r={right}); per-source: {breakdown}",
        )
        log.info(
            "autonomy turn=%s reason=forward_blocked forward=%s clear=%smm "
            "left=%smm right=%smm by_source=%s",
            choice, forward_str, fwd_clear, left, right,
            dict(obstacle.forward_by_source),
        )
        self._apply_action(choice)

    # ------------------------------------------------------------------
    # Action -> wheels
    # ------------------------------------------------------------------

    def _apply_action(self, action: str) -> None:
        cruise = int(self._settings.cruise_speed_pct)
        turn = int(self._settings.turn_speed_pct)
        try:
            if action == "forward":
                self._drive.drive_wheels(_DIR_FORWARD, cruise, _DIR_FORWARD, cruise)
            elif action == "reverse":
                self._drive.drive_wheels(_DIR_BACK, cruise, _DIR_BACK, cruise)
            elif action == "turn_left":
                self._drive.drive_wheels(_DIR_BACK, turn, _DIR_FORWARD, turn)
            elif action == "turn_right":
                self._drive.drive_wheels(_DIR_FORWARD, turn, _DIR_BACK, turn)
            else:
                self._drive.stop()
        except Exception as exc:
            log.exception("apply_action(%s): %s", action, exc)

    def _reverse_briefly(self) -> None:
        cruise = int(self._settings.cruise_speed_pct)
        try:
            self._drive.drive_wheels(_DIR_BACK, cruise, _DIR_BACK, cruise)
        except Exception:
            pass
        # Hold the reverse for the configured backoff window so we
        # actually move clear of the obstacle before re-deciding.
        deadline = time.monotonic() + (self._settings.backoff_duration_ms / 1000.0)
        while time.monotonic() < deadline:
            if self._stop_evt.is_set():
                break
            self._stop_evt.wait(0.05)
        try:
            self._drive.stop()
        except Exception:
            pass
        # Force a fresh side-pick on the next tick.
        self._commit_action = ""
        self._commit_until = 0.0

    def _set_action(self, action: str, reason: str) -> None:
        with self._lock:
            self._state.last_action = action
            self._state.last_reason = reason
        self._notify()

    def _notify(self) -> None:
        with self._lock:
            snapshot = PilotState(
                running=self._state.running,
                last_action=self._state.last_action,
                last_reason=self._state.last_reason,
                field_snapshot=dict(self._state.field_snapshot),
                ticks=self._state.ticks,
                started_at=self._state.started_at,
            )
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(snapshot)
            except Exception:
                log.exception("autonomy listener raised")
