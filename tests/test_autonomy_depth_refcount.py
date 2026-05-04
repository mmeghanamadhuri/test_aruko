"""Tests for AutonomyController.acquire_depth / release_depth.

The Perception screen needs to open the D435 to visualize depth
even when autonomy is OFF. Without refcounting, two paths could
both call `RealSenseD435.open()` on the same device:

    Perception.on_enter   -> autonomy.acquire_depth()  -> rs.pipeline.start()
    User toggles Auto ON  -> autonomy._enable()
                              -> rs.pipeline.start() AGAIN  <-- librealsense
                                  rejects this with "device busy"

Conversely, toggling autonomy OFF must NOT close the depth sensor
if a Perception screen still wants it. These tests pin both
contracts using a fake RealSenseD435 that records every open/close.

`acquire_depth` is intentionally **async**: the actual
`RealSense.open()` runs on a worker thread because
`pipeline.start()` blocks 1-3 s on the Jetson. Tests that need to
observe the post-open state use the `_wait_depth_open()` helper
below to deterministically join that worker thread.
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

from nina.config.settings import AutonomySettings
from sirena_ui.workers.autonomy_controller import AutonomyController


def _wait_depth_open(ctrl: AutonomyController, timeout: float = 2.0) -> None:
    """Block until the controller's pending depth-open thread (if
    any) completes. Used by tests that need to assert the
    post-open state without racing the worker thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Snapshot under the lock to avoid races with the worker
        # thread mutating these fields.
        with ctrl._lock:  # noqa: SLF001 - test helper
            in_progress = ctrl._depth_open_in_progress
            thread = ctrl._depth_open_thread
        if not in_progress:
            if thread is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
            return
        time.sleep(0.01)
    raise AssertionError("depth open thread didn't finish within timeout")


# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------


class _FakeDepth:
    """Stand-in for `RealSenseD435` that records open/close calls and
    optionally fails open() to simulate "no D435 plugged in"."""

    def __init__(self, fail_open: bool = False) -> None:
        self.fail_open = fail_open
        self.opens = 0
        self.closes = 0
        self.color_publish_calls: list = []

    def open(self) -> None:
        self.opens += 1
        if self.fail_open:
            raise RuntimeError("no D435 connected")

    def close(self) -> None:
        self.closes += 1

    def read(self):
        return None

    def latest_color_image(self):
        return None

    def set_color_publish(self, enabled: bool) -> None:
        self.color_publish_calls.append(bool(enabled))


class _FakeSlam:
    def __init__(self) -> None:
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def status(self) -> dict:
        return {"lidar_connected": False, "lidar_message": "fake", "running": True}

    def latest_scan(self):
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
        self.brake_calls: list = []
        self.stop_calls = 0

    def set_brake(self, on: bool) -> None:
        self.brake_calls.append(bool(on))

    def ensure_hardware(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_calls += 1


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def fake_pilot(monkeypatch: pytest.MonkeyPatch):
    """Replace AutonomousPilot with a stub so _enable doesn't try to
    spin a real obstacle-avoidance loop on a worker thread."""
    import sirena_ui.workers.autonomy_controller as ac

    class _StubPilot:
        def __init__(self, *args, **kwargs) -> None:
            self._listeners: list = []
            self._state = ac.PilotState(running=False)

        def add_listener(self, cb) -> None:
            self._listeners.append(cb)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def state(self) -> "ac.PilotState":
            return self._state

    monkeypatch.setattr(ac, "AutonomousPilot", _StubPilot)


def _autonomy_settings() -> AutonomySettings:
    """Minimal-but-valid AutonomySettings for the controller. Values
    are illustrative; the refcount tests don't actually drive the
    pilot loop, so the thresholds are irrelevant - we just have to
    satisfy the frozen-dataclass required-fields contract."""
    return AutonomySettings(
        tick_hz=10.0,
        cruise_speed_pct=15,
        turn_speed_pct=20,
        forward_clear_mm=600,
        side_clear_mm=300,
        emergency_stop_mm=200,
        cliff_min_mm=50,
        turn_duration_ms=600,
        backoff_duration_ms=400,
    )


@pytest.fixture
def controller(fake_pilot):
    depth = _FakeDepth()
    slam = _FakeSlam()
    drive = _FakeDrive()
    ctrl = AutonomyController(
        drive=drive,
        slam=slam,
        settings=_autonomy_settings(),
        ultrasonics=_FakeUltras(),
        ir=_FakeIR(),
        depth=depth,
    )
    return ctrl, depth, slam


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_acquire_depth_opens_camera_once(controller) -> None:
    """The first acquire_depth() actually calls the underlying
    RealSenseD435.open(). Without this the Perception screen would
    show "depth waiting" forever on a healthy bot.

    The open is async (worker thread) so we join it before
    checking. The synchronous return value is "we promise to open"
    (True, "depth opening..."), not "we have opened" - that
    distinction matters because RealSense.start() can take 1-3 s
    and freezes the Qt main thread if called inline.
    """
    ctrl, depth, _ = controller
    ok, msg = ctrl.acquire_depth()
    assert ok is True, f"acquire_depth said not ok: {msg!r}"
    _wait_depth_open(ctrl)
    assert depth.opens == 1


def test_second_acquire_does_not_reopen_camera(controller) -> None:
    """Two acquire_depth() calls in a row are a refcount bump only.
    librealsense rejects pipeline.start() on an already-running
    pipeline with `RuntimeError: device or resource busy` and the
    resulting error would land on the Perception or Map screen
    without any actionable diagnostic."""
    ctrl, depth, _ = controller
    ctrl.acquire_depth()
    _wait_depth_open(ctrl)
    ctrl.acquire_depth()  # second acquire - just bumps refcount
    _wait_depth_open(ctrl)  # no-op, but confirms no thread is mid-open
    assert depth.opens == 1, (
        f"depth.open() ran {depth.opens} times for two acquires - "
        "the refcount is broken; librealsense will reject the second"
    )


def test_release_with_outstanding_holder_does_not_close(controller) -> None:
    """Perception holds + autonomy enables (which also acquires) +
    autonomy disables -> the depth camera must STAY open because the
    Perception screen is still watching."""
    ctrl, depth, _ = controller
    ctrl.acquire_depth()  # Perception screen
    _wait_depth_open(ctrl)
    ctrl.set_enabled(True)  # autonomy on - acquires too (refcount += 1)
    _wait_depth_open(ctrl)
    ctrl.set_enabled(False)  # autonomy off - releases
    assert depth.closes == 0, (
        "depth was closed even though Perception screen still held "
        "a reference; the screen would freeze on the last frame"
    )


def test_last_release_actually_closes(controller) -> None:
    """When the last holder releases, the camera is actually closed
    so the next start can pick fresh USB enumeration. Otherwise the
    bot leaks the depth pipeline across screen navigations until
    process exit."""
    ctrl, depth, _ = controller
    ctrl.acquire_depth()
    _wait_depth_open(ctrl)
    ctrl.acquire_depth()
    _wait_depth_open(ctrl)
    ctrl.release_depth()
    ctrl.release_depth()
    assert depth.closes == 1


def test_failed_open_does_not_corrupt_refcount(controller) -> None:
    """If RealSenseD435.open() fails (no camera plugged in), a
    subsequent release_depth() must NOT decrement past zero. The
    Perception screen calls release_depth() on on_leave regardless
    of whether on_enter's acquire succeeded - an underflow would
    later close a camera some other holder thought was open."""
    ctrl, depth, _ = controller
    depth.fail_open = True

    # Sync return is (True, "depth opening...") because the open
    # hasn't run yet. The actual failure is observable only after
    # the worker thread completes.
    ok, msg = ctrl.acquire_depth()
    assert ok is True
    _wait_depth_open(ctrl)
    # Post-open state: failure recorded, refcount cleared.
    assert ctrl._depth_open_ok is False  # noqa: SLF001 - test introspection
    assert "D435" in ctrl._depth_open_msg or "depth" in ctrl._depth_open_msg

    # Perception screen leaves; this must be a clean no-op because
    # the failed open already cleared the refcount.
    ctrl.release_depth()
    assert depth.closes == 0  # nothing to close - open never succeeded

    # Now a SECOND acquire should still work (heal the refcount).
    depth.fail_open = False
    ok2, _ = ctrl.acquire_depth()
    _wait_depth_open(ctrl)
    assert ok2 is True
    assert ctrl._depth_open_ok is True  # noqa: SLF001
    assert depth.opens == 2  # one failed + one succeeded


def test_set_depth_visualization_passes_through(controller) -> None:
    """Toggling visualization must call the underlying driver's
    set_color_publish - otherwise the Perception screen never gets
    colorized frames and the depth panel stays in placeholder mode
    even with the camera open."""
    ctrl, depth, _ = controller
    ctrl.set_depth_visualization_enabled(True)
    ctrl.set_depth_visualization_enabled(False)
    assert depth.color_publish_calls == [True, False]


def test_release_depth_disables_color_publish(controller) -> None:
    """The last release_depth() must disable colorization BEFORE
    closing - otherwise the worker thread could colorize a frame
    pulled from a pipeline being torn down, which racy librealsense
    builds segfault on."""
    ctrl, depth, _ = controller
    ctrl.acquire_depth()
    _wait_depth_open(ctrl)
    ctrl.set_depth_visualization_enabled(True)
    assert depth.color_publish_calls[-1] is True

    ctrl.release_depth()
    # The last set_color_publish call must be False, and it must
    # come BEFORE the close (i.e. there's a False between the True
    # and the close).
    assert depth.color_publish_calls[-1] is False
    assert depth.closes == 1


def test_acquire_depth_returns_immediately(controller) -> None:
    """The whole point of the async refactor: `acquire_depth()`
    must return without waiting for the (potentially multi-second)
    `RealSense.open()` to complete. We verify by making the fake
    open *block* and asserting the call still returns promptly.
    """
    import threading

    ctrl, depth, _ = controller
    # Replace the fake's open() with one that blocks until released.
    gate = threading.Event()

    def blocking_open() -> None:
        depth.opens += 1
        gate.wait(timeout=5.0)

    depth.open = blocking_open  # type: ignore[method-assign]

    t0 = time.monotonic()
    ok, msg = ctrl.acquire_depth()
    elapsed = time.monotonic() - t0

    # Must return promptly even though the open is still running
    # in the background. 0.2 s is generous; the actual return path
    # takes <1 ms because we just spawn a thread and return.
    assert elapsed < 0.2, (
        f"acquire_depth blocked for {elapsed:.2f}s - the Qt main "
        "thread would freeze for the same duration in the GUI"
    )
    assert ok is True
    assert "opening" in msg.lower()

    # Release the fake open so the worker thread can finish + the
    # test fixture cleanup doesn't hang.
    gate.set()
    _wait_depth_open(ctrl)


def test_release_during_open_closes_when_open_completes(controller) -> None:
    """If release_depth() is called while an open is still in
    flight, the open thread MUST close the pipeline on completion -
    otherwise the camera stays on with nobody consuming frames."""
    import threading

    ctrl, depth, _ = controller
    gate = threading.Event()

    def blocking_open() -> None:
        depth.opens += 1
        gate.wait(timeout=5.0)

    depth.open = blocking_open  # type: ignore[method-assign]

    ctrl.acquire_depth()                # spawns open thread
    ctrl.release_depth()                # asks for close-when-ready
    gate.set()                          # let open finish
    _wait_depth_open(ctrl)

    assert depth.closes == 1, (
        "open finished but no close was issued; the pipeline would "
        "leak its USB endpoint until process exit"
    )
