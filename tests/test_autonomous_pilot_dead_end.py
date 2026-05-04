"""Dead-end backoff: wander pilot reverses when boxed in or stalled."""

from __future__ import annotations

from typing import Optional

import pytest

import nina.navigation.autonomous_pilot as ap_module
from nina.config.settings import AutonomySettings
from nina.navigation.autonomous_pilot import AutonomousPilot, SensorBundle
from nina.sensors.types import DepthFrame


def _settings(**over: object) -> AutonomySettings:
    base: dict = dict(
        tick_hz=8.0,
        cruise_speed_pct=15,
        turn_speed_pct=16,
        forward_clear_mm=1200,
        side_clear_mm=450,
        emergency_stop_mm=850,
        cliff_min_mm=60,
        turn_duration_ms=350,
        backoff_duration_ms=0,
        fwd_blocked_backup_sec=2.5,
    )
    base.update(over)
    return AutonomySettings(**base)


class _FakeDrive:
    def __init__(self) -> None:
        self.calls: list = []

    def set_brake(self, on: bool) -> None:
        pass

    def stop(self) -> None:
        self.calls.append("stop")

    def drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        self.calls.append((left_dir, right_dir))


def _depth(
    fwd: int,
    left: Optional[int] = None,
    right: Optional[int] = None,
) -> DepthFrame:
    return DepthFrame(
        forward_min_mm=fwd,
        forward_avg_mm=fwd,
        left_min_mm=left,
        right_min_mm=right,
        timestamp_s=0.0,
        width=640,
        height=480,
    )


def test_both_sides_tight_triggers_reverse_immediately() -> None:
    drv = _FakeDrive()
    pilot = AutonomousPilot(
        drv,
        SensorBundle(depth=lambda: _depth(1000, 200, 200)),
        _settings(),
    )
    pilot._tick()
    assert ("back", "back") in drv.calls


def test_forward_blocked_for_backup_sec_triggers_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(ap_module.time, "monotonic", lambda: clock["t"])
    drv = _FakeDrive()
    pilot = AutonomousPilot(
        drv,
        SensorBundle(depth=lambda: _depth(1000, 2500, 2500)),
        _settings(),
    )
    pilot._tick()
    assert ("back", "back") not in drv.calls

    clock["t"] = 3.0
    pilot._tick()
    assert ("back", "back") in drv.calls


def test_backup_sec_zero_skips_timeout_but_keeps_both_tight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"t": 100.0}
    monkeypatch.setattr(ap_module.time, "monotonic", lambda: clock["t"])
    drv = _FakeDrive()
    pilot = AutonomousPilot(
        drv,
        SensorBundle(depth=lambda: _depth(1000, 2500, 2500)),
        _settings(fwd_blocked_backup_sec=0.0),
    )
    pilot._tick()
    assert ("back", "back") not in drv.calls

    drv2 = _FakeDrive()
    pilot2 = AutonomousPilot(
        drv2,
        SensorBundle(depth=lambda: _depth(1000, 200, 200)),
        _settings(fwd_blocked_backup_sec=0.0),
    )
    pilot2._tick()
    assert ("back", "back") in drv2.calls
