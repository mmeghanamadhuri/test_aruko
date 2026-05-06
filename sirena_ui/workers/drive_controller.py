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

import json
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

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


# Operator-facing speed envelope. The Yalu hub motors + JYQD drivers on
# the current Nina build are not safe to run above ~14% PWM duty on
# smooth floors — the wheels slip less and the bot runs faster than on
# carpet at the same duty. 8% is the lowest we ship as the GUI floor;
# on some benches wheels may need a higher floor after a cold start.
#
#   * Manual drive uses a fixed in-range duty (`FIXED_MANUAL_DRIVE_SPEED_PCT`);
#     `set_speed()` still clamps for programmatic callers.
#   * Factory tests may pass `default_speed_percent` on the controller.
#
# Bump these together (and re-test on a wheels-up bench) when the
# mechanical build can handle more. They're module-level so screens /
# tests can import the same constants instead of re-deriving them.
#
# Note: `NavigationManager` also applies `NINA_NAV_START_KICK_PCT` from
# settings (default aligned with MAX_SPEED_PCT). If that env is left at
# an old high value (e.g. 35), every start-from-rest will pulse that
# duty and low GUI speeds will feel ignored.
MIN_SPEED_PCT = 8
MAX_SPEED_PCT = 14

# Single manual-drive duty (no slider): midpoint of the safe envelope.
FIXED_MANUAL_DRIVE_SPEED_PCT = (MIN_SPEED_PCT + MAX_SPEED_PCT) // 2

# When both wheels share the same **forward** direction, optional right duty
# delta (START / RUN, PWM points). Defaults 0 / 0 → same speed L/R for kick
# and cruise. Reverse (both backward), turns, and coast stay symmetric.
RIGHT_WHEEL_EXTRA_START_PP = 0
RIGHT_WHEEL_EXTRA_RUN_PP = 0

# When manual drive begins from a full stop (`_active_drive` is None),
# apply a short kick at FROM_STOP_KICK_PCT, then drop to FROM_STOP_CRUISE_PCT
# so logs, lidar, and bench observation can characterise motion at a lower
# duty. The cruise value can be below MIN_SPEED_PCT; it is sent only to the
# nav layer (UI clamp does not apply to hardware PWM).
FROM_STOP_KICK_PCT = 14
FROM_STOP_CRUISE_PCT = 5


def _clamp_speed(pct: int) -> int:
    """Clamp `pct` into the operator-safe envelope. Negative / non-int
    inputs are coerced to MIN_SPEED_PCT rather than 0 - inside this
    project there's no legitimate caller asking for speed=0 (stops go
    through `set_brake()` / `stop()` which don't touch speed_pct), so
    treating speed=0 as "minimum cruise" is safer than letting it slip
    through as a literal halt that bypasses the brake state machine.
    """
    return max(MIN_SPEED_PCT, min(MAX_SPEED_PCT, int(pct)))


def _pair_duties_with_right_bias(
    left_dir: str,
    left_base: int,
    right_dir: str,
    right_base: int,
    *,
    start_phase: bool,
) -> Tuple[int, int]:
    """Left duty unchanged; right duty + START/RUN delta when both wheels move
    **forward** together. Reverse (both **backward**), opposite directions
    (turn-in-place), and coast (any zero duty) stay symmetric."""
    lb, rb = int(left_base), int(right_base)
    if lb == 0 and rb == 0:
        return 0, 0
    if left_dir != right_dir:
        return lb, rb
    if lb == 0 or rb == 0:
        return lb, rb
    if left_dir == NavigationManager.DIR_BACKWARD:
        return lb, rb
    extra = (
        RIGHT_WHEEL_EXTRA_START_PP if start_phase else RIGHT_WHEEL_EXTRA_RUN_PP
    )
    return lb, max(0, min(100, int(rb) + int(extra)))

# Heartbeat interval for re-issuing the current SET while a D-pad
# button or arrow key is held. Only matters when the active backend is
# the remote Pi bridge - the bridge has a safety watchdog (default
# 1.5 s) that calls soft_stop() if no command arrives while the wheels
# are commanded to non-zero PWM. We tick well under that so a held
# button doesn't time out.
#
# Local Jetson-GPIO mode doesn't have a watchdog (PWM stays asserted
# until we change it) but a re-issued SET in the same direction is
# essentially free - it just re-writes the same duty cycle - so we
# leave the heartbeat on for both backends to keep the code path
# uniform.
_HEARTBEAT_INTERVAL_SEC = 0.3
# If the worker queue already has more than this many commands
# pending, we skip enqueueing the next heartbeat tick instead of
# piling up. Prevents runaway growth if the bridge / serial link
# stalls and SETs start taking longer than the heartbeat interval.
_HEARTBEAT_MAX_QUEUED = 2


# ---------------------------------------------------------------------
# Wheel-polarity persistence
#
# The Nina hardware comes off the bench with one or both motors phase-
# wired backward, depending on which JYQD got soldered to which hub
# motor. The historical fix was an env var (NINA_NAV_INVERT_LEFT /
# RIGHT) seeded into the kiosk systemd unit, which required SSHing in
# and re-running the installer every time. The Drive screen now exposes
# Flip L / Flip R toggles that can be flipped at runtime - the chosen
# polarity is persisted here so it survives a reboot of the Jetson.
#
# Precedence on startup:
#   1. ~/.config/sirena/drive_polarity.json if present
#   2. NINA_NAV_INVERT_LEFT / NINA_NAV_INVERT_RIGHT env vars
#   3. False (no flip)
# ---------------------------------------------------------------------


def _polarity_state_path() -> Path:
    """Where we persist the chosen wheel polarity. XDG-friendly."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "sirena" / "drive_polarity.json"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def _load_persisted_polarity() -> Tuple[Optional[bool], Optional[bool]]:
    """Return `(invert_left, invert_right)` if the JSON file is present
    and well-formed; `(None, None)` otherwise (caller falls back to env
    vars). Logs but never raises - a corrupted file should never stop
    the GUI from booting."""
    path = _polarity_state_path()
    try:
        if not path.exists():
            return (None, None)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        left = data.get("invert_left")
        right = data.get("invert_right")
        return (
            bool(left) if left is not None else None,
            bool(right) if right is not None else None,
        )
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        log.warning("Could not read polarity state from %s: %s", path, exc)
        return (None, None)


def _save_persisted_polarity(invert_left: bool, invert_right: bool) -> None:
    """Write the polarity to disk so the next boot picks it up. Best-
    effort: a write failure logs a warning but does not raise, so the
    operator can still flip the toggle and drive (just with a one-shot
    setting that won't survive a restart)."""
    path = _polarity_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then rename so we never leave
        # half-written JSON on disk if the process is killed mid-write.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "invert_left": bool(invert_left),
                    "invert_right": bool(invert_right),
                },
                fh,
                indent=2,
            )
            fh.write("\n")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        log.warning("Could not save polarity state to %s: %s", path, exc)


def _resolve_initial_polarity() -> Tuple[bool, bool]:
    """Highest-precedence source wins: persisted JSON, then env vars,
    then False. Centralised so DriveController and any future tools
    derive the same boot-time defaults."""
    left, right = _load_persisted_polarity()
    if left is None:
        left = _env_truthy("NINA_NAV_INVERT_LEFT")
    if right is None:
        right = _env_truthy("NINA_NAV_INVERT_RIGHT")
    return (bool(left), bool(right))


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
        Defaults to 8% (matches `NavigationConfig.default_speed_percent` /
        `NINA_NAV_SPEED` when unset).
        """
        super().__init__(parent)

        self._injected_nav: Optional[NavigationManagerLike] = nav_manager
        self._config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._nav: Optional[NavigationManagerLike] = None
        self._init_attempted = False

        if default_speed_percent is not None:
            initial_speed = _clamp_speed(default_speed_percent)
        else:
            initial_speed = _clamp_speed(FIXED_MANUAL_DRIVE_SPEED_PCT)

        self._lock = threading.RLock()
        # Wheel polarity is resolved here (persisted JSON > env var >
        # False) and re-applied to the nav manager from `_do_init`.
        # That way the polarity survives a boot AND the operator can
        # flip it at runtime from the Drive screen without touching
        # the kiosk service or env vars.
        initial_invert_left, initial_invert_right = _resolve_initial_polarity()
        self._state = {
            "connected": False,
            "speed_pct": initial_speed,
            "direction": "idle",
            "brake": True,
            "reverse": False,
            "heading_deg": 0,
            "distance_m": 0.0,
            "driver_message": "",
            "invert_left": initial_invert_left,
            "invert_right": initial_invert_right,
        }

        # Last (left_dir, left_speed, right_dir, right_speed) that was
        # actually sent to the underlying nav backend. The heartbeat
        # thread replays this verbatim rather than re-deriving from
        # `state["direction"]` so the autonomous pilot's per-wheel
        # speeds (which can differ from the GUI slider) are preserved.
        # Cleared whenever the wheels are commanded to stop / brake /
        # estop so the heartbeat goes quiet between drives.
        self._active_drive: Optional[Tuple[str, int, str, int]] = None

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

        # Heartbeat thread: while a direction is active and the brake
        # is off, re-issues the current SET at _HEARTBEAT_INTERVAL_SEC
        # so the remote bridge's watchdog never trips during a held
        # button press / arrow key. See `_heartbeat_loop` for details.
        self._heartbeat_stop = threading.Event()
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name="DriveControllerHeartbeat",
            daemon=True,
        )
        self._heartbeat.start()

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
        self._heartbeat_stop.set()
        self._enqueue(self._do_shutdown)
        self._cmd_q.put(None)
        self._stop_evt.set()
        # Best-effort join: both threads are daemon so we don't hang
        # shutdown forever if something inside NavigationManager wedges.
        self._worker.join(timeout=2.0)
        self._heartbeat.join(timeout=2.0)

    def set_speed(self, pct: int) -> None:
        pct = _clamp_speed(pct)
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

    def set_invert_left(self, on: bool) -> None:
        """Flip the left wheel's forward/backward polarity at runtime.

        The setting is persisted to disk so it survives a reboot. If
        the wheels are currently moving, the change applies on the
        very next SET command - which the heartbeat will issue within
        ~300 ms - so the operator sees the wheel direction flip
        without releasing the D-pad.
        """
        on = bool(on)
        with self._lock:
            if self._state["invert_left"] == on:
                return
            self._state["invert_left"] = on
        log.info("DriveController.set_invert_left(%s)", on)
        self._enqueue(self._do_apply_polarity)
        _save_persisted_polarity(*self._snapshot_polarity())
        self._emit_state()

    def set_invert_right(self, on: bool) -> None:
        """Flip the right wheel's forward/backward polarity at runtime.
        See set_invert_left for semantics."""
        on = bool(on)
        with self._lock:
            if self._state["invert_right"] == on:
                return
            self._state["invert_right"] = on
        log.info("DriveController.set_invert_right(%s)", on)
        self._enqueue(self._do_apply_polarity)
        _save_persisted_polarity(*self._snapshot_polarity())
        self._emit_state()

    def _snapshot_polarity(self) -> Tuple[bool, bool]:
        with self._lock:
            return (
                bool(self._state["invert_left"]),
                bool(self._state["invert_right"]),
            )

    def _commit_wheels(
        self,
        left_dir: str,
        left_base: int,
        right_dir: str,
        right_base: int,
        *,
        start_phase: bool,
    ) -> None:
        """set_wheels with right-wheel bias; update _active_drive."""
        if self._nav is None:
            return
        tl, tr = _pair_duties_with_right_bias(
            left_dir,
            left_base,
            right_dir,
            right_base,
            start_phase=start_phase,
        )
        self._nav.set_wheels(
            left_dir=left_dir,
            left_speed=tl,
            right_dir=right_dir,
            right_speed=tr,
        )
        with self._lock:
            self._active_drive = (left_dir, tl, right_dir, tr)

    def _do_apply_polarity(self) -> None:
        """Worker-thread side of set_invert_*. Pushes the current
        polarity into the nav manager. Safe to call multiple times -
        it just re-applies whatever is in `state`."""
        self._apply_polarity_to_nav()

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

        if reverse and direction in (_DIR_FORWARD, _DIR_BACK):
            direction = _DIR_BACK if direction == _DIR_FORWARD else _DIR_FORWARD

        with self._lock:
            self._state["direction"] = direction
        self._emit_state()
        speed = FIXED_MANUAL_DRIVE_SPEED_PCT
        self._enqueue(lambda d=direction, s=speed: self._do_drive(d, s))

    def stop(self, *, drain: bool = False) -> None:
        """Request soft stop. With ``drain=True``, drop pending worker
        commands first so a queued heartbeat SET cannot run after this
        stop (critical for face-follow / autonomy hand-off)."""
        with self._lock:
            self._state["direction"] = "idle"
        self._emit_state()
        if drain:
            self._drain_queue()
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

    def _heartbeat_loop(self) -> None:
        """Re-issue the most recent SET while the wheels are active.

        The remote Pi bridge has a safety watchdog (default 1.5 s) that
        calls `soft_stop()` if no command arrives while the wheels are
        commanded to non-zero PWM, so a single press-and-hold from the
        D-pad / arrow keys would otherwise coast to a stop after ~1.5 s.
        We tick at `_HEARTBEAT_INTERVAL_SEC` (well under the watchdog)
        and enqueue `_do_heartbeat_tick` only when there's actually a
        live SET to maintain.

        We also skip the enqueue if the worker queue already has more
        than `_HEARTBEAT_MAX_QUEUED` pending commands, so a stalled
        bridge / serial link can't make us pile up SETs faster than the
        worker can drain them.

        `wait()` returns True only when shutdown has been requested,
        so this loop exits cleanly.
        """
        while not self._heartbeat_stop.wait(_HEARTBEAT_INTERVAL_SEC):
            with self._lock:
                if self._active_drive is None:
                    continue
            if self._cmd_q.qsize() > _HEARTBEAT_MAX_QUEUED:
                continue
            self._enqueue(self._do_heartbeat_tick)

    def _do_heartbeat_tick(self) -> None:
        """Worker-thread side of the heartbeat: re-read the last SET we
        actually sent and replay it verbatim.

        Replays the cached per-wheel command (not derived from
        `state["direction"]`) so the autonomous pilot's per-wheel
        speeds - which can differ from the GUI slider - aren't
        overridden by the heartbeat. The user/pilot may have released
        the button between when the heartbeat enqueued and when this
        runs; in that case `_active_drive` is None and we no-op.
        """
        if self._nav is None:
            return
        with self._lock:
            active = self._active_drive
        if active is None:
            return
        ldir, lspeed, rdir, rspeed = active
        try:
            self._nav.set_wheels(
                left_dir=ldir, left_speed=lspeed,
                right_dir=rdir, right_speed=rspeed,
            )
        except Exception as exc:
            log.exception("heartbeat tick failed: %s", exc)

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
            # Push the persisted/env-seeded wheel polarity into the
            # nav manager BEFORE we settle into engage_brake() so the
            # very first SET issued from the GUI honours it. This is
            # what makes the runtime Flip L / Flip R toggles 'just
            # work' even on a fresh kiosk service that was never given
            # NINA_NAV_INVERT_* env vars.
            self._apply_polarity_to_nav()
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

    def _apply_polarity_to_nav(self) -> None:
        """Push the current `state["invert_left/right"]` into the nav
        manager. No-op if the nav backend doesn't expose runtime
        polarity setters (older NavigationManager versions or a test
        fake) - in that case the polarity from the frozen config is
        already applied at construction time, so the user just doesn't
        get the runtime override - which is fine."""
        if self._nav is None:
            return
        with self._lock:
            left = bool(self._state["invert_left"])
            right = bool(self._state["invert_right"])
        try:
            if hasattr(self._nav, "set_invert_left"):
                self._nav.set_invert_left(left)
            if hasattr(self._nav, "set_invert_right"):
                self._nav.set_invert_right(right)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not push polarity to nav: %s", exc)

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
            with self._lock:
                self._active_drive = None
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
            with self._lock:
                start_from_stop = self._active_drive is None
            # Use drive_continuous for all four directions so L/R is
            # held-while-pressed (matches forward/back) instead of the
            # old timed turn that auto-stopped after ~2.3s.
            if start_from_stop:
                kick = max(MIN_SPEED_PCT, int(FROM_STOP_KICK_PCT))
                cruise = max(0, min(100, int(FROM_STOP_CRUISE_PCT)))
                self._nav.drive_continuous(ldir, rdir, kick)
                self._commit_wheels(
                    ldir, kick, rdir, kick, start_phase=True,
                )
                self._commit_wheels(
                    ldir, cruise, rdir, cruise, start_phase=False,
                )
                log.info(
                    "drive from stop: kick %s%% then cruise %s%% (manual %s%%)",
                    kick,
                    cruise,
                    FIXED_MANUAL_DRIVE_SPEED_PCT,
                )
            else:
                self._commit_wheels(
                    ldir, speed_pct, rdir, speed_pct, start_phase=False,
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
            self._commit_wheels(
                ldir, speed_pct, rdir, speed_pct, start_phase=False,
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
            with self._lock:
                self._active_drive = None
        except Exception as exc:
            log.exception("stop() failed: %s", exc)

    def _do_emergency_stop(self) -> None:
        if self._nav is None:
            with self._lock:
                self._active_drive = None
                self._state["driver_message"] = (
                    "EMERGENCY STOP requested - hardware not connected"
                )
            self._emit_state()
            return
        try:
            self._nav.emergency_stop()
            with self._lock:
                self._active_drive = None
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
            if left_speed == 0 and right_speed == 0:
                self._nav.set_wheels(
                    left_dir=ldir,
                    left_speed=0,
                    right_dir=rdir,
                    right_speed=0,
                )
                with self._lock:
                    self._active_drive = None
                return
            self._commit_wheels(
                ldir, left_speed, rdir, right_speed, start_phase=False,
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
