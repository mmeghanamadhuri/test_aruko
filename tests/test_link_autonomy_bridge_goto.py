"""Tests for ``nina.link_daemon.autonomy_bridge`` goto endpoints.

The HTTP layer in ``nina.link_daemon.api`` is the thinnest possible
wrapper around ``autonomy_bridge.set_goal / clear_goal``, so we
exercise the bridge directly with the relevant globals stubbed
out:

  * `robot_bridge.navigation_for_autonomy()`  -> a fake nav drive.
  * `slam_bridge.get_bridge()`                -> a fake SLAM bridge.
  * `AutonomousPilot` / `GotoPilot`            -> stubs that record
                                                  start/stop calls.
  * Sensor classes (`HCSR04Array`, `GP2Y0E02B`)
                                              -> harmless stubs.
  * `depth_bridge.acquire / release`           -> stubs.

The tests pin the bridge's mode-dispatch contract so the Android
companion never gets a 200 from a code path that didn't actually
arm goto.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from pathlib import Path

from nina.config.settings import load_settings


# ----------------------------------------------------------------------
# Stubs
# ----------------------------------------------------------------------


class _FakeNav:
    def __init__(self) -> None:
        self.stops = 0
        self.estops = 0

    def stop(self) -> None:
        self.stops += 1

    def emergency_stop(self) -> None:
        self.estops += 1

    def set_wheels(self, **kwargs) -> None:
        pass


class _FakeSlamBridge:
    def latest_scan(self):
        return None

    def latest_snapshot(self):
        return None

    def status(self):
        return {"lidar_connected": True, "lidar_message": "fake lidar"}


class _StubWander:
    instances: List["_StubWander"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.started = False
        self.stopped = False
        self._listeners: list = []
        _StubWander.instances.append(self)

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def state(self):
        from nina.navigation.autonomous_pilot import PilotState
        return PilotState(running=self.started and not self.stopped)


class _StubGoto:
    instances: List["_StubGoto"] = []

    def __init__(self, drive, sensors, goto_settings, autonomy_settings) -> None:
        self.starts: list = []
        self.stops = 0
        self._listeners: list = []
        self._running = False
        self._goal = None
        _StubGoto.instances.append(self)

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def start(self, x: float, y: float) -> None:
        self.starts.append((x, y))
        self._goal = (x, y)
        self._running = True

    def stop(self) -> None:
        self.stops += 1
        self._running = False

    def state(self):
        from nina.navigation.goto_pilot import GotoState
        return GotoState(running=self._running, state="planning",
                         goal_mm=self._goal)


class _NoopSensor:
    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self):
        return []

    def read_all(self):
        return []

    def read(self):
        return None


def _settings_blob(tmp_path: Path):
    """Load real NinaSettings from a tmp dir (env vars unaffected) so
    the bridge sees a complete `goto` + `autonomy` + `slam` blob.
    """
    return load_settings(tmp_path)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Reset the bridge module's globals + monkeypatch deps."""
    from nina.link_daemon import autonomy_bridge
    from nina.link_daemon import depth_bridge
    from nina.link_daemon import slam_bridge as slam_mod
    from nina.link_daemon import robot_bridge as robot_mod

    # Drop any state from a previous test.
    autonomy_bridge._enabled = False
    autonomy_bridge._mode = autonomy_bridge.MODE_IDLE
    autonomy_bridge._pilot = None
    autonomy_bridge._goto_pilot = None
    autonomy_bridge._goto_started_us = False
    autonomy_bridge._sensor_bundle = None
    autonomy_bridge._last_pilot = None
    autonomy_bridge._last_goto = None
    autonomy_bridge._ultras = _NoopSensor()
    autonomy_bridge._ir = _NoopSensor()

    _StubWander.instances.clear()
    _StubGoto.instances.clear()

    monkeypatch.setattr(autonomy_bridge, "AutonomousPilot", _StubWander)
    monkeypatch.setattr(autonomy_bridge, "GotoPilot", _StubGoto)

    fake_slam = _FakeSlamBridge()
    monkeypatch.setattr(slam_mod, "ensure_bridge_started", lambda: None)
    monkeypatch.setattr(slam_mod, "get_bridge", lambda: fake_slam)

    fake_nav = _FakeNav()
    monkeypatch.setattr(robot_mod, "navigation_for_autonomy", lambda: fake_nav)
    monkeypatch.setattr(robot_mod, "set_autonomy_blocks_drive", lambda v: None)

    monkeypatch.setattr(depth_bridge, "acquire", lambda owner: (True, "ok"))
    monkeypatch.setattr(depth_bridge, "release", lambda owner: None)
    monkeypatch.setattr(
        autonomy_bridge, "_load_settings_blob",
        lambda: _settings_blob(tmp_path),
    )

    return autonomy_bridge


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_set_goal_while_off_arms_goto_and_enables(bridge):
    result = bridge.set_goal(500.0, 0.0)
    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["mode"] == bridge.MODE_GOTO
    assert bridge._enabled is True
    assert bridge._mode == bridge.MODE_GOTO
    assert _StubGoto.instances[-1].starts == [(500.0, 0.0)]
    assert all(not p.started for p in _StubWander.instances)


def test_clear_goal_after_off_set_goal_disables(bridge):
    bridge.set_goal(500.0, 0.0)
    result = bridge.clear_goal()
    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["mode"] == bridge.MODE_IDLE
    assert bridge._enabled is False


def test_set_enabled_then_set_goal_swaps_to_goto(bridge):
    enable_result = bridge.set_enabled(True)
    assert enable_result["ok"] is True
    assert bridge._mode == bridge.MODE_WANDER
    wander = _StubWander.instances[-1]
    assert wander.started is True

    goal_result = bridge.set_goal(800.0, 200.0)
    assert goal_result["ok"] is True
    assert goal_result["mode"] == bridge.MODE_GOTO
    assert wander.stopped is True
    assert _StubGoto.instances[-1].starts == [(800.0, 200.0)]


def test_clear_goal_after_wander_returns_to_wander(bridge):
    bridge.set_enabled(True)
    bridge.set_goal(800.0, 200.0)
    result = bridge.clear_goal()
    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["mode"] == bridge.MODE_WANDER
    # Wander pilot should have been respawned.
    started_again = [p for p in _StubWander.instances if p.started]
    assert len(started_again) >= 2


def test_status_dict_carries_mode_and_goto(bridge):
    bridge.set_goal(500.0, 0.0)
    st = bridge.status_dict()
    assert st["enabled"] is True
    assert st["mode"] == bridge.MODE_GOTO
    assert st["goto"] is not None
    assert st["goto"]["state"] == "planning"


def test_set_goal_rejects_non_numeric(bridge):
    result = bridge.set_goal("oops", 0.0)
    assert result["ok"] is False


def test_clear_goal_when_idle_is_noop(bridge):
    result = bridge.clear_goal()
    assert result["ok"] is True
    assert "no active goto" in result["message"]
