"""Smoke tests for PerceptionScreen.

The Perception screen is the only place an operator can simultaneously
verify lidar, RGB, and depth feeds. If the screen fails to construct
(missing widget import, broken signal hookup, layout exception) the
operator's only recourse on a sensor problem is the Health screen,
which is much less actionable.

These are deliberately surface-level: we build the screen against a
stub NinaService, then run the on_enter / on_leave / refresh hooks to
prove the wiring path doesn't blow up. We don't try to fake live
camera frames - the FRAME signal handlers are simple QPixmap.scaled
calls already covered by VisionScreen, and re-testing them here would
mostly retest Qt.
"""

from __future__ import annotations

import os
import sys

import pytest

# QT_QPA_PLATFORM=offscreen lets these run on CI / dev hosts that
# don't have a real X display. Set BEFORE importing PyQt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PyQt5 = pytest.importorskip("PyQt5")
from PyQt5.QtWidgets import QApplication  # noqa: E402


# ---------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------


class _StubVision:
    """Minimal VisionWorker stub. Has the signals the screen connects
    to (frame_ready / status_changed) plus refcount surface."""

    def __init__(self) -> None:
        from PyQt5.QtCore import QObject, pyqtSignal
        from PyQt5.QtGui import QImage

        class _Sig(QObject):
            frame_ready = pyqtSignal(QImage)
            status_changed = pyqtSignal(dict)

        self._sig = _Sig()
        self.frame_ready = self._sig.frame_ready
        self.status_changed = self._sig.status_changed
        self.acquires = 0
        self.releases = 0

    def acquire(self) -> None:
        self.acquires += 1

    def release(self) -> None:
        self.releases += 1

    def status(self):
        return {"camera_open": False, "message": "stub"}


class _StubSlam:
    def __init__(self) -> None:
        from PyQt5.QtCore import QObject, pyqtSignal

        class _Sig(QObject):
            snapshot_changed = pyqtSignal(dict)
            status_changed = pyqtSignal(dict)
            pose_changed = pyqtSignal(dict)

        self._sig = _Sig()
        self.snapshot_changed = self._sig.snapshot_changed
        self.status_changed = self._sig.status_changed
        self.pose_changed = self._sig.pose_changed
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def status(self) -> dict:
        return {"lidar_connected": False, "lidar_message": "stub", "running": False}

    def latest_snapshot(self):
        return None


class _StubAutonomy:
    def __init__(self) -> None:
        from PyQt5.QtCore import QObject, pyqtSignal

        class _Sig(QObject):
            enabled_changed = pyqtSignal(bool)
            sensor_health_changed = pyqtSignal(dict)
            pilot_state_changed = pyqtSignal(dict)

        self._sig = _Sig()
        self.enabled_changed = self._sig.enabled_changed
        self.sensor_health_changed = self._sig.sensor_health_changed
        self.pilot_state_changed = self._sig.pilot_state_changed

        self.enabled = False
        self.acquire_calls = 0
        self.release_calls = 0
        self.viz_calls: list = []

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self.enabled_changed.emit(self.enabled)

    def acquire_depth(self):
        self.acquire_calls += 1
        return False, "stub: no D435 in test"

    def release_depth(self) -> None:
        self.release_calls += 1

    def set_depth_visualization_enabled(self, enabled: bool) -> None:
        self.viz_calls.append(bool(enabled))

    def latest_depth_visualization(self):
        return None


class _StubService:
    def __init__(self) -> None:
        self.vision = _StubVision()
        self.slam = _StubSlam()
        self.autonomy = _StubAutonomy()


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    """Single shared QApplication for the test module - PyQt refuses
    a second instance per process."""
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst


@pytest.fixture
def screen(app):
    from sirena_ui.screens.perception_screen import PerceptionScreen

    svc = _StubService()
    s = PerceptionScreen(svc)
    yield s, svc
    s.deleteLater()


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_perception_screen_builds(screen) -> None:
    """The screen constructs without raising. This is the regression
    we care about: an unimported widget, a broken signal connection,
    or a layout error here would crash the Nina app on the first
    nav-click to Perception."""
    s, _ = screen
    # Three sensor cards plus header + footer. We don't assert on
    # the exact widget tree (too fragile) - just that the screen
    # has children, i.e. the layout actually populated.
    assert len(s.children()) > 0


def test_on_enter_acquires_camera_and_depth(screen) -> None:
    """on_enter must acquire BOTH the vision worker AND the depth
    sensor exactly once. If we don't acquire depth, the Perception
    panel would never get a frame on a bot where autonomy is OFF."""
    s, svc = screen
    s.on_enter()
    assert svc.vision.acquires == 1, (
        "on_enter didn't acquire the vision worker - the RGB pane "
        "would stay in placeholder"
    )
    assert svc.autonomy.acquire_calls == 1, (
        "on_enter didn't acquire depth via autonomy - the depth "
        "pane would stay in placeholder even with a D435 plugged in"
    )
    assert svc.slam.start_calls == 1
    # Visualization toggle was flipped on.
    assert True in svc.autonomy.viz_calls
    s.on_leave()


def test_on_leave_releases_resources(screen) -> None:
    """on_leave must release BOTH camera and depth. Leaking either
    would burn CPU / power on an unwatched feed."""
    s, svc = screen
    s.on_enter()
    s.on_leave()
    assert svc.vision.releases == 1
    assert svc.autonomy.release_calls == 1
    # Visualization toggle was flipped off as part of leave.
    assert svc.autonomy.viz_calls[-1] is False


def test_double_leave_is_safe(screen) -> None:
    """Qt's nav can fire on_leave more than once during stack
    teardown. The screen must not double-release (which would
    underflow some other holder's refcount)."""
    s, svc = screen
    s.on_enter()
    s.on_leave()
    s.on_leave()  # double - must not double-release
    assert svc.vision.releases == 1
    assert svc.autonomy.release_calls == 1


def test_autonomy_toggle_flows_through(screen) -> None:
    """Clicking the autonomy button on the Perception screen must
    call AutonomyController.set_enabled(True) - the button isn't
    cosmetic, it's a real handoff to the autonomy stack."""
    s, svc = screen
    s.on_enter()
    s._autonomy_btn.setChecked(True)  # programmatic click
    assert svc.autonomy.enabled is True
    s._autonomy_btn.setChecked(False)
    assert svc.autonomy.enabled is False
    s.on_leave()


def test_screen_renders_when_no_depth_payload(screen) -> None:
    """The depth poll runs on a QTimer - if the underlying camera
    isn't open, latest_depth_visualization() returns None and the
    poll handler must NOT crash. Most operators will boot Nina
    once without a D435 connected."""
    s, _ = screen
    # No on_enter (timer not started) - just call the handler
    # directly with the screen in its initial state.
    s._poll_depth()  # must not raise
    # And once more with the screen entered (camera "open" but
    # stub returns None payload).
    s.on_enter()
    s._poll_depth()
    s.on_leave()
