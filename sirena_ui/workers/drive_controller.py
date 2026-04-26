"""
Real BLDC drive controller for the Drive screen.

Wraps `nina.controllers.navigation_manager.NavigationManager` (which
drives the two JYQD_V7.3E2 BLDC drivers) with a Qt-friendly worker so
the UI never blocks on GPIO/PWM calls. The public surface mirrors the
old `DriveStub` exactly, so it is a drop-in replacement:

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
    ) -> None:
        super().__init__(parent)

        self._config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._nav: Optional[NavigationManager] = None
        self._init_attempted = False

        self._lock = threading.RLock()
        self._state = {
            "connected": False,
            "speed_pct": int(self._config.default_speed_percent),
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
        self._emit_state()

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
            if direction == _DIR_FORWARD:
                self._nav.forward(speed_pct)
            elif direction == _DIR_BACK:
                self._nav.backward(speed_pct)
            elif direction == _DIR_LEFT:
                self._nav.turn_left(speed_pct)
            elif direction == _DIR_RIGHT:
                self._nav.turn_right(speed_pct)
        except Exception as exc:
            log.exception("drive(%s, %s) failed: %s", direction, speed_pct, exc)

    def _do_stop(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.stop()
        except Exception as exc:
            log.exception("stop() failed: %s", exc)

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
