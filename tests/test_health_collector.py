"""Smoke tests for health_collector.collect.

The point of this test file isn't to exhaustively cover every status
permutation - it's to PIN the "no more 'not yet integrated' lies"
contract that the rewrite established. Specifically:

- A NinaService whose lazy workers were never touched must produce
  PENDING rows (not ERROR or OK or fake-OK), with messages that tell
  the operator HOW to bring the subsystem up.
- A NinaService whose vision/slam/autonomy/drive workers ARE up and
  reporting healthy must produce OK rows.
- The collector must NEVER lazy-instantiate a worker - that would
  open hardware (camera, RPLIDAR, RealSense) just to ask "are you
  open?" which is the opposite of what a passive health screen does.

The previous implementation reported every sensor as PENDING with the
message "not yet integrated" forever, even after the actual perception
stack landed. This test would catch that regression class.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

from sirena_ui.workers.health_collector import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_WARN,
    collect,
)


# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------


class _FakeBusLock:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@dataclass
class _FakeSettings:
    serial_port: str = "/tmp/no-such-port"
    baudrate: int = 1000000


class _FakeDxl:
    """Pretends the bus is uninitialised so the bus row goes PENDING.
    A more elaborate variant would override `_is_initialized=True` and
    return a fake health object; not needed for the contract test."""

    _is_initialized = False

    def run_health_check(self):  # pragma: no cover - never called when uninit
        raise AssertionError("should not be called when bus uninitialised")


class _FakeService:
    """Mimics the slice of NinaService the health collector actually
    reads. Underscore-prefixed worker fields are settable so each test
    can dial in the exact 'has this worker been opened yet?' state."""

    def __init__(self) -> None:
        self.bus_lock = _FakeBusLock()
        self.dxl = _FakeDxl()
        self.settings = _FakeSettings()
        self._vision = None
        self._slam = None
        self._autonomy = None
        self._drive = None


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def _row_by_key(rows, key):
    matches = [r for r in rows if r.key == key]
    assert matches, f"no row with key={key!r}; got {[r.key for r in rows]}"
    return matches[0]


def test_untouched_service_reports_pending_for_lazy_subsystems() -> None:
    """A fresh NinaService has no vision/slam/autonomy/drive workers
    constructed yet. The collector must report all four as PENDING
    with HELPFUL detail messages (so the operator knows how to bring
    each one up), NOT as ERROR (which would falsely indicate broken
    hardware) or as OK (the old bug)."""
    service = _FakeService()

    rows = collect(service)  # type: ignore[arg-type]

    for key in ("camera", "lidar", "ir", "ultra", "depth", "bldc"):
        row = _row_by_key(rows, key)
        assert row.status == STATUS_PENDING, (
            f"{key} should be PENDING when its worker hasn't been "
            f"opened yet, got status={row.status!r} detail={row.detail!r}"
        )
        # The detail message must guide the operator to action,
        # not report fake "not yet integrated" stubs.
        assert "not yet integrated" not in row.detail.lower(), (
            f"{key}: detail message contains stale 'not yet "
            f"integrated' wording: {row.detail!r}"
        )
        assert row.detail, f"{key}: empty detail message"


def test_collector_never_lazy_instantiates_workers() -> None:
    """Critical invariant: collect() reads underscore-prefixed
    backing fields directly so it doesn't trigger NinaService.vision /
    .slam / .autonomy / .drive lazy construction (which would open
    USB camera, RPLIDAR, RealSense, GPIO/serial). If somebody adds a
    `service.vision` access in the future, this test catches it."""

    accesses: list = []

    class _Tripwire(_FakeService):
        # Override the public properties so any access via
        # service.vision / .slam / etc. raises immediately. The
        # collector must read service._vision (etc.) instead.
        @property
        def vision(self):
            accesses.append("vision")
            raise AssertionError(
                "collect() must not read service.vision (would lazy-open camera)"
            )

        @property
        def slam(self):
            accesses.append("slam")
            raise AssertionError(
                "collect() must not read service.slam (would lazy-open RPLIDAR)"
            )

        @property
        def autonomy(self):
            accesses.append("autonomy")
            raise AssertionError(
                "collect() must not read service.autonomy (would open all sensors)"
            )

        @property
        def drive(self):
            accesses.append("drive")
            raise AssertionError(
                "collect() must not read service.drive (would init nav backend)"
            )

    rows = collect(_Tripwire())  # type: ignore[arg-type]
    assert accesses == [], (
        f"collect() lazily accessed {accesses}; must use _vision/_slam/"
        f"_autonomy/_drive private fields instead"
    )
    # And we still got back a useful set of rows.
    assert len(rows) >= 10, f"expected the full health table, got {len(rows)} rows"


def test_open_subsystems_report_ok() -> None:
    """When vision/slam/autonomy/drive workers ARE constructed and
    reporting healthy, the collector promotes them out of PENDING
    into OK (or WARN for partial states). This is the 'autonomy is
    actually running, show green' path."""

    fake_vision = SimpleNamespace(
        status=lambda: SimpleNamespace(
            camera_open=True,
            face_ready=True,
            object_ready=True,
            message="Camera ready",
        )
    )
    fake_slam = SimpleNamespace(
        status=lambda: {
            "lidar_connected": True,
            "lidar_message": "RPLIDAR A1 @ /dev/ttyUSB0",
            "running": True,
        }
    )
    fake_autonomy = SimpleNamespace(
        state=lambda: {
            "enabled": True,
            "health": {
                "lidar": (True, "RPLIDAR A1"),
                "ir": (True, "GP2Y0E02B @ 0x40"),
                "depth": (True, "D435 640x480@15fps"),
                "ultrasonic": [
                    ("FL", True, "ok"),
                    ("FR", True, "ok"),
                    ("RL", True, "ok"),
                    ("RR", True, "ok"),
                ],
            },
        }
    )
    fake_drive = SimpleNamespace(
        state=lambda: {
            "connected": True,
            "speed_pct": 18,
            "direction": "idle",
            "brake": True,
            "driver_message": "remote bridge ready",
        }
    )

    service = _FakeService()
    service._vision = fake_vision     # type: ignore[assignment]
    service._slam = fake_slam         # type: ignore[assignment]
    service._autonomy = fake_autonomy # type: ignore[assignment]
    service._drive = fake_drive       # type: ignore[assignment]

    rows = collect(service)  # type: ignore[arg-type]

    assert _row_by_key(rows, "camera").status == STATUS_OK
    assert _row_by_key(rows, "lidar").status == STATUS_OK
    assert _row_by_key(rows, "ir").status == STATUS_OK
    assert _row_by_key(rows, "depth").status == STATUS_OK
    assert _row_by_key(rows, "ultra").status == STATUS_OK
    assert _row_by_key(rows, "bldc").status == STATUS_OK


def test_partial_ultrasonic_ring_reports_warn() -> None:
    """One sensor on a 4-ultrasonic ring is degraded but the bot can
    still navigate from the other three - reflect that as WARN, not
    ERROR (operator must see the difference between 'partial cover'
    and 'no cover at all')."""
    service = _FakeService()
    service._autonomy = SimpleNamespace(  # type: ignore[assignment]
        state=lambda: {
            "health": {
                "lidar": (False, ""),
                "ir": (False, ""),
                "depth": (False, ""),
                "ultrasonic": [
                    ("FL", True, "ok"),
                    ("FR", True, "ok"),
                    ("RL", False, "no echo"),
                    ("RR", True, "ok"),
                ],
            }
        }
    )

    row = _row_by_key(collect(service), "ultra")  # type: ignore[arg-type]
    assert row.status == STATUS_WARN
    assert "3/4" in row.detail


def test_serial_port_row_errors_when_device_missing() -> None:
    """The FTDI row must go ERROR (not PENDING / not OK) when the
    configured serial device file doesn't exist - this is the
    'unplug the FTDI cable' diagnostic the operator relies on."""
    service = _FakeService()
    service.settings = _FakeSettings(serial_port="/no/such/device/exists")  # type: ignore[assignment]

    row = _row_by_key(collect(service), "ftdi")  # type: ignore[arg-type]
    assert row.status == STATUS_ERROR
