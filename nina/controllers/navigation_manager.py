"""
NavigationManager for Nina (5 ft wheeled bot, Jetson Orin Nano).

This module is a clean port of the proven Sirena Raspberry Pi reference
build (`/Downloads/navigation_bldc.py` + `motor_control.py` from the Pi
prototype) onto the Jetson Orin Nano. It drives 2x JYQD_V7.3E2 BLDC
drivers (one per wheel) with the **exact same pin map and write
sequence** as the RPi build - the Orin Nano J12 header is Pi-compatible,
so every BCM number used on the RPi maps to the same physical pin here.

Why a 1:1 port and not a clever Jetson rewrite:
  Earlier Jetson builds tried to be smart about the JYQD ("VR with
  Signal-gate" mode, EL low->high re-edge to latch DIR, kick-start,
  deadband shaping) and ended up with motors that would only spin one
  way regardless of the keyboard input. The RPi reference build proves
  none of that is needed: leave Signal floating, hold EL HIGH, write DIR
  level-sensitive, drive VR with hardware PWM. That's it. This module
  mirrors that exactly.

What the RPi reference says about JYQD V7.3E2 in this build:

- The "Signal" screw on the JYQD's "set" header is **not driven**.
  The chip commutates fine with Signal floating. Earlier code that
  drove Signal HIGH was fixing a problem that didn't exist.
- DIR (Z/F) is sampled **continuously** (level-sensitive). There is no
  "EL rising edge latches direction" requirement. Direction changes
  work by simply: drop PWM, write the new DIR level, ramp PWM back up.
- `stop()` keeps EL HIGH and zeroes PWM. Only `emergency_stop()` drops
  EL LOW (chip-disabled state).
- Per-side hardware PWM. L-PWM on BCM 12 (pin 32, PWM0) and R-PWM on
  BCM 13 (pin 33, PWM2). True differential drive is supported.

Pin map (mostly mirrors the RPi reference; three pads remapped
because the Orin Nano image / carrier doesn't expose them as plain
GPIO - see notes A, B, C below):

    Function       BCM    Physical pin    Notes
    L-EL           24     18              digital out  (see note B below)
    L-DIR (Z/F)     6     31              digital out  (see note C below)
    L-PWM (VR)     12     32              hardware PWM0
    R-EL           10     19              digital out
    R-DIR (Z/F)    23     16              digital out  (see note A below)
    R-PWM (VR)     13     33              hardware PWM2
    Status RED     21     40              digital out (active-low)
    Status GREEN   20     38              digital out (active-low)
    Status BLUE    16     36              digital out (active-low)
    E-stop 1       17     11              digital in (input only)
    E-stop 2        5     29              digital in (input only)
    5 V to JYQDs    -      2 or 4         power (logic only)
    GND for L-JYQD  -     39              power
    GND for R-JYQD  -     34              power
    24 V to JYQDs   -      -              external battery to VCC screws

Direction polarity (matches RPi reference exactly):
    Left  forward  =>  L_DIR HIGH
    Right forward  =>  R_DIR LOW    (right side is mirrored)

If a wheel spins backwards from what's expected, set
NINA_NAV_INVERT_LEFT=1 or NINA_NAV_INVERT_RIGHT=1.

Note A (R-DIR pin choice):
  The RPi reference uses BCM 22 / pin 15 for R-DIR. On the specific
  Jetson Orin Nano carrier this bot uses, pin 15 is **dead** as a
  GPIO output - it sits at a constant ~1.5 V regardless of what is
  written, so the JYQD always reads it as below-threshold (= LOW)
  and the right wheel can never reverse. Bench-confirmed with
  `python3 -m nina.app.pin_probe --pin 22` (driving correctly under
  JETSON_ORIN_NANO model). We use BCM 23 / pin 16 instead - clean
  GPIO on this carrier. Override via NINA_NAV_R_DIR if a later image
  frees pin 15 back up.

Note B (L-EL pin choice):
  The RPi reference uses BCM 18 / pin 12 for L-EL. On the Orin Nano,
  pin 12 (PCM_CLK / I2S2_SCLK in the SoC pin table) is partially
  claimed by the audio device tree by default, so GPIO writes get
  overridden - the pin sits at a non-logic ~2.4 V <-> ~4 V swing
  regardless of what the kernel GPIO sysfs says. The JYQD opto-
  isolator reads this as "kind of HIGH most of the time," which
  manifests as a left wheel that occasionally spins, often jerks,
  and dies entirely at higher PWM duty. Bench-confirmed with
  `python3 -m nina.app.pin_probe --pin 18`. We use BCM 24 / pin 18
  instead - plain GPIO on this carrier. Override via NINA_NAV_L_EN
  if a later image / device-tree overlay releases pin 12.

Note C (L-DIR pin choice):
  The RPi reference uses BCM 25 / pin 22 for L-DIR. On the Orin Nano,
  pin 22 also sits at a degraded ~0 V <-> ~1.5 V swing instead of a
  clean 0/3.3 V drive, with intermittent toggle - same failure class
  as Note B (claimed by an alt-function in the L4T device tree on
  this image). The JYQD reads 1.5 V as ambiguous and locks the
  motor's direction to whichever side of its threshold it last saw,
  so the left wheel can never reverse. Bench-confirmed with
  `python3 -m nina.app.pin_probe --pin 25`. We use BCM 6 / pin 31
  instead - plain GPIO on this carrier (clean 0/3.3 V toggle). Note
  this collides with the default HC-SR04 rear-right TRIG channel; if
  you wire that ultrasonic sensor, override either pin via env var.
  Override via NINA_NAV_L_DIR if a later image frees pin 22.

One-time Jetson setup (per fresh install / new SD card):
  sudo /opt/nvidia/jetson-io/jetson-io.py
    -> "Configure Jetson 40-pin Header"
    -> "Configure header pins manually"
    -> enable both `pwm0` (pin 32) and `pwm2` (pin 33)
    -> save, reboot
After reboot, BCM 12 and BCM 13 are PWM-only (cannot also be GPIO);
all other pins above stay as plain GPIO.

Wiring rules:
- If one wheel consistently needs a **manual push** on **forward** but
  the *other* wheel does on **reverse**, scope **DIR at the JYQD screw**
  on both sides when moving in each direction. On the stock pin map,
  **right "forward"** and **left "reverse"** both assert **DIR LOW**;
  marginal LOW drive (slow opto settle, WEAK GPIO, divider boards) can
  present exactly that swapped behaviour. Software mitigations:
  `NINA_NAV_DIR_SETTLE_SEC` and `NINA_NAV_PWM_REASSERT_SEC` in
  `NavigationManager._start_both_wheels`; fix the harness if volts are
  not clean 0/3.3 V at the screw.

- Leave the Signal screw on **each** JYQD physically disconnected.
  Don't tie it to GND, don't tie it to 5V, don't run a wire from the
  Jetson - just leave the screw empty. The chip needs nothing there.
- No level shifters anywhere on the EL / DIR / PWM lines. The JYQD
  opto inputs trigger fine on Jetson 3.3V GPIOs. The cheap red 4-channel
  passive resistor-divider boards silently mangle these signals into
  intermediate voltages (~1-2V at the JYQD screw with the Jetson side
  toggling cleanly at 0/3.3V) and the failure mode looks like "wheels
  won't reverse" or "one wheel never spins". If you suspect a wiring
  issue, probe at the **JYQD screw** with `python3 -m nina.app.pin_probe
  --pin <bcm>` and compare to the Jetson header pin: if the JYQD-side
  reading isn't a clean 0V/3.3V swing matching the header, fix the
  harness.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from nina.controllers.gpio_backend import GpioBackend, create_backend
from nina.config.settings import NAV_START_KICK_SEC_MAX


log = logging.getLogger("nina.navigation")

# After EL+DIR are written with PWM 0, pause before torque so DIR lines
# reach valid levels at the JYQD optos. On some builds the wheel that
# needs DIR LOW for that motion (right "forward", left "reverse" on the
# stock map) starts reliably only after this settle. NINA_NAV_DIR_SETTLE_SEC
# (0 disables). Second PWM write: NINA_NAV_PWM_REASSERT_SEC.
_DEFAULT_DIR_PWM_GAP_SEC = float(os.environ.get("NINA_NAV_DIR_SETTLE_SEC", "0.03"))
_DEFAULT_PWM_REASSERT_SEC = float(os.environ.get("NINA_NAV_PWM_REASSERT_SEC", "0.02"))
# Brief straight-line jog opposite to the crawl (anti-backlash). 0 sec disables.
_DEFAULT_STRAIGHT_OPP_NUDGE_SEC = float(
    os.environ.get("NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC", "0.08")
)
_DEFAULT_STRAIGHT_OPP_NUDGE_PCT = int(
    os.environ.get("NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_PCT", "20")
)
_DEFAULT_OPP_ZERO_SETTLE_SEC = float(
    os.environ.get("NINA_NAV_OPPOSITE_ZERO_SETTLE_SEC", "0.04")
)


@dataclass(frozen=True)
class NavigationPins:
    """BCM pin numbers for navigation hardware.

    Values are RPi BCM numbers; the Jetson Orin Nano J12 header maps
    them to the same physical pins as the RPi 40-pin header, so the
    same numbers describe the same wiring on both boards.
    """
    l_en: int
    l_dir: int
    pwm_l: int
    r_en: int
    r_dir: int
    pwm_r: int
    led_red: int
    led_green: int
    led_blue: int
    estop_1: int
    estop_2: int

    @property
    def l_zf(self) -> int:
        """Backwards-compat alias - the JYQD calls the direction pin Z/F."""
        return self.l_dir

    @property
    def r_zf(self) -> int:
        """Backwards-compat alias - the JYQD calls the direction pin Z/F."""
        return self.r_dir


@dataclass(frozen=True)
class NavigationConfig:
    pins: "NavigationPins"
    backend_name: str = "jetson"
    pwm_frequency_hz: int = 2000           # matches RPi reference (pigpio hardware_PWM @ 2 kHz)
    default_speed_percent: int = 15        # matches RPi reference (control_speed(..., 15, ...))
    turn_duration_sec: float = 2.3         # matches GUI / autonomy expectation
    settle_delay_sec: float = 0.1          # matches RPi `time.sleep(0.1)` between stop and re-drive
    invert_left_dir: bool = False          # flip if left wheel spins opposite of expected
    invert_right_dir: bool = False         # flip if right wheel spins opposite of expected
    # When both wheels were at PWM 0, boost each moving side to at least this duty for
    # `start_kick_sec` to overcome static friction / JYQD cogging, then apply the command.
    # Set either kick field to 0 to disable (see NINA_NAV_START_KICK_*). SEC default/cap
    # matches `nina.config.settings.NAV_START_KICK_SEC_MAX` when using `load_settings`.
    start_kick_percent: int = 35
    start_kick_sec: float = 1.0
    dir_pwm_gap_sec: float = _DEFAULT_DIR_PWM_GAP_SEC
    pwm_reassert_sec: float = _DEFAULT_PWM_REASSERT_SEC
    straight_opposite_nudge_sec: float = _DEFAULT_STRAIGHT_OPP_NUDGE_SEC
    straight_opposite_nudge_pct: int = _DEFAULT_STRAIGHT_OPP_NUDGE_PCT
    opposite_zero_settle_sec: float = _DEFAULT_OPP_ZERO_SETTLE_SEC


# Default Nina pinout: 1:1 mirror of the working RPi reference build.
# Override any single pin via the corresponding NINA_NAV_* env var if a
# specific harness needs a different mapping (rare).
DEFAULT_PINS = NavigationPins(
    # NOTE: BCM 24 (pin 18), not BCM 18 (pin 12) per the RPi reference.
    # Pin 12 is partially claimed by the Orin Nano audio device tree -
    # GPIO writes get overridden, output sits at ~2.4 V / ~4 V instead
    # of clean 0/3.3 V. See Note B in the module docstring.
    l_en=int(os.environ.get("NINA_NAV_L_EN", "24")),
    # NOTE: BCM 6 (pin 31), not BCM 25 (pin 22) per the RPi reference.
    # Pin 22 sits at degraded ~0 V <-> ~1.5 V intermittent toggle on
    # this Orin Nano carrier (same alt-function-claim class as L-EL).
    # See Note C in the module docstring.
    l_dir=int(os.environ.get("NINA_NAV_L_DIR", os.environ.get("NINA_NAV_L_ZF", "6"))),
    pwm_l=int(os.environ.get("NINA_NAV_L_PWM", "12")),
    r_en=int(os.environ.get("NINA_NAV_R_EN", "10")),
    # NOTE: BCM 23 (pin 16), not BCM 22 (pin 15) per the RPi reference.
    # Pin 15 is dead as a GPIO output on the Orin Nano carrier this bot
    # uses (probed at 1.5 V constant) - see Note A in the module docstring.
    r_dir=int(os.environ.get("NINA_NAV_R_DIR", os.environ.get("NINA_NAV_R_ZF", "23"))),
    pwm_r=int(os.environ.get("NINA_NAV_R_PWM", "13")),
    led_red=21,
    led_green=20,
    led_blue=16,
    estop_1=17,
    estop_2=5,
)


class NavigationManager:
    """BLDC navigation controller mirroring the proven Raspberry Pi build.

    Public surface (kept stable so the GUI / autonomy / CLI tools don't
    need to change):

      initialize(), shutdown()
      forward(speed_percent=None)
      backward(speed_percent=None)
      turn_left(speed_percent=None, duration=None)
      turn_right(speed_percent=None, duration=None)
      drive_continuous(left_dir, right_dir, speed_percent=None)
      set_wheels(left_dir=, left_speed=, right_dir=, right_speed=)
      stop()                       # PWM=0, EL stays HIGH (RPi-style soft stop)
      emergency_stop()             # PWM=0, EL drops LOW (chip disabled)
      engage_brake() / release_brake()
      set_status(mode)
    """

    SIDE_LEFT = "left"
    SIDE_RIGHT = "right"
    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"

    def __init__(
        self,
        config: Optional[NavigationConfig] = None,
        backend: Optional[GpioBackend] = None,
    ) -> None:
        self.config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._backend: GpioBackend = backend or create_backend(self.config.backend_name)
        self._is_initialized = False
        # Runtime polarity overrides. None = fall back to the frozen
        # config (env-var seeded). The Drive screen calls
        # set_invert_left/right() so the operator can flip a wheel
        # without restarting - effective on the next set_wheels call.
        self._invert_left_override: Optional[bool] = None
        self._invert_right_override: Optional[bool] = None
        # Last PWM duties sent (0..100) for breakaway-kick heuristics.
        self._last_l_pwm = 0
        self._last_r_pwm = 0
        self._last_straight_sign: Optional[int] = None  # +1 F / -1 B / None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        if self._is_initialized:
            return

        self._backend.setup()
        pins = self.config.pins

        # E-stop pins are inputs in the RPi reference; configure as
        # output is best-effort and silently skipped if the platform
        # rejects it (some Orin Nano builds reserve these pads).
        for pin in (pins.estop_1, pins.estop_2):
            try:
                self._backend.configure_output(pin)
            except Exception:
                log.debug("E-stop pin %s left as input", pin)

        for pin in (
            pins.led_red, pins.led_green, pins.led_blue,
            pins.l_en, pins.l_dir,
            pins.r_en, pins.r_dir,
        ):
            self._backend.configure_output(pin)

        self._backend.configure_pwm(pins.pwm_l, self.config.pwm_frequency_hz)
        self._backend.configure_pwm(pins.pwm_r, self.config.pwm_frequency_hz)

        # Park: chip disabled (EL=LOW), DIR set to forward defaults,
        # PWM=0. Mirrors the RPi behaviour: setup_gpio() leaves all
        # outputs at 0; the first forward()/backward() call drives EL
        # HIGH and sets a duty cycle.
        self._backend.write(pins.l_en, 0)
        self._backend.write(pins.r_en, 0)
        self._backend.write(pins.l_dir, 1)   # left forward = HIGH
        self._backend.write(pins.r_dir, 0)   # right forward = LOW (mirrored)
        # LEDs OFF (active-low; HIGH = off).
        self._backend.write(pins.led_red, 1)
        self._backend.write(pins.led_green, 1)
        self._backend.write(pins.led_blue, 1)

        self._is_initialized = True
        log.info(
            "NavigationManager initialized backend=%s "
            "L_EN=BCM%d L_DIR=BCM%d L_PWM=BCM%d "
            "R_EN=BCM%d R_DIR=BCM%d R_PWM=BCM%d "
            "invert_left=%s invert_right=%s",
            self._backend.name,
            pins.l_en, pins.l_dir, pins.pwm_l,
            pins.r_en, pins.r_dir, pins.pwm_r,
            self.config.invert_left_dir, self.config.invert_right_dir,
        )

    def shutdown(self) -> None:
        if not self._is_initialized:
            return
        try:
            self.emergency_stop()
        finally:
            try:
                self._backend.shutdown()
            except Exception:
                log.warning("backend shutdown raised; ignoring")
            self._is_initialized = False
            log.info("NavigationManager shutdown")

    # ------------------------------------------------------------------
    # Motion API (mirrors the RPi reference 1:1)
    # ------------------------------------------------------------------

    def forward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_FORWARD, speed)
        log.info("forward speed=%s%%", speed)

    def backward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_BACKWARD, speed)
        log.info("backward speed=%s%%", speed)

    def turn_left(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """In-place pivot left: left wheel reverses, right wheel forwards."""
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(
            left_dir=self.DIR_BACKWARD,
            right_dir=self.DIR_FORWARD,
            speed=speed,
            duration=duration,
        )
        log.info("turn_left speed=%s%% (L=back R=forward)", speed)

    def turn_right(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """In-place pivot right: left wheel forwards, right wheel reverses."""
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(
            left_dir=self.DIR_FORWARD,
            right_dir=self.DIR_BACKWARD,
            speed=speed,
            duration=duration,
        )
        log.info("turn_right speed=%s%% (L=forward R=back)", speed)

    def drive_continuous(
        self,
        left_dir: str,
        right_dir: str,
        speed_percent: Optional[int] = None,
    ) -> None:
        """Per-wheel motion that does NOT auto-stop.

        Used by the GUI's held D-pad buttons so left/right last as long
        as the operator holds the key down. Mirrors the RPi
        forward_forever/backward_forever pattern: stop, settle, then
        arm both drivers (DIR + EL, PWM 0) and apply both duties in
        quick succession so neither wheel leads the other at start.
        """
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        speed = self._resolve_speed(speed_percent)
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._start_both_wheels(
            left_dir=left_dir,
            left_speed=speed,
            right_dir=right_dir,
            right_speed=speed,
        )
        log.info(
            "drive_continuous L=%s R=%s speed=%s%%",
            left_dir, right_dir, speed,
        )

    def set_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Apply per-wheel direction + speed without any settle delay.

        Returns immediately. Used by the autonomy hot path (5-20 Hz)
        where each tick wants to nudge the duty cycle without re-running
        the stop/settle sequence. Direction is sampled level-sensitive
        by the JYQD, so changing DIR mid-spin is safe. Both wheels are
        armed at PWM 0 before duties are applied back-to-back (local GPIO
        only — remote mode sends one SET frame).
        """
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        self._start_both_wheels(
            left_dir=left_dir,
            left_speed=left_speed,
            right_dir=right_dir,
            right_speed=right_speed,
        )

    def stop(self) -> None:
        """Soft stop matching the RPi reference: PWM=0, EL stays HIGH.

        The JYQD samples DIR continuously, so direction changes work
        without dropping EL. `emergency_stop()` is the variant that
        drops EL=LOW for a true chip-disabled state.
        """
        self._start_both_wheels(
            left_dir=self.DIR_FORWARD,
            left_speed=0,
            right_dir=self.DIR_FORWARD,
            right_speed=0,
        )
        time.sleep(self.config.settle_delay_sec)
        log.info("stop (EL=HIGH, PWM=0)")

    def emergency_stop(self) -> None:
        """Mirrors RPi `emergency_stop`: stop then drop EL=LOW on both sides."""
        log.warning("EMERGENCY STOP requested")
        try:
            self._control_speed(self.SIDE_LEFT, True, 0, self.DIR_FORWARD)
            self._control_speed(self.SIDE_RIGHT, True, 0, self.DIR_FORWARD)
            self._control_speed(self.SIDE_LEFT, False, 0, self.DIR_FORWARD)
            self._control_speed(self.SIDE_RIGHT, False, 0, self.DIR_FORWARD)
            self._set_status_led(red=True, green=True, blue=True)
        except Exception as exc:
            log.exception("emergency_stop failed: %s", exc)
        finally:
            self._last_l_pwm = 0
            self._last_r_pwm = 0
            self._last_straight_sign = None

    def engage_brake(self) -> None:
        """Coast-stop both wheels.

        The JYQD V7.3E2 has no software brake unless its BRK pin is
        wired separately, so this is just a stop. We keep the method on
        the API surface because the GUI's brake toggle wires into it.
        """
        self.stop()
        log.info("brake engaged (PWM=0; motors coast)")

    def release_brake(self) -> None:
        """Logical 'brake off'. No-op for the RPi-mirror config.

        EL stays HIGH whenever the manager is initialised; PWM=0 IS the
        brake. Kept on the API so existing GUI / CLI callers don't need
        to change.
        """
        log.info("brake released (no-op; ready for next motion command)")

    def set_status(self, mode: str) -> None:
        """Drive the RGB status LED. Modes mirror the RPi notifier()."""
        if not self._is_initialized:
            return
        self._set_status_led(red=False, green=False, blue=False)
        if mode == "CONNECTED":
            self._set_status_led(green=True)
        elif mode == "ERROR":
            self._set_status_led(red=True)
        elif mode == "WAITING":
            self._set_status_led(blue=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_side_motion(self, side: str, direction: str) -> None:
        """Arm one driver: EL HIGH, DIR set, PWM 0.

        Used together with `_apply_side_pwm`: program both sides to
        their directions with zero torque first, then raise both duties
        in quick succession. That avoids the old left-then-right
        `_control_speed` ordering where the first wheel could spin up
        while the second was still at PWM 0 (instant yaw)."""
        self._require_initialized()
        if side not in (self.SIDE_LEFT, self.SIDE_RIGHT):
            raise ValueError(f"Invalid side '{side}'")
        if direction not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid direction '{direction}'")

        pins = self.config.pins
        forward = direction == self.DIR_FORWARD

        if side == self.SIDE_LEFT:
            level = 1 if forward else 0
            if self._effective_invert_left():
                level = 0 if level else 1
            self._backend.write(pins.l_en, 1)
            self._backend.write(pins.l_dir, level)
            self._backend.set_duty(pins.pwm_l, 0.0)
        else:
            level = 0 if forward else 1
            if self._effective_invert_right():
                level = 0 if level else 1
            self._backend.write(pins.r_en, 1)
            self._backend.write(pins.r_dir, level)
            self._backend.set_duty(pins.pwm_r, 0.0)

    def _apply_side_pwm(self, side: str, speed_percent: int) -> None:
        """Set PWM duty only; EL and DIR must already match `_prepare_side_motion`."""
        self._require_initialized()
        if side not in (self.SIDE_LEFT, self.SIDE_RIGHT):
            raise ValueError(f"Invalid side '{side}'")
        speed = max(0, min(100, int(speed_percent)))
        duty = float(speed)
        pins = self.config.pins
        if side == self.SIDE_LEFT:
            self._backend.set_duty(pins.pwm_l, duty)
        else:
            self._backend.set_duty(pins.pwm_r, duty)

    def _start_both_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Start or retarget both wheels with minimal inter-wheel delay."""
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")

        ls = max(0, min(100, int(left_speed)))
        rs = max(0, min(100, int(right_speed)))
        was_rest = self._last_l_pwm == 0 and self._last_r_pwm == 0
        moving_now = ls > 0 or rs > 0
        straight_crawl = (
            left_dir == right_dir
            and ls == rs
            and ls > 0
        )
        target_sign: Optional[int] = None
        if straight_crawl:
            target_sign = 1 if left_dir == self.DIR_FORWARD else -1

        cfg = self.config
        ns = max(0.0, min(0.5, float(cfg.straight_opposite_nudge_sec)))
        pct = max(0, min(100, int(cfg.straight_opposite_nudge_pct)))
        want_nudge = (
            straight_crawl
            and ns > 0
            and pct > 0
            and (
                was_rest
                or (
                    self._last_straight_sign is not None
                    and target_sign is not None
                    and self._last_straight_sign != target_sign
                )
            )
        )

        if want_nudge:
            opp_dir = (
                self.DIR_BACKWARD
                if left_dir == self.DIR_FORWARD
                else self.DIR_FORWARD
            )
            self._prepare_side_motion(self.SIDE_LEFT, opp_dir)
            self._prepare_side_motion(self.SIDE_RIGHT, opp_dir)
            gap_pre = max(0.0, min(0.2, float(cfg.dir_pwm_gap_sec)))
            if gap_pre > 0:
                time.sleep(gap_pre)
            nd = max(1, min(100, (ls * pct + 99) // 100))
            self._apply_side_pwm(self.SIDE_LEFT, nd)
            self._apply_side_pwm(self.SIDE_RIGHT, nd)
            time.sleep(ns)
            self._apply_side_pwm(self.SIDE_LEFT, 0)
            self._apply_side_pwm(self.SIDE_RIGHT, 0)
            zs = max(0.0, min(0.2, float(cfg.opposite_zero_settle_sec)))
            if zs > 0:
                time.sleep(zs)

        self._prepare_side_motion(self.SIDE_LEFT, left_dir)
        self._prepare_side_motion(self.SIDE_RIGHT, right_dir)

        if was_rest and moving_now:
            gap = max(0.0, min(0.2, float(cfg.dir_pwm_gap_sec)))
            if gap > 0:
                time.sleep(gap)
        kp = max(0, min(100, int(cfg.start_kick_percent)))
        ks = max(0.0, min(NAV_START_KICK_SEC_MAX, float(cfg.start_kick_sec)))

        def _kick_duty(cmd: int) -> int:
            if cmd <= 0 or kp <= 0 or ks <= 0:
                return cmd
            return max(cmd, kp)

        kls = _kick_duty(ls)
        krs = _kick_duty(rs)
        need_kick = (
            was_rest
            and moving_now
            and (kls > ls or krs > rs)
        )
        if need_kick:
            self._apply_side_pwm(self.SIDE_LEFT, kls)
            self._apply_side_pwm(self.SIDE_RIGHT, krs)
            time.sleep(ks)
        self._apply_side_pwm(self.SIDE_LEFT, ls)
        self._apply_side_pwm(self.SIDE_RIGHT, rs)
        if was_rest and moving_now and (ls > 0 or rs > 0):
            rar = max(0.0, min(0.1, float(cfg.pwm_reassert_sec)))
            if rar > 0:
                time.sleep(rar)
                self._apply_side_pwm(self.SIDE_LEFT, ls)
                self._apply_side_pwm(self.SIDE_RIGHT, rs)
        self._last_l_pwm = ls
        self._last_r_pwm = rs
        if ls == 0 and rs == 0:
            self._last_straight_sign = None
        elif straight_crawl and target_sign is not None:
            self._last_straight_sign = target_sign
        else:
            self._last_straight_sign = None

    def _command_both(self, direction: str, speed: int) -> None:
        """Mirrors the RPi forward_forever / backward_forever sequence:
        stop, sleep settle, then set both wheels to the new direction."""
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._start_both_wheels(
            left_dir=direction,
            left_speed=speed,
            right_dir=direction,
            right_speed=speed,
        )

    def _timed_turn(
        self,
        *,
        left_dir: str,
        right_dir: str,
        speed: int,
        duration: Optional[float],
    ) -> None:
        """Mirrors the RPi turn_left / turn_right sequence."""
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._start_both_wheels(
            left_dir=left_dir,
            left_speed=speed,
            right_dir=right_dir,
            right_speed=speed,
        )
        time.sleep(duration if duration is not None else self.config.turn_duration_sec)
        self.stop()

    def _control_speed(
        self,
        side: str,
        enable: bool,
        speed_percent: int,
        direction: str,
    ) -> None:
        """Direct port of the RPi `control_speed` function.

        Order of writes matters and matches the RPi exactly:
          1. EN  (drive EL high or low)
          2. DIR (drive Z/F to the requested level, mirrored on right)
          3. PWM duty (hardware PWM)
        """
        self._require_initialized()
        if side not in (self.SIDE_LEFT, self.SIDE_RIGHT):
            raise ValueError(f"Invalid side '{side}'")
        if direction not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid direction '{direction}'")

        speed = max(0, min(100, int(speed_percent)))
        duty = float(speed)
        pins = self.config.pins
        forward = direction == self.DIR_FORWARD

        if side == self.SIDE_LEFT:
            level = 1 if forward else 0
            if self._effective_invert_left():
                level = 0 if level else 1
            self._backend.write(pins.l_en, 1 if enable else 0)
            self._backend.write(pins.l_dir, level)
            self._backend.set_duty(pins.pwm_l, duty)
        else:
            # RPi reference: right wheel polarity is mirrored - forward = LOW.
            level = 0 if forward else 1
            if self._effective_invert_right():
                level = 0 if level else 1
            self._backend.write(pins.r_en, 1 if enable else 0)
            self._backend.write(pins.r_dir, level)
            self._backend.set_duty(pins.pwm_r, duty)

    # ------------------------------------------------------------------
    # Runtime polarity controls. Same surface as RemoteNavigationManager
    # so DriveController can call them without caring which backend is
    # active. Effective on the next per-wheel write.
    # ------------------------------------------------------------------

    def set_invert_left(self, on: bool) -> None:
        self._invert_left_override = bool(on)
        log.info("invert_left set to %s (runtime override)", bool(on))

    def set_invert_right(self, on: bool) -> None:
        self._invert_right_override = bool(on)
        log.info("invert_right set to %s (runtime override)", bool(on))

    def get_invert_left(self) -> bool:
        return self._effective_invert_left()

    def get_invert_right(self) -> bool:
        return self._effective_invert_right()

    def _effective_invert_left(self) -> bool:
        if self._invert_left_override is not None:
            return self._invert_left_override
        return bool(self.config.invert_left_dir)

    def _effective_invert_right(self) -> bool:
        if self._invert_right_override is not None:
            return self._invert_right_override
        return bool(self.config.invert_right_dir)

    def _set_status_led(
        self,
        red: bool = False,
        green: bool = False,
        blue: bool = False,
    ) -> None:
        # Active-low LEDs: write 0 to turn ON.
        pins = self.config.pins
        self._backend.write(pins.led_red, 0 if red else 1)
        self._backend.write(pins.led_green, 0 if green else 1)
        self._backend.write(pins.led_blue, 0 if blue else 1)

    def _resolve_speed(self, requested: Optional[int]) -> int:
        if requested is None:
            return self.config.default_speed_percent
        return max(0, min(100, int(requested)))

    def _require_initialized(self) -> None:
        if not self._is_initialized:
            raise RuntimeError(
                "NavigationManager is not initialized. Call initialize() first."
            )
