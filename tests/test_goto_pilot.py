"""Tests for ``nina.navigation.goto_pilot.GotoPilot``.

The pilot owns a background thread, so we steer it with a fake
``DriveController``-shaped object plus injection-points for SLAM
pose, snapshot, and the reactive sensor feed. Each test:

  1. Starts the pilot with a goal.
  2. Polls until the pilot transitions into a terminal or expected
     intermediate state (5-second hard cap so a hang is loud, not silent).
  3. Asserts the recorded drive commands match the expected behaviour.

We don't assert exact reason strings - those are diagnostics and
free to change. We DO assert on the public state ladder:

    planning -> driving / turning -> arrived
                              \
                               -> avoiding -> replanning -> ...
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pytest

from nina.config.settings import AutonomySettings, GotoSettings
from nina.navigation.goto_pilot import (
    GotoPilot,
    GotoPose,
    GotoSensorBundle,
    GotoSnapshot,
    GotoState,
    STATE_ARRIVED,
    STATE_AVOIDING,
    STATE_DRIVING,
    STATE_LOST,
    STATE_PLANNING,
    STATE_STUCK,
    STATE_TURNING,
    STATE_UNREACHABLE,
)
from nina.sensors.types import LidarScan, UltrasonicReading


# Common settings for the tests. Tick fast (50 Hz) so a "wait for
# terminal" loop doesn't hold up CI for seconds; stuck-window is
# generous so it doesn't fire spuriously when the fake bot doesn't
# move every tick (e.g. while turning in place).
def _goto_settings(**over) -> GotoSettings:
    base = dict(
        arrival_radius_mm=200,
        footprint_radius_mm=0,
        cruise_speed_pct=20,
        turn_speed_pct=20,
        heading_deadband_deg=15.0,
        lookahead_mm=400,
        replan_period_sec=10.0,
        stuck_window_sec=10.0,
        stuck_motion_mm=10,
        tick_hz=50.0,
        unknown_pixel_cost=1.5,
    )
    base.update(over)
    return GotoSettings(**base)


def _autonomy_settings(**over) -> AutonomySettings:
    base = dict(
        tick_hz=50.0,
        cruise_speed_pct=20,
        turn_speed_pct=20,
        forward_clear_mm=600,
        side_clear_mm=300,
        emergency_stop_mm=300,
        cliff_min_mm=60,
        turn_duration_ms=100,
        backoff_duration_ms=50,
    )
    base.update(over)
    return AutonomySettings(**base)


# ----------------------------------------------------------------------
# Test fixtures
# ----------------------------------------------------------------------


class FakeDrive:
    """Captures the wheel commands the pilot issues."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, int, str, int]] = []
        self._brake = False

    def set_brake(self, on: bool) -> None:
        self._brake = bool(on)

    def stop(self) -> None:
        self.calls.append(("stop", "n/a", 0, "n/a", 0))

    def drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        self.calls.append(
            ("drive", left_dir, int(left_speed),
             right_dir, int(right_speed))
        )

    def actions(self) -> List[str]:
        out: List[str] = []
        for tag, ld, ls, rd, rs in self.calls:
            if tag == "stop":
                out.append("stop")
            elif ld == "forward" and rd == "forward":
                out.append("forward")
            elif ld == "back" and rd == "back":
                out.append("reverse")
            elif ld == "back" and rd == "forward":
                out.append("turn_left")
            elif ld == "forward" and rd == "back":
                out.append("turn_right")
            else:
                out.append("?")
        return out


def _open_grid(w: int, h: int) -> bytes:
    """All-free 80x80 grid stored as bytes for the snapshot getter."""
    return bytes([255] * (w * h))


def _wait_for(pilot: GotoPilot, predicate, timeout: float = 5.0):
    """Block until predicate(state) is True or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = pilot.state()
        if predicate(st):
            return st
        time.sleep(0.005)
    raise AssertionError(
        f"timeout waiting for predicate; last state={pilot.state().state}"
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_arrival_when_already_within_radius():
    """Starting on top of the goal should fast-path to ARRIVED in
    one tick - no drive commands beyond the final stop.
    """
    drive = FakeDrive()
    pose = GotoPose(0.0, 0.0, 0.0)
    snap = GotoSnapshot(_open_grid(80, 80), 80, 80, 50.0)
    bundle = GotoSensorBundle(
        pose=lambda: pose, snapshot=lambda: snap,
        lidar=lambda: None, ultrasonics=lambda: [],
        ir=lambda: None, depth=lambda: None,
    )
    pilot = GotoPilot(drive, bundle, _goto_settings(), _autonomy_settings())
    pilot.start(50.0, 0.0)   # 50 mm < arrival_radius_mm=200
    final = _wait_for(pilot, lambda s: s.state == STATE_ARRIVED, timeout=2.0)
    pilot.stop()
    assert final.state == STATE_ARRIVED
    assert "stop" in drive.actions()
    forwards = [a for a in drive.actions() if a == "forward"]
    assert forwards == [], "should not drive forward when already arrived"


def test_pose_progression_drives_then_arrives():
    """Simulate the bot moving along the path - pilot should issue
    forward commands and eventually report ARRIVED.
    """
    drive = FakeDrive()
    # Pose is mutable across getter calls so the test loop can
    # advance it after each forward command.
    state = {"x": -1000.0, "y": 0.0, "theta": 90.0}  # facing +x

    def _get_pose():
        return GotoPose(state["x"], state["y"], state["theta"])

    snap = GotoSnapshot(_open_grid(80, 80), 80, 80, 50.0)

    bundle = GotoSensorBundle(
        pose=_get_pose, snapshot=lambda: snap,
        lidar=lambda: None, ultrasonics=lambda: [],
        ir=lambda: None, depth=lambda: None,
    )
    pilot = GotoPilot(
        drive, bundle,
        _goto_settings(stuck_window_sec=30.0, stuck_motion_mm=1),
        _autonomy_settings(),
    )

    # Run the pilot in a thread that also "moves the bot" each tick
    # in response to forward commands.
    pilot.start(1000.0, 0.0)

    # Advance the pose toward the goal in chunks until ARRIVED.
    deadline = time.monotonic() + 5.0
    arrived = False
    while time.monotonic() < deadline:
        st = pilot.state()
        if st.state == STATE_ARRIVED:
            arrived = True
            break
        # Move the fake bot toward the goal whenever the pilot is
        # currently driving forward.
        if st.last_action == "forward":
            state["x"] = min(1000.0, state["x"] + 50.0)
        time.sleep(0.05)
    pilot.stop()

    assert arrived, f"never arrived; last state={pilot.state().state}"
    actions = drive.actions()
    assert "forward" in actions
    assert actions[-1] == "stop"


def test_lidar_veto_triggers_avoiding_state():
    """A close lidar return inside the e-stop radius should kick
    the pilot into AVOIDING and issue a reverse, regardless of
    whether the planned path looked clear.
    """
    drive = FakeDrive()
    pose = GotoPose(-1000.0, 0.0, 90.0)
    snap = GotoSnapshot(_open_grid(80, 80), 80, 80, 50.0)
    # Lidar reports a wall 200 mm ahead in the forward sector.
    n = 360
    distances = [0] * n
    distances[0] = 200    # straight ahead, well below e-stop=300
    scan = LidarScan(distances_mm=distances, timestamp_s=0.0, quality=1.0)

    bundle = GotoSensorBundle(
        pose=lambda: pose, snapshot=lambda: snap,
        lidar=lambda: scan, ultrasonics=lambda: [],
        ir=lambda: None, depth=lambda: None,
    )
    pilot = GotoPilot(drive, bundle, _goto_settings(), _autonomy_settings())
    pilot.start(1000.0, 0.0)
    _wait_for(pilot, lambda s: s.state == STATE_AVOIDING, timeout=2.0)
    pilot.stop()
    assert "reverse" in drive.actions()


def test_no_pose_yields_lost_then_terminates():
    """SLAM pose unavailable for >2 s -> pilot terminates LOST.
    Even with a brisk tick rate the pilot must wait its grace
    period before bailing.
    """
    drive = FakeDrive()
    snap = GotoSnapshot(_open_grid(80, 80), 80, 80, 50.0)
    bundle = GotoSensorBundle(
        pose=lambda: None, snapshot=lambda: snap,
        lidar=lambda: None, ultrasonics=lambda: [],
        ir=lambda: None, depth=lambda: None,
    )
    pilot = GotoPilot(
        drive, bundle, _goto_settings(tick_hz=50.0), _autonomy_settings(),
    )
    pilot.start(0.0, 1000.0)
    final = _wait_for(
        pilot,
        lambda s: s.state == STATE_LOST and not s.running,
        timeout=4.0,
    )
    pilot.stop()
    assert final.state == STATE_LOST


def test_cancel_terminates_pilot_and_stops_drive():
    drive = FakeDrive()
    pose = GotoPose(-2000.0, 0.0, 90.0)
    snap = GotoSnapshot(_open_grid(80, 80), 80, 80, 50.0)
    bundle = GotoSensorBundle(
        pose=lambda: pose, snapshot=lambda: snap,
        lidar=lambda: None, ultrasonics=lambda: [],
        ir=lambda: None, depth=lambda: None,
    )
    pilot = GotoPilot(drive, bundle, _goto_settings(), _autonomy_settings())
    pilot.start(1500.0, 0.0)
    _wait_for(pilot, lambda s: s.running and s.state in (
        STATE_PLANNING, STATE_DRIVING, STATE_TURNING,
    ))
    pilot.cancel()
    final = _wait_for(pilot, lambda s: not s.running, timeout=2.0)
    assert final.state in ("cancelled", "arrived")  # raceless terminal
    assert "stop" in drive.actions()


def test_heading_off_axis_emits_turn_first():
    """Goal at +x with bot facing forward (theta=0 = +y) should turn
    in place before forwarding.
    """
    drive = FakeDrive()
    pose = GotoPose(0.0, 0.0, 0.0)   # facing +y
    snap = GotoSnapshot(_open_grid(80, 80), 80, 80, 50.0)
    bundle = GotoSensorBundle(
        pose=lambda: pose, snapshot=lambda: snap,
        lidar=lambda: None, ultrasonics=lambda: [],
        ir=lambda: None, depth=lambda: None,
    )
    # Use a settings where the goal is far enough that we can't
    # accidentally arrive on the first tick.
    pilot = GotoPilot(drive, bundle, _goto_settings(), _autonomy_settings())
    pilot.start(1500.0, 0.0)   # +x: requires a right turn
    # Wait for the pilot to settle into TURNING (heading err > deadband).
    _wait_for(
        pilot,
        lambda s: s.state == STATE_TURNING,
        timeout=2.0,
    )
    pilot.cancel()
    pilot.stop()
    actions = drive.actions()
    assert "turn_right" in actions or "turn_left" in actions
