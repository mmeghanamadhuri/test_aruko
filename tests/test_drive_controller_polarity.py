"""
Tests for the runtime wheel-polarity feature on DriveController.

These cover the contract the Drive screen relies on: when the operator
clicks "Flip L" / "Flip R", the toggle is

  1. immediately propagated to the underlying nav manager (so the very
     next SET / set_wheels call honours it),
  2. persisted to ~/.config/sirena/drive_polarity.json (XDG-respecting),
  3. re-applied on the next DriveController boot,
  4. and overrideable by env vars only when no persisted file exists.

The DriveController constructor spins up two daemon threads (worker +
heartbeat). Each test shuts the controller down at the end via
shutdown(). We use a pytest fixture to point XDG_CONFIG_HOME at a
tmp_path so persistence writes don't escape the test sandbox, and we
inject a thin FakeNav in lieu of a real NavigationManager so we can
read back what set_invert_left/right calls landed.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import pytest


# Skip the whole module gracefully on hosts without PyQt5 (CI without
# a Qt build, etc.). Importing PyQt5.QtCore alone is enough to confirm
# the binding is usable.
pytest.importorskip("PyQt5.QtCore")


@pytest.fixture
def isolate_polarity_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point XDG_CONFIG_HOME at a tmp dir so the persisted JSON file
    lives under tmp_path/sirena/drive_polarity.json. Each test gets a
    fresh dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("NINA_NAV_INVERT_LEFT", raising=False)
    monkeypatch.delenv("NINA_NAV_INVERT_RIGHT", raising=False)
    return tmp_path / "sirena" / "drive_polarity.json"


class FakeNav:
    """Minimal nav-manager stand-in for DriveController.

    Implements the subset of the NavigationManager surface that the
    controller touches during init + polarity propagation. Records
    every set_invert_* call so tests can assert on the final state
    AND on the call sequence (e.g. that polarity is applied BEFORE
    engage_brake on init)."""

    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"

    def __init__(self) -> None:
        self.calls: list = []
        self.invert_left: bool = False
        self.invert_right: bool = False
        self.brake_engaged = False

    def initialize(self) -> None:
        self.calls.append(("initialize",))

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))

    def engage_brake(self) -> None:
        self.calls.append(("engage_brake",))
        self.brake_engaged = True

    def release_brake(self) -> None:
        self.calls.append(("release_brake",))
        self.brake_engaged = False

    def set_invert_left(self, on: bool) -> None:
        self.calls.append(("set_invert_left", bool(on)))
        self.invert_left = bool(on)

    def set_invert_right(self, on: bool) -> None:
        self.calls.append(("set_invert_right", bool(on)))
        self.invert_right = bool(on)

    def stop(self) -> None:
        self.calls.append(("stop",))

    def emergency_stop(self) -> None:
        self.calls.append(("emergency_stop",))

    def drive_continuous(self, **kwargs) -> None:
        self.calls.append(("drive_continuous", kwargs))

    def set_wheels(self, **kwargs) -> None:
        self.calls.append(("set_wheels", kwargs))


def _make_controller(nav: FakeNav, default_speed: int = 15):
    """Spawn a DriveController with the FakeNav injected. Importing
    inside the helper keeps the (slow) PyQt5 import out of the test
    module top-level so collection stays fast on hosts without it."""
    from sirena_ui.workers.drive_controller import DriveController

    return DriveController(nav_manager=nav, default_speed_percent=default_speed)


def _wait_for(predicate, timeout: float = 1.0, poll: float = 0.01) -> bool:
    """Spin until predicate() is truthy or `timeout` seconds elapse.
    Returns True iff predicate was satisfied within the budget."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


# ---------------------------------------------------------------------
# Initial polarity resolution: env vars / persisted JSON / defaults
# ---------------------------------------------------------------------


def test_initial_polarity_defaults_to_false_when_nothing_set(
    isolate_polarity_dir: Path,
) -> None:
    """No persisted JSON, no env vars - both flips OFF."""
    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        snap = ctrl.state()
        assert snap["invert_left"] is False
        assert snap["invert_right"] is False
    finally:
        ctrl.shutdown()


def test_initial_polarity_reads_env_vars(
    isolate_polarity_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no JSON file exists, NINA_NAV_INVERT_LEFT/RIGHT env vars
    seed the boot polarity."""
    monkeypatch.setenv("NINA_NAV_INVERT_LEFT", "1")
    monkeypatch.setenv("NINA_NAV_INVERT_RIGHT", "0")

    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        snap = ctrl.state()
        assert snap["invert_left"] is True
        assert snap["invert_right"] is False
    finally:
        ctrl.shutdown()


def test_initial_polarity_json_wins_over_env(
    isolate_polarity_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisted JSON beats env vars - matches the docstring contract
    and lets the GUI's choice survive a kiosk-service restart that
    might still ship the older env var."""
    isolate_polarity_dir.parent.mkdir(parents=True, exist_ok=True)
    isolate_polarity_dir.write_text(
        json.dumps({"invert_left": False, "invert_right": True})
    )
    monkeypatch.setenv("NINA_NAV_INVERT_LEFT", "1")
    monkeypatch.setenv("NINA_NAV_INVERT_RIGHT", "0")

    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        snap = ctrl.state()
        assert snap["invert_left"] is False
        assert snap["invert_right"] is True
    finally:
        ctrl.shutdown()


def test_corrupt_polarity_json_falls_back_to_env(
    isolate_polarity_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupted file must NOT crash the GUI - we log + fall back."""
    isolate_polarity_dir.parent.mkdir(parents=True, exist_ok=True)
    isolate_polarity_dir.write_text("{not-json")
    monkeypatch.setenv("NINA_NAV_INVERT_LEFT", "1")

    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        snap = ctrl.state()
        assert snap["invert_left"] is True
        assert snap["invert_right"] is False
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------
# Polarity push to the nav manager
# ---------------------------------------------------------------------


def test_polarity_pushed_to_nav_during_init(
    isolate_polarity_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot polarity must reach the nav manager BEFORE engage_brake,
    so the very first SET issued from the GUI honours it."""
    monkeypatch.setenv("NINA_NAV_INVERT_LEFT", "1")
    monkeypatch.setenv("NINA_NAV_INVERT_RIGHT", "0")

    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        ctrl.ensure_hardware()
        assert _wait_for(lambda: nav.brake_engaged), \
            "init never reached engage_brake"

        ops = [c[0] for c in nav.calls]
        # invert_left must be applied between initialize and engage_brake.
        init_idx = ops.index("initialize")
        brake_idx = ops.index("engage_brake")
        invert_left_idx = ops.index("set_invert_left")
        assert init_idx < invert_left_idx < brake_idx

        assert nav.invert_left is True
        assert nav.invert_right is False
    finally:
        ctrl.shutdown()


def test_set_invert_left_propagates_and_persists(
    isolate_polarity_dir: Path,
) -> None:
    """Operator clicks Flip L: state updates immediately, the nav
    manager gets the call (eventually, via the worker queue), and the
    JSON file lands on disk with the new value."""
    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        ctrl.ensure_hardware()
        assert _wait_for(lambda: nav.brake_engaged)

        ctrl.set_invert_left(True)

        assert ctrl.state()["invert_left"] is True
        assert _wait_for(lambda: nav.invert_left is True), \
            "nav manager never saw set_invert_left(True)"

        # Persistence: the file must exist and reflect the new state.
        assert isolate_polarity_dir.exists()
        data = json.loads(isolate_polarity_dir.read_text())
        assert data == {"invert_left": True, "invert_right": False}
    finally:
        ctrl.shutdown()


def test_set_invert_right_propagates_and_persists(
    isolate_polarity_dir: Path,
) -> None:
    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        ctrl.ensure_hardware()
        assert _wait_for(lambda: nav.brake_engaged)

        ctrl.set_invert_right(True)

        assert ctrl.state()["invert_right"] is True
        assert _wait_for(lambda: nav.invert_right is True)
        data = json.loads(isolate_polarity_dir.read_text())
        assert data == {"invert_left": False, "invert_right": True}
    finally:
        ctrl.shutdown()


def test_set_invert_idempotent_does_not_re_persist(
    isolate_polarity_dir: Path,
) -> None:
    """Calling set_invert_left(True) twice should only persist once -
    we early-return when state is unchanged. Verified by deleting the
    file between calls and confirming the second call doesn't recreate
    it."""
    nav = FakeNav()
    ctrl = _make_controller(nav)
    try:
        ctrl.set_invert_left(True)
        assert isolate_polarity_dir.exists()
        isolate_polarity_dir.unlink()

        ctrl.set_invert_left(True)  # no-op: state already True
        assert not isolate_polarity_dir.exists()
    finally:
        ctrl.shutdown()


def test_polarity_round_trip_across_controller_restarts(
    isolate_polarity_dir: Path,
) -> None:
    """Flip a wheel in controller #1, tear it down, start controller #2
    with a fresh FakeNav: the new nav must see the flipped polarity
    pushed during init."""
    nav1 = FakeNav()
    ctrl1 = _make_controller(nav1)
    try:
        ctrl1.set_invert_left(True)
        ctrl1.set_invert_right(True)
        assert _wait_for(
            lambda: isolate_polarity_dir.exists()
            and json.loads(isolate_polarity_dir.read_text()).get("invert_left") is True
        )
    finally:
        ctrl1.shutdown()

    nav2 = FakeNav()
    ctrl2 = _make_controller(nav2)
    try:
        snap = ctrl2.state()
        assert snap["invert_left"] is True
        assert snap["invert_right"] is True

        ctrl2.ensure_hardware()
        assert _wait_for(lambda: nav2.invert_left is True and nav2.invert_right is True)
    finally:
        ctrl2.shutdown()


# ---------------------------------------------------------------------
# Backwards compat: a nav backend without set_invert_* doesn't crash
# ---------------------------------------------------------------------


class FakeNavNoInvert:
    """Older NavigationManager-shaped object that doesn't expose the
    new set_invert_left/right methods. DriveController must tolerate
    this so we can keep test/CI stubs working AND so a partial
    deployment (new app, old nav lib) doesn't brick the GUI."""

    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"

    def __init__(self) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True

    def shutdown(self) -> None:
        pass

    def engage_brake(self) -> None:
        pass


def test_polarity_apply_on_nav_without_setters_is_silent(
    isolate_polarity_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No setters on the nav -> _apply_polarity_to_nav silently no-ops
    (we logged a warning at most). The Drive screen is still usable
    and the polarity state still persists."""
    monkeypatch.setenv("NINA_NAV_INVERT_LEFT", "1")

    nav = FakeNavNoInvert()
    ctrl = _make_controller(nav)
    try:
        ctrl.ensure_hardware()
        assert _wait_for(lambda: nav.initialized)

        ctrl.set_invert_left(False)
        # No assertion on nav (it has no setter). Just confirm we didn't
        # crash and the file landed.
        assert _wait_for(
            lambda: isolate_polarity_dir.exists()
            and json.loads(isolate_polarity_dir.read_text())["invert_left"] is False
        )
    finally:
        ctrl.shutdown()


# ---------------------------------------------------------------------
# Start-from-stop: kick then low cruise (characterisation)
# ---------------------------------------------------------------------


def test_drive_from_stop_kicks_then_cruises_low(
    isolate_polarity_dir: Path,
) -> None:
    """First motion after idle uses drive_continuous at the kick duty,
    then set_wheels at FROM_STOP_CRUISE_PCT."""
    from sirena_ui.workers import drive_controller as dc

    nav = FakeNav()
    ctrl = _make_controller(nav, default_speed=20)
    try:
        ctrl.ensure_hardware()
        assert _wait_for(lambda: nav.brake_engaged)
        ctrl.set_brake(False)
        assert _wait_for(lambda: not nav.brake_engaged)

        ctrl.drive("forward")

        def saw_kick_and_cruise() -> bool:
            dcs = [c for c in nav.calls if c[0] == "drive_continuous"]
            sws = [c for c in nav.calls if c[0] == "set_wheels"]
            return len(dcs) >= 1 and len(sws) >= 1

        assert _wait_for(saw_kick_and_cruise)

        # drive_continuous first with kick speed
        dc_calls = [c for c in nav.calls if c[0] == "drive_continuous"]
        sw_calls = [c for c in nav.calls if c[0] == "set_wheels"]
        assert len(dc_calls) >= 1
        assert dc_calls[-1][1]["speed_percent"] == dc.FROM_STOP_KICK_PCT
        assert len(sw_calls) >= 1
        last_sw = sw_calls[-1][1]
        assert last_sw["left_speed"] == dc.FROM_STOP_CRUISE_PCT
        assert last_sw["right_speed"] == dc.FROM_STOP_CRUISE_PCT

        # Second drive without stop: single drive_continuous at slider speed
        nav.calls.clear()
        ctrl.drive("forward")
        assert _wait_for(lambda: len(nav.calls) >= 1)
        dc_calls2 = [c for c in nav.calls if c[0] == "drive_continuous"]
        sw_calls2 = [c for c in nav.calls if c[0] == "set_wheels"]
        assert len(dc_calls2) >= 1
        assert dc_calls2[-1][1]["speed_percent"] == 20
        assert sw_calls2 == []
    finally:
        ctrl.shutdown()
