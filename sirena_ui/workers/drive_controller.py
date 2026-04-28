"""
Real BLDC drive controller for the Drive screen.

Wraps a navigation manager (either
`nina.controllers.navigation_manager.NavigationManager` driving the
JYQDs from Jetson GPIOs, or
`nina.controllers.remote_navigation_manager.RemoteNavigationManager`
sending commands over serial to a Raspberry Pi running
`pi_motor_bridge`) with a Qt-friendly worker so the UI never blocks
on GPIO / serial calls. The public surface mirrors the old `DriveStub`
exactly, so it is a drop-in replacement:

  state_changed(dict)  signal
  state()              snapshot
  set_speed(pct)
  set_brake(on)
  set_reverse(on)
  drive(direction)     direction in {forward, back, left, right}
  stop()

Hardware-touching operations (init, brake, drive, stop, shutdown) are
serialised onto a dedicated worker thread via a command queue so:

  * `forward`/`backward` calls (which include a 0.1s settle sleep)
    don't stall the GUI.
  * `turn_left`/`turn_right` (which block for ~2.3s by design) run
    concurrently with UI updates.
  * Commands always execute in the order they were issued.

Pure state changes (speed, reverse) are applied synchronously since
they only affect the next drive command.

If the hardware backend is not available - typically when the GUI is
run on a developer Mac without `Jetson.GPIO` installed, or when the
PWM pins haven't been enabled via `jetson-io.py` - the controller
falls back to a "simulation" mode: the in-memory state machine still
updates so the screen behaves normally, but no PWM is sent. The
failure reason is exposed through `state()["driver_message"]` so the
UI can render an informative pill instead of pretending everything is
fine.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)

# Type alias only; the remote manager is imported lazily by the factory
# so this file stays usable on dev machines without pyserial.
NavigationManagerLike = object


log = logging.getLogger("sirena_ui.drive")


_DIR_FORWARD = "forward"
_DIR_BACK = "back"
_DIR_LEFT = "left"
_DIR_RIGHT = "right"

_VALID_DIRECTIONS = {_DIR_FORWARD, _DIR_BACK, _DIR_LEFT, _DIR_RIGHT}


class DriveController(QObject):
    """Qt facade over `NavigationManager` for the Drive screen."""

    state_changed = pyqtSignal(dict)

    def __init__(
        self,
        config: Optional[NavigationConfig] = None,
        parent=None,
        *,
        nav_manager: Optional[NavigationManagerLike] = None,
        default_speed_percent: Optional[int] = None,
    ) -> None:
        """Construct the Qt-side facade.

        Two construction modes are supported:

          1. Local (legacy / default): pass a `NavigationConfig` (or
             nothing, to get the env-driven defaults). DriveController
             will instantiate `NavigationManager` itself when the
             worker thread runs `_do_init`.

          2. Factory-injected: pass a pre-built `nav_manager` (any
             object implementing the NavigationManager surface, e.g.
             `RemoteNavigationManager`). `_do_init` will call its
             `initialize()` instead of constructing one. Use this from
             `NinaService` when `NINA_NAV_MODE=remote`.

        `default_speed_percent` is only needed when using mode (2),
        because we can't read it from a NavigationConfig in that case.
        Defaults to 15% (the historical RPi-prototype default).
        """
        super().__init__(parent)

        self._injected_nav: Optional[NavigationManagerLike] = nav_manager
        self._config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._nav: Optional[NavigationManagerLike] = None
        self._init_attempted = False

        if default_speed_percent is not None:
            initial_speed = int(default_speed_percent)
        else:
            initial_speed = int(self._config.default_speed_percent)

        self._lock = threading.RLock()
        self._state = {
            "connected": False,
            "speed_pct": initial_speed,
            "direction": "idle",
            "brake": True,
            "reverse": False,
            "heading_deg": 0,
            "distance_m": 0.0,
            "driver_message": "",
        }

        # All hardware-touching work runs on a single worker thread, in
        # the order commands were issued, so GUI clicks never collide
        # with a still-blocking turn.
        self._cmd_q: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._stop_evt = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="DriveController",
            daemon=True,
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # Public API (matches the old DriveStub)
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._state["connected"])

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def ensure_hardware(self) -> None:
        """Kick off lazy initialisation of the BLDC drivers.

        Safe to call repeatedly; the worker dedupes via
        `_init_attempted` so re-entry from `on_enter()` is free.
        """
        self._enqueue(self._do_init)

    def shutdown(self) -> None:
        """Tear down the worker thread and release GPIO."""
        self._enqueue(self._do_shutdown)
        self._cmd_q.put(None)
        self._stop_evt.set()
        # Best-effort join: the worker is daemon so we don't hang shutdown
        # forever if something inside NavigationManager wedges.
        self._worker.join(timeout=2.0)

    def set_speed(self, pct: int) -> None:
        pct = max(0, min(100, int(pct)))
        with self._lock:
            self._state["speed_pct"] = pct
            direction = self._state["direction"]
            brake = self._state["brake"]
        self._emit_state()

        # If the wheels are currently moving, push the new duty cycle
        # straight through so the speed slider acts live. We deliberately
        # use `set_wheels` (no settle / no kick-start) here - those only
        # matter when starting from rest, and re-running them on every
        # slider tick would chop the motors. The order is preserved by
        # the worker queue so a still-pending start command will run
        # first and this update will follow.
        if not brake and direction != "idle":
            self._enqueue(
                lambda d=direction, s=pct: self._do_apply_live_speed(d, s)
            )

    def set_reverse(self, on: bool) -> None:
        # Reverse is interpreted as "swap forward/back at the hardware
        # layer", which is the intuitive meaning when the operator is
        # watching a rear-facing camera. Left/right are unaffected.
        with self._lock:
            self._state["reverse"] = bool(on)
        self._emit_state()

    def set_brake(self, on: bool) -> None:
        with self._lock:
            self._state["brake"] = bool(on)
            if on:
                self._state["direction"] = "idle"
        self._emit_state()
        if on:
            self._enqueue(self._do_brake_on)
        else:
            self._enqueue(self._do_brake_off)

    def drive(self, direction: str) -> None:
        if direction not in _VALID_DIRECTIONS:
            log.warning("drive(): unknown direction '%s'", direction)
            return

        with self._lock:
            if self._state["brake"]:
                return
            reverse = self._state["reverse"]
            speed = int(self._state["speed_pct"])

        if reverse and direction in (_DIR_FORWARD, _DIR_BACK):
            direction = _DIR_BACK if direction == _DIR_FORWARD else _DIR_FORWARD

        with self._lock:
            self._state["direction"] = direction
        self._emit_state()
        self._enqueue(lambda d=direction, s=speed: self._do_drive(d, s))

    def stop(self) -> None:
        with self._lock:
            self._state["direction"] = "idle"
        self._emit_state()
        self._enqueue(self._do_stop)

    def emergency_stop(self) -> None:
        """Hard stop: set duty=0, engage brake, light the red+green+blue
        status LED. Independent of the regular brake toggle so the user
        can fire it without first releasing the D-pad.

        Drains any pending drive commands from the worker queue so a
        kick-start or settle that was queued just before the panic
        click can't sneak in after the e-stop. The command currently
        in flight (if any) still has to complete - we can't safely
        interrupt mid-sleep - but nothing else queued behind it will
        run before the stop+brake+EL-disable.
        """
        with self._lock:
            self._state["direction"] = "idle"
            self._state["brake"] = True
        self._emit_state()
        self._drain_queue()
        self._enqueue(self._do_emergency_stop)

    def _drain_queue(self) -> None:
        """Pop every pending command. Safe to call any time; the worker
        thread will simply find an empty queue and block on get()."""
        while True:
            try:
                item = self._cmd_q.get_nowait()
            except queue.Empty:
                return
            # Preserve the shutdown sentinel if shutdown was already
            # requested; otherwise drop the callable on the floor.
            if item is None:
                self._cmd_q.put(None)
                return

    def drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Continuous, non-blocking wheel control.

        Used by the autonomous pilot - calling this at 5-20 Hz steers
        the robot smoothly without each call blocking on the timed-turn
        sleep that `drive('left')` / `drive('right')` use.

        `left_dir` / `right_dir` are 'forward' or 'back'; speeds are
        0..100. Brake state is honoured: if the operator engaged the
        brake, this call is a no-op.
        """
        for d in (left_dir, right_dir):
            if d not in (_DIR_FORWARD, _DIR_BACK):
                log.warning("drive_wheels: unknown direction '%s'", d)
                return
        with self._lock:
            if self._state["brake"]:
                return
        ls = max(0, min(100, int(left_speed)))
        rs = max(0, min(100, int(right_speed)))
        # Reflect direction in state so the screen pill shows what
        # autonomy is actually doing right now.
        with self._lock:
            if ls == 0 and rs == 0:
                self._state["direction"] = "idle"
            elif left_dir == right_dir:
                self._state["direction"] = (
                    "forward" if left_dir == _DIR_FORWARD else "back"
                )
            else:
                self._state["direction"] = (
                    "left" if left_dir == _DIR_BACK else "right"
                )
        self._emit_state()
        self._enqueue(
            lambda: self._do_drive_wheels(left_dir, ls, right_dir, rs)
        )

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _enqueue(self, fn: Callable[[], None]) -> None:
        self._cmd_q.put(fn)

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                cmd = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if cmd is None:
                break
            try:
                cmd()
            except Exception as exc:
                log.exception("DriveController worker raised: %s", exc)

    # ------------------------------------------------------------------
    # Hardware ops (run on the worker thread)
    # ------------------------------------------------------------------

    def _do_init(self) -> None:
        if self._init_attempted:
            return
        self._init_attempted = True
        try:
            if self._injected_nav is not None:
                self._nav = self._injected_nav
            else:
                self._nav = NavigationManager(self._config)
            self._nav.initialize()
            # JYQD_V7.3E2 has no software brake unless the BRK pin is
            # wired - the safest "armed but stationary" resting state
            # is brake engaged + PWM 0, which is what initialize()
            # leaves us in. Make that explicit anyway.
            self._nav.engage_brake()
            with self._lock:
                self._state["connected"] = True
                self._state["driver_message"] = "BLDC L+R connected"
            log.info("DriveController: BLDC drivers connected")
        except Exception as exc:
            self._nav = None
            with self._lock:
                self._state["connected"] = False
                self._state["driver_message"] = f"Simulation \u2014 {exc}"
            log.warning(
                "DriveController init failed (%s) - running in simulation",
                exc,
            )
        self._emit_state()

    def _do_shutdown(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.shutdown()
        except Exception as exc:
            log.exception("DriveController shutdown raised: %s", exc)
        finally:
            self._nav = None
            with self._lock:
                self._state["connected"] = False
                self._state["driver_message"] = "Disconnected"
            self._emit_state()

    def _do_brake_on(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.engage_brake()
        except Exception as exc:
            log.exception("engage_brake failed: %s", exc)

    def _do_brake_off(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.release_brake()
        except Exception as exc:
            log.exception("release_brake failed: %s", exc)

    def _do_drive(self, direction: str, speed_pct: int) -> None:
        if self._nav is None:
            return
        try:
            ldir, rdir = self._wheel_dirs_for(direction)
            if ldir is None or rdir is None:
                return
            # Use drive_continuous for all four directions so L/R is
            # held-while-pressed (matches forward/back) instead of the
            # old timed turn that auto-stopped after ~2.3s.
            self._nav.drive_continuous(
                left_dir=ldir,
                right_dir=rdir,
                speed_percent=speed_pct,
            )
        except Exception as exc:
            log.exception("drive(%s, %s) failed: %s", direction, speed_pct, exc)

    def _do_apply_live_speed(self, direction: str, speed_pct: int) -> None:
        """Update PWM duty on the running motors without re-issuing the
        settle / kick-start sequence. Called from set_speed() while a
        D-pad button is held."""
        if self._nav is None:
            return
        ldir, rdir = self._wheel_dirs_for(direction)
        if ldir is None or rdir is None:
            return
        try:
            self._nav.set_wheels(
                left_dir=ldir,
                left_speed=speed_pct,
                right_dir=rdir,
                right_speed=speed_pct,
            )
        except Exception as exc:
            log.exception(
                "apply_live_speed(%s, %s) failed: %s",
                direction, speed_pct, exc,
            )

    def _wheel_dirs_for(self, direction: str):
        """Map a UI direction to a (left, right) pair of nav directions."""
        if self._nav is None:
            return None, None
        if direction == _DIR_FORWARD:
            return self._nav.DIR_FORWARD, self._nav.DIR_FORWARD
        if direction == _DIR_BACK:
            return self._nav.DIR_BACKWARD, self._nav.DIR_BACKWARD
        if direction == _DIR_LEFT:
            return self._nav.DIR_BACKWARD, self._nav.DIR_FORWARD
        if direction == _DIR_RIGHT:
            return self._nav.DIR_FORWARD, self._nav.DIR_BACKWARD
        return None, None

    def _do_stop(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.stop()
        except Exception as exc:
            log.exception("stop() failed: %s", exc)

    def _do_emergency_stop(self) -> None:
        if self._nav is None:
            with self._lock:
                self._state["driver_message"] = (
                    "EMERGENCY STOP requested - hardware not connected"
                )
            self._emit_state()
            return
        try:
            self._nav.emergency_stop()
            with self._lock:
                self._state["driver_message"] = (
                    "EMERGENCY STOP - brake engaged, release brake to resume"
                )
            self._emit_state()
            log.warning("DriveController: emergency_stop fired")
        except Exception as exc:
            log.exception("emergency_stop failed: %s", exc)

    def _do_drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        if self._nav is None:
            return
        try:
            ldir = (
                self._nav.DIR_FORWARD
                if left_dir == _DIR_FORWARD
                else self._nav.DIR_BACKWARD
            )
            rdir = (
                self._nav.DIR_FORWARD
                if right_dir == _DIR_FORWARD
                else self._nav.DIR_BACKWARD
            )
            self._nav.set_wheels(
                left_dir=ldir,
                left_speed=left_speed,
                right_dir=rdir,
                right_speed=right_speed,
            )
        except Exception as exc:
            log.exception(
                "drive_wheels(%s/%s, %s/%s) failed: %s",
                left_dir, left_speed, right_dir, right_speed, exc,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit_state(self) -> None:
        with self._lock:
            snapshot = dict(self._state)
        self.state_changed.emit(snapshot)
