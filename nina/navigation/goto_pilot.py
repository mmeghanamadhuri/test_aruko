"""Goal-directed pilot for "tap a point on the map -> drive there".

This is the goto-mode counterpart to `nina.navigation.autonomous_pilot`
(reactive wander). Both pilots share the same drive surface
(`DriveController`-shaped) and the same `obstacle_field.fuse()`
safety layer; this one adds:

  1. **Goal pose** in world millimetres (origin = SLAM map centre).
  2. **A* path** computed on the live BreezySLAM occupancy grid by
     `nina.navigation.path_planner.plan_path`.
  3. **Pure-pursuit follower** with a heading deadband - tracks the
     planned path while smoothing the wheel commands.
  4. **Reactive veto layer** - on every tick we still consume the
     `ObstacleField`. If the live sensors see an obstacle the SLAM
     map didn't know about (e.g. a chair the lidar plane skimmed
     under, or a person who just walked in), we ABORT forward
     motion, back off briefly, and replan from the new pose.
  5. **Termination states** that the UI surfaces verbatim:
        arrived | unreachable | stuck | lost | cancelled | error

The pilot is framework-agnostic in the same way `AutonomousPilot`
is - it pulls SLAM snapshots / poses / sensor readings through a
`GotoSensorBundle` so unit tests can plug in fakes without a Jetson.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from nina.config.settings import AutonomySettings, GotoSettings
from nina.navigation.obstacle_field import (
    SECTOR_FORWARD,
    SECTOR_LEFT,
    SECTOR_RIGHT,
    fuse,
)
from nina.navigation.path_planner import PlanResult, plan_path
from nina.sensors.types import (
    DepthFrame,
    IRReading,
    LidarScan,
    UltrasonicReading,
)


log = logging.getLogger("nina.goto_pilot")


# Direction tokens for the drive surface (matches AutonomousPilot).
_DIR_FORWARD = "forward"
_DIR_BACK = "back"


# ----------------------------------------------------------------------
# State + bundle
# ----------------------------------------------------------------------


# Public state strings. Kept as constants so UI / link-daemon / Android
# can compare without typo-spotting.
STATE_PLANNING = "planning"
STATE_DRIVING = "driving"
STATE_TURNING = "turning"
STATE_REPLANNING = "replanning"
STATE_AVOIDING = "avoiding"     # reactive veto -> backing off
STATE_ARRIVED = "arrived"
STATE_UNREACHABLE = "unreachable"
STATE_STUCK = "stuck"
STATE_LOST = "lost"
STATE_CANCELLED = "cancelled"
STATE_ERROR = "error"


@dataclass
class GotoPose:
    x_mm: float
    y_mm: float
    theta_deg: float
    updated_at: float = 0.0


@dataclass
class GotoSnapshot:
    """Subset of the SLAM snapshot the planner needs."""

    grid_bytes: bytes
    width: int
    height: int
    scale_mm_per_px: float


@dataclass
class GotoSensorBundle:
    """Dependency-injection seam for the pilot.

    Each callable is invoked once per tick. `pose()` and `snapshot()`
    must come from the same SLAM stream (so the pose's frame matches
    the grid centre); `lidar / depth / ir / ultrasonics` come from
    the active autonomy sensor stack and feed into `obstacle_field`.
    A getter that returns `None` is treated as "no data this tick"
    and the pilot will do its best on whatever is available.
    """

    pose: Callable[[], Optional[GotoPose]] = lambda: None
    snapshot: Callable[[], Optional[GotoSnapshot]] = lambda: None
    lidar: Callable[[], Optional[LidarScan]] = lambda: None
    ultrasonics: Callable[[], List[UltrasonicReading]] = lambda: []
    ir: Callable[[], Optional[IRReading]] = lambda: None
    depth: Callable[[], Optional[DepthFrame]] = lambda: None


@dataclass
class GotoState:
    running: bool = False
    state: str = "idle"
    reason: str = ""
    goal_mm: Optional[Tuple[float, float]] = None
    pose: Optional[Tuple[float, float, float]] = None
    waypoints_mm: List[Tuple[float, float]] = field(default_factory=list)
    distance_to_goal_mm: Optional[float] = None
    heading_error_deg: Optional[float] = None
    ticks: int = 0
    started_at: float = 0.0
    snapped_goal_mm: Optional[Tuple[float, float]] = None
    last_action: str = "idle"

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "state": self.state,
            "reason": self.reason,
            "goal_mm": (
                {"x": self.goal_mm[0], "y": self.goal_mm[1]}
                if self.goal_mm else None
            ),
            "snapped_goal_mm": (
                {"x": self.snapped_goal_mm[0], "y": self.snapped_goal_mm[1]}
                if self.snapped_goal_mm else None
            ),
            "pose": (
                {
                    "x_mm": self.pose[0],
                    "y_mm": self.pose[1],
                    "theta_deg": self.pose[2],
                }
                if self.pose else None
            ),
            "waypoints_mm": [
                {"x": x, "y": y} for x, y in self.waypoints_mm
            ],
            "distance_to_goal_mm": self.distance_to_goal_mm,
            "heading_error_deg": self.heading_error_deg,
            "ticks": self.ticks,
            "started_at": self.started_at,
            "last_action": self.last_action,
        }


# ----------------------------------------------------------------------
# Pilot
# ----------------------------------------------------------------------


class GotoPilot:
    """Background-thread goal navigator.

    Lifecycle:

        pilot = GotoPilot(drive, sensors, goto_settings, autonomy_settings)
        pilot.start(goal_x_mm, goal_y_mm)   # arms + spins up worker
        ...
        pilot.cancel()                       # operator hit Cancel
        pilot.stop()                         # shutdown / autonomy off

    Listeners subscribe via `add_listener(fn)` to receive a `GotoState`
    snapshot every time something changes. The Qt facade in the UI
    fans these into Qt signals.
    """

    def __init__(
        self,
        drive,
        sensors: GotoSensorBundle,
        goto_settings: GotoSettings,
        autonomy_settings: AutonomySettings,
    ) -> None:
        self._drive = drive
        self._sensors = sensors
        self._goto = goto_settings
        self._auto = autonomy_settings

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._state = GotoState()
        self._listeners: List[Callable[[GotoState], None]] = []

        # Replan bookkeeping
        self._waypoints: List[Tuple[float, float]] = []
        # Index of the next un-passed waypoint along `_waypoints`.
        # Pure-pursuit lookahead is computed from this offset onward
        # so a bot that has driven past a waypoint never sees it as
        # a candidate target again (which would manifest as the bot
        # spinning to face a behind-it path vertex).
        self._path_index: int = 0
        self._next_replan_at: float = 0.0

        # Stuck detection: rolling samples of (t, x, y).
        self._motion_log: List[Tuple[float, float, float]] = []
        # First tick forward fell below fwd_clear (goto veto); None if clear.
        self._fwd_dead_end_since: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_listener(self, fn: Callable[[GotoState], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def state(self) -> GotoState:
        with self._lock:
            return _clone_state(self._state)

    def is_running(self) -> bool:
        with self._lock:
            return self._state.running

    def start(self, goal_x_mm: float, goal_y_mm: float) -> None:
        spawn_thread = False
        with self._lock:
            if self._state.running:
                # Update goal in-flight rather than spinning a fresh thread.
                self._state.goal_mm = (float(goal_x_mm), float(goal_y_mm))
                self._waypoints = []
                self._next_replan_at = 0.0
                self._fwd_dead_end_since = None
                self._state.state = STATE_PLANNING
                self._state.reason = "goal updated"
            else:
                self._state = GotoState(
                    running=True,
                    state=STATE_PLANNING,
                    reason="starting",
                    goal_mm=(float(goal_x_mm), float(goal_y_mm)),
                    started_at=time.monotonic(),
                )
                self._waypoints = []
                self._path_index = 0
                self._next_replan_at = 0.0
                self._motion_log = []
                self._fwd_dead_end_since = None
                self._stop_evt.clear()
                spawn_thread = True

        if not spawn_thread:
            self._notify()
            return

        try:
            self._drive.set_brake(False)
        except Exception:
            pass

        self._thread = threading.Thread(
            target=self._run, name="GotoPilot", daemon=True
        )
        self._thread.start()
        log.info("GotoPilot started: goal=(%.0f, %.0f) mm", goal_x_mm, goal_y_mm)
        self._notify()

    def cancel(self) -> None:
        with self._lock:
            if not self._state.running:
                return
            self._state.state = STATE_CANCELLED
            self._state.reason = "operator cancelled"
        # `_teardown` joins the worker thread before notifying. We
        # pass `terminal_state=STATE_CANCELLED` so any state
        # mutations the worker performs in its in-flight tick are
        # overwritten back to CANCELLED before the listener sees
        # the final state. Without this, a tick that observed
        # state=DRIVING/TURNING while cancel() was waiting on the
        # lock would clobber the cancel signal, and the UI would
        # see "running=False, state=driving" - confusing.
        self._teardown(terminal_state=STATE_CANCELLED)

    def stop(self) -> None:
        with self._lock:
            running = self._state.running
        if not running:
            return
        self._teardown()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _teardown(self, *, terminal_state: Optional[str] = None) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        try:
            self._drive.stop()
        except Exception as exc:
            log.exception("drive.stop() in goto teardown: %s", exc)
        with self._lock:
            self._state.running = False
            self._state.last_action = "stop"
            if terminal_state is not None:
                self._state.state = terminal_state
        self._notify()

    def _run(self) -> None:
        period = 1.0 / max(0.5, float(self._goto.tick_hz))
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                terminal = self._tick()
            except Exception as exc:
                log.exception("goto tick failed: %s", exc)
                with self._lock:
                    self._state.state = STATE_ERROR
                    self._state.reason = f"tick error: {exc}"
                terminal = True
                try:
                    self._drive.stop()
                except Exception:
                    pass
            self._notify()
            if terminal:
                with self._lock:
                    self._state.running = False
                    self._state.last_action = "stop"
                try:
                    self._drive.stop()
                except Exception:
                    pass
                self._notify()
                return
            elapsed = time.monotonic() - t0
            if elapsed < period:
                self._stop_evt.wait(period - elapsed)

    def _tick(self) -> bool:
        """One control iteration. Returns True if the pilot should
        terminate after this tick (arrived / unreachable / stuck / etc).
        """
        # Cancel race: `cancel()` sets state=CANCELLED then signals
        # `_stop_evt`. If we entered _tick before that signal but
        # observe it now, return immediately *without* mutating
        # state - otherwise we'd clobber the operator-set
        # CANCELLED back to DRIVING / TURNING.
        if self._stop_evt.is_set():
            return True

        sensors = self._sensors
        pose = sensors.pose()
        snap = sensors.snapshot()
        with self._lock:
            goal = self._state.goal_mm
            self._state.ticks += 1
        if goal is None:
            with self._lock:
                self._state.state = STATE_CANCELLED
                self._state.reason = "no goal set"
            return True

        # SLAM data missing -> we can't plan. Hold position and
        # report 'lost' so the operator knows why.
        if pose is None or snap is None:
            with self._lock:
                self._state.state = STATE_LOST
                self._state.reason = "no SLAM pose / snapshot"
                self._state.last_action = "stop"
            try:
                self._drive.stop()
            except Exception:
                pass
            # Don't terminate immediately on a single missed read -
            # SLAM might recover next tick. Only escalate to terminal
            # if we've been lost for >2 s.
            return self._has_been_lost_too_long()

        with self._lock:
            self._state.pose = (pose.x_mm, pose.y_mm, pose.theta_deg)

        # Arrival check up front so a goal you're already standing on
        # short-circuits the planner.
        dist = math.hypot(goal[0] - pose.x_mm, goal[1] - pose.y_mm)
        with self._lock:
            self._state.distance_to_goal_mm = dist
        if dist <= self._goto.arrival_radius_mm:
            with self._lock:
                self._state.state = STATE_ARRIVED
                self._state.reason = f"within {self._goto.arrival_radius_mm} mm of goal"
                self._state.last_action = "stop"
            try:
                self._drive.stop()
            except Exception:
                pass
            return True

        # Reactive obstacle field. We compute it BEFORE driving so
        # the cliff / emergency layer can short-circuit a forward
        # tick. The wander pilot does the same dance.
        obstacle = fuse(
            lidar=sensors.lidar(),
            ultrasonics=sensors.ultrasonics(),
            ir=sensors.ir(),
            depth=sensors.depth(),
            cliff_min_mm=self._auto.cliff_min_mm,
        )
        if obstacle.cliff_alarm:
            with self._lock:
                self._state.state = STATE_AVOIDING
                self._state.reason = "cliff alarm"
                self._state.last_action = "reverse"
            self._fwd_dead_end_since = None
            self._reverse_briefly()
            self._waypoints = []  # force replan from the new pose
            self._path_index = 0
            return False

        emin = self._auto.emergency_stop_mm
        forward_mm = obstacle.forward_mm
        if forward_mm is not None and forward_mm < emin:
            with self._lock:
                self._state.state = STATE_AVOIDING
                self._state.reason = (
                    f"forward {forward_mm} mm < {emin} mm e-stop"
                )
                self._state.last_action = "reverse"
            self._fwd_dead_end_since = None
            self._reverse_briefly()
            self._waypoints = []
            self._path_index = 0
            return False

        fwd_clear = self._goto.forward_clear_mm
        _tn = time.monotonic()
        _forward_veto = forward_mm is None or forward_mm < fwd_clear
        if not _forward_veto:
            self._fwd_dead_end_since = None
        elif self._fwd_dead_end_since is None:
            self._fwd_dead_end_since = _tn

        # (Re)plan if we don't have a path or the replan timer fired.
        now = time.monotonic()
        need_plan = (not self._waypoints) or now >= self._next_replan_at
        if need_plan:
            with self._lock:
                self._state.state = (
                    STATE_REPLANNING if self._waypoints else STATE_PLANNING
                )
                self._state.reason = (
                    "scheduled replan" if self._waypoints
                    else "initial plan"
                )
            result = plan_path(
                snap.grid_bytes, snap.width, snap.height,
                snap.scale_mm_per_px,
                start_mm=(pose.x_mm, pose.y_mm),
                goal_mm=goal,
                footprint_radius_mm=self._goto.footprint_radius_mm,
                min_passage_width_mm=self._goto.min_passage_width_mm,
                unknown_pixel_cost=self._goto.unknown_pixel_cost,
            )
            if not result.ok:
                log.warning(
                    "Goto plan_path failed: %s (goal=%s start=(%.0f,%.0f))",
                    result.reason,
                    goal,
                    pose.x_mm,
                    pose.y_mm,
                )
                # Record the snapped pin even on failure so the UI
                # can show "we tried, but here".
                with self._lock:
                    self._state.state = STATE_UNREACHABLE
                    self._state.reason = (
                        f"plan failed: {result.reason}"
                    )
                    self._state.snapped_goal_mm = (
                        _snapped_goal_mm(result, snap)
                    )
                    self._state.last_action = "stop"
                try:
                    self._drive.stop()
                except Exception:
                    pass
                return True
            self._waypoints = list(result.waypoints_mm)
            self._path_index = 0
            self._next_replan_at = now + self._goto.replan_period_sec
            with self._lock:
                self._state.waypoints_mm = list(self._waypoints)
                self._state.snapped_goal_mm = (
                    _snapped_goal_mm(result, snap)
                )

        # Pure-pursuit lookahead on the planned path.
        target = self._lookahead_target(pose, self._waypoints)
        if target is None:
            # Path exists but we're past its end - treat as arrived.
            with self._lock:
                self._state.state = STATE_ARRIVED
                self._state.reason = "passed final waypoint"
                self._state.last_action = "stop"
            try:
                self._drive.stop()
            except Exception:
                pass
            return True

        heading_err = _heading_error_deg(pose, target)
        with self._lock:
            self._state.heading_error_deg = heading_err

        # Stuck detection runs every tick once we have at least one
        # forward command in the log.
        self._motion_log.append((now, pose.x_mm, pose.y_mm))
        cutoff = now - self._goto.stuck_window_sec
        self._motion_log = [m for m in self._motion_log if m[0] >= cutoff]
        if (
            self._has_logged_full_window(now)
            and self._max_displacement(self._motion_log) < self._goto.stuck_motion_mm
        ):
            with self._lock:
                self._state.state = STATE_STUCK
                self._state.reason = (
                    f"moved < {self._goto.stuck_motion_mm} mm in last "
                    f"{self._goto.stuck_window_sec:.0f} s"
                )
                self._state.last_action = "stop"
            try:
                self._drive.stop()
            except Exception:
                pass
            return True

        # Heading deadband - turn in place when we're well off, else
        # forward. We DON'T mix forward + skid-steer arc here because
        # the BLDC drivetrain on Nina is differential and the wheels
        # take a noticeable moment to respond; a clean turn-then-go
        # is more legible to the operator and easier to debug than a
        # smooth arc that visually looks like "the bot is drifting".
        deadband = self._goto.heading_deadband_deg
        if abs(heading_err) > deadband:
            action = "turn_left" if heading_err > 0 else "turn_right"
            self._apply_action(action)
            with self._lock:
                self._state.state = STATE_TURNING
                self._state.reason = (
                    f"heading err {heading_err:+.1f} deg > "
                    f"{deadband:.0f} deg deadband"
                )
                self._state.last_action = action
            return False

        # Forward we go. Use goto's own forward_clear_mm (lower than
        # wander's default) so map-following can proceed in normal rooms;
        # wander keeps the conservative 1200 mm for reactive roaming.
        if forward_mm is not None and forward_mm < fwd_clear:
            side_clear = self._auto.side_clear_mm
            lm = obstacle.min_mm(SECTOR_LEFT)
            rm = obstacle.min_mm(SECTOR_RIGHT)
            both_tight = (
                lm is not None
                and rm is not None
                and lm < side_clear
                and rm < side_clear
            )
            bsec = self._auto.fwd_blocked_backup_sec
            blocked_long = (
                bsec > 0.0
                and self._fwd_dead_end_since is not None
                and (_tn - self._fwd_dead_end_since) >= bsec
            )
            if both_tight or blocked_long:
                self._fwd_dead_end_since = None
                why = (
                    "both sides < side_clear"
                    if both_tight
                    else f"forward blocked {bsec:.1f}s"
                )
                with self._lock:
                    self._state.state = STATE_AVOIDING
                    self._state.reason = (
                        f"forward {forward_mm} mm < {fwd_clear} mm; "
                        f"dead-end backoff ({why})"
                    )
                    self._state.last_action = "reverse"
                self._reverse_briefly()
            else:
                with self._lock:
                    self._state.state = STATE_AVOIDING
                    self._state.reason = (
                        f"forward {forward_mm} mm < {fwd_clear} mm clear; "
                        "replanning"
                    )
                    self._state.last_action = "stop"
                try:
                    self._drive.stop()
                except Exception:
                    pass
            self._waypoints = []
            self._path_index = 0
            return False

        self._apply_action("forward")
        with self._lock:
            self._state.state = STATE_DRIVING
            self._state.reason = (
                f"heading err {heading_err:+.1f} deg, dist {dist:.0f} mm"
            )
            self._state.last_action = "forward"
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_action(self, action: str) -> None:
        cruise = int(self._goto.cruise_speed_pct)
        turn = int(self._goto.turn_speed_pct)
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
        except Exception:
            log.exception("apply_action(%s)", action)

    def _reverse_briefly(self) -> None:
        cruise = int(self._goto.cruise_speed_pct)
        try:
            self._drive.drive_wheels(_DIR_BACK, cruise, _DIR_BACK, cruise)
        except Exception:
            pass
        deadline = time.monotonic() + (self._auto.backoff_duration_ms / 1000.0)
        while time.monotonic() < deadline:
            if self._stop_evt.is_set():
                break
            self._stop_evt.wait(0.05)
        try:
            self._drive.stop()
        except Exception:
            pass

    def _lookahead_target(
        self,
        pose: GotoPose,
        waypoints: List[Tuple[float, float]],
    ) -> Optional[Tuple[float, float]]:
        """Pure-pursuit lookahead.

        Walks the path from `_path_index` forward, advancing past
        waypoints the bot has already reached (within
        `arrival_radius_mm`) and returning the first remaining
        waypoint that's at least `lookahead_mm` away. Falls back to
        the final waypoint if the bot is closer to the goal than
        the lookahead distance (so the pilot still drives *at* the
        goal as it homes in).
        """
        if not waypoints:
            return None
        n = len(waypoints)
        # Advance past waypoints we've effectively reached. We use a
        # generous radius (max of arrival radius, 0.6 * lookahead)
        # so a small overshoot doesn't snag us against a passed
        # vertex on the next tick.
        passed_radius = max(
            self._goto.arrival_radius_mm,
            int(0.6 * self._goto.lookahead_mm),
        )
        idx = max(0, min(self._path_index, n - 1))
        while idx < n - 1:
            wx, wy = waypoints[idx]
            d = math.hypot(wx - pose.x_mm, wy - pose.y_mm)
            if d <= passed_radius:
                idx += 1
                continue
            break
        self._path_index = idx
        # Look forward from `idx` for the first waypoint at or past
        # the lookahead distance.
        look = self._goto.lookahead_mm
        for j in range(idx, n):
            wx, wy = waypoints[j]
            d = math.hypot(wx - pose.x_mm, wy - pose.y_mm)
            if d >= look:
                return (wx, wy)
        return waypoints[-1]

    def _has_been_lost_too_long(self) -> bool:
        # 2 s grace window before LOST becomes terminal.
        with self._lock:
            started = self._state.started_at
            ticks = self._state.ticks
        if ticks < int(2.0 * self._goto.tick_hz):
            return False
        return (time.monotonic() - started) >= 2.0

    def _has_logged_full_window(self, now: float) -> bool:
        if not self._motion_log:
            return False
        return (now - self._motion_log[0][0]) >= self._goto.stuck_window_sec * 0.9

    @staticmethod
    def _max_displacement(log: List[Tuple[float, float, float]]) -> float:
        if len(log) < 2:
            return 0.0
        x0, y0 = log[0][1], log[0][2]
        max_d = 0.0
        for _, x, y in log[1:]:
            d = math.hypot(x - x0, y - y0)
            if d > max_d:
                max_d = d
        return max_d

    def _notify(self) -> None:
        with self._lock:
            snapshot = _clone_state(self._state)
            listeners = list(self._listeners)
        # Listeners run OUTSIDE the lock so a slow / re-entrant
        # listener can't block the worker thread or deadlock against
        # a UI-side callback that wants to inspect pilot state.
        for fn in listeners:
            try:
                fn(snapshot)
            except Exception:
                log.exception("goto listener raised")


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _clone_state(s: GotoState) -> GotoState:
    return GotoState(
        running=s.running,
        state=s.state,
        reason=s.reason,
        goal_mm=s.goal_mm,
        pose=s.pose,
        waypoints_mm=list(s.waypoints_mm),
        distance_to_goal_mm=s.distance_to_goal_mm,
        heading_error_deg=s.heading_error_deg,
        ticks=s.ticks,
        started_at=s.started_at,
        snapped_goal_mm=s.snapped_goal_mm,
        last_action=s.last_action,
    )


def _heading_error_deg(pose: GotoPose, target: Tuple[float, float]) -> float:
    """Heading error in degrees, normalised to (-180, +180].

    Convention: positive theta turns Nina counter-clockwise (left),
    matching `OccupancyGridView`'s pose-triangle math (front of the
    bot = +y world / -y screen). A target to the bot's LEFT yields a
    positive error (so we 'turn_left' to reduce it).
    """
    dx = target[0] - pose.x_mm
    dy = target[1] - pose.y_mm
    target_deg = math.degrees(math.atan2(dx, dy))   # +y forward; +x right
    err = target_deg - pose.theta_deg
    while err > 180.0:
        err -= 360.0
    while err <= -180.0:
        err += 360.0
    # Sign convention: positive err -> target is to the LEFT of the
    # bot's heading -> turn_left. Our atan2(dx, dy) gives positive
    # for targets to the right (since +x = right). Flip it.
    return -err


def _snapped_goal_mm(
    result: PlanResult, snap: GotoSnapshot,
) -> Optional[Tuple[float, float]]:
    if result.snapped_goal_px is None:
        return None
    px, py = result.snapped_goal_px
    cx = snap.width / 2.0
    cy = snap.height / 2.0
    return (
        (px - cx) * snap.scale_mm_per_px,
        (cy - py) * snap.scale_mm_per_px,
    )
