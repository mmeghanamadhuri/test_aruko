"""Tests for AutonomyController goto-mode lifecycle.

Goto mode flips the controller's internal pilot from
`AutonomousPilot` to `GotoPilot`, so we have to verify:

  * `set_goal` while autonomy is OFF turns autonomy ON, swaps to
    goto, and remembers it should disable autonomy on `clear_goal`.
  * `set_goal` while autonomy is ON in wander stops the wander
    pilot, starts the goto pilot, and `clear_goal` returns to
    wander (NOT to off).
  * `set_goal` while already in goto updates the in-flight goal
    rather than tearing the pilot down (the pilot internally
    handles the replan).
  * `current_mode()` reflects the active mode at every step.

We use stubs for both pilots so no actual sensors / threads are
involved - we only care about the controller's bookkeeping.
"""

from __future__ import annotations

from typing import List

import pytest

from nina.config.settings import AutonomySettings, GotoSettings
import sirena_ui.workers.autonomy_controller as ac
from sirena_ui.workers.autonomy_controller import AutonomyController


# ----------------------------------------------------------------------
# Stubs
# ----------------------------------------------------------------------


class _StubWanderPilot:
    instances: List["_StubWanderPilot"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.started = False
        self.stopped = False
        self._listeners: list = []
        self._state = ac.PilotState(running=False)
        _StubWanderPilot.instances.append(self)

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def start(self) -> None:
        self.started = True
        self._state = ac.PilotState(running=True)

    def stop(self) -> None:
        self.stopped = True
        self._state = ac.PilotState(running=False)

    def state(self) -> "ac.PilotState":
        return self._state


class _StubGotoPilot:
    instances: List["_StubGotoPilot"] = []

    def __init__(self, drive, sensors, goto_settings, autonomy_settings) -> None:
        self.start_calls: list = []
        self.stop_calls: int = 0
        self._listeners: list = []
        self._running = False
        self._goal = None
        _StubGotoPilot.instances.append(self)

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def start(self, x: float, y: float) -> None:
        self.start_calls.append((x, y))
        self._goal = (x, y)
        self._running = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = False

    def state(self):
        from nina.navigation.goto_pilot import GotoState
        return GotoState(running=self._running, state="planning",
                         goal_mm=self._goal)


class _FakeDepth:
    def __init__(self) -> None:
        self.opens = 0
        self.closes = 0

    def open(self) -> None:
        self.opens += 1

    def close(self) -> None:
        self.closes += 1

    def read(self):
        return None

    def latest_color_image(self):
        return None

    def set_color_publish(self, enabled: bool) -> None:
        pass


class _FakeSlam:
    def start(self) -> None:
        pass

    def status(self) -> dict:
        return {"lidar_connected": False, "lidar_message": "fake", "running": True}

    def latest_scan(self):
        return None

    def latest_pose(self):
        return None

    def latest_grid_view(self):
        return None


class _FakeUltras:
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self):
        return []

    def read_all(self):
        return []


class _FakeIR:
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read(self):
        return None


class _FakeDrive:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.brake_calls = []

    def set_brake(self, on: bool) -> None:
        self.brake_calls.append(bool(on))

    def ensure_hardware(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_calls += 1


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _settings():
    return AutonomySettings(
        tick_hz=10.0, cruise_speed_pct=15, turn_speed_pct=20,
        forward_clear_mm=600, side_clear_mm=300,
        emergency_stop_mm=200, cliff_min_mm=50,
        turn_duration_ms=600, backoff_duration_ms=400,
    )


def _goto_settings():
    return GotoSettings(
        arrival_radius_mm=200, footprint_radius_mm=200,
        min_passage_width_mm=610,
        cruise_speed_pct=15, turn_speed_pct=16,
        heading_deadband_deg=15.0, lookahead_mm=400,
        replan_period_sec=3.0, stuck_window_sec=5.0,
        stuck_motion_mm=50, tick_hz=8.0, unknown_pixel_cost=1.5,
    )


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ac, "AutonomousPilot", _StubWanderPilot)
    monkeypatch.setattr(ac, "GotoPilot", _StubGotoPilot)
    _StubWanderPilot.instances.clear()
    _StubGotoPilot.instances.clear()

    drive = _FakeDrive()
    ctrl = AutonomyController(
        drive=drive, slam=_FakeSlam(),
        settings=_settings(),
        goto_settings=_goto_settings(),
        ultrasonics=_FakeUltras(), ir=_FakeIR(), depth=_FakeDepth(),
    )
    return ctrl


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_set_goal_while_off_arms_goto_and_enables_autonomy(controller):
    """Operator clicks the map without first toggling autonomy ON.
    The controller should enable autonomy AND start in goto mode.
    """
    result = controller.set_goal(500.0, 0.0)
    assert result["ok"] is True
    assert result["mode"] == ac.MODE_GOTO
    assert controller.is_enabled() is True
    assert controller.current_mode() == ac.MODE_GOTO
    # Goto pilot should have started; wander pilot should NOT.
    assert _StubGotoPilot.instances, "goto pilot was not constructed"
    assert _StubGotoPilot.instances[0].start_calls == [(500.0, 0.0)]
    assert all(not p.started for p in _StubWanderPilot.instances)


def test_clear_goal_after_off_set_goal_disables_autonomy(controller):
    """If goto turned autonomy ON, clear_goal also turns it off."""
    controller.set_goal(500.0, 0.0)
    result = controller.clear_goal()
    assert result["ok"] is True
    assert result["mode"] == ac.MODE_IDLE
    assert controller.is_enabled() is False
    assert controller.current_mode() == ac.MODE_IDLE


def test_set_goal_while_wander_swaps_to_goto(controller):
    """Operator already in wander, then taps the map: wander pilot
    must stop, goto pilot must start, mode = goto.
    """
    controller.set_enabled(True)
    assert controller.current_mode() == ac.MODE_WANDER
    wander_one = _StubWanderPilot.instances[-1]
    assert wander_one.started is True

    result = controller.set_goal(800.0, 200.0)
    assert result["ok"] is True
    assert result["mode"] == ac.MODE_GOTO
    assert controller.current_mode() == ac.MODE_GOTO
    assert wander_one.stopped is True
    assert _StubGotoPilot.instances[-1].start_calls == [(800.0, 200.0)]


def test_clear_goal_after_wander_set_goal_returns_to_wander(controller):
    """If autonomy was already on (wander), clear_goal goes back to
    wander - NOT to off.
    """
    controller.set_enabled(True)
    controller.set_goal(800.0, 200.0)
    result = controller.clear_goal()
    assert result["ok"] is True
    assert result["mode"] == ac.MODE_WANDER
    assert controller.is_enabled() is True
    assert controller.current_mode() == ac.MODE_WANDER
    # Goto pilot stopped, a fresh wander pilot started.
    assert _StubGotoPilot.instances[-1].stop_calls >= 1


def test_second_set_goal_in_goto_updates_in_flight(controller):
    """Two set_goal calls back-to-back in goto mode shouldn't tear
    down the pilot - same instance, just .start(new_goal) again.
    """
    controller.set_goal(500.0, 0.0)
    pilot = _StubGotoPilot.instances[-1]
    controller.set_goal(900.0, -100.0)
    # Same pilot instance should have received both starts.
    assert pilot.start_calls == [(500.0, 0.0), (900.0, -100.0)]
    # Should NOT have constructed a second goto pilot.
    assert len(_StubGotoPilot.instances) == 1


def test_set_goal_rejects_non_numeric_coords(controller):
    result = controller.set_goal("oops", 0.0)
    assert result["ok"] is False
