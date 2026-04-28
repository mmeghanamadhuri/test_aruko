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

Pin map (mirror of the working RPi build):

    Function       BCM    Physical pin    Notes
    L-EL           18     12              digital out
    L-DIR (Z/F)    25     22              digital out
    L-PWM (VR)     12     32              hardware PWM0
    R-EL           10     19              digital out
    R-DIR (Z/F)    23     16              digital out  (see note A below)
    R-PWM (VR)     13     33              hardware PWM2
    Status RED     21     40              digital out (active-low)
    Status GREEN   20     38              digital out (active-low)
    Status BLUE    16     36              digital out (active-low)
    E-stop 1       17     11              digital in (input only)
    E-stop 2        5     29              digital in (input only)
    5 V to JYQDs    -      2 or 4         power
    GND for L-JYQD  -     39              power
    GND for R-JYQD  -     34              power

Direction polarity (matches RPi reference exactly):
    Left  forward  =>  L_DIR HIGH
    Right forward  =>  R_DIR LOW    (right side is mirrored)

If a wheel spins backwards from what's expected, set
NINA_NAV_INVERT_LEFT=1 or NINA_NAV_INVERT_RIGHT=1.

Note A (R-DIR pin choice):
  The RPi reference uses BCM 22 / pin 15 for R-DIR. On the specific
  Jetson Orin Nano carrier this bot uses, pin 15 is **dead** as a
  GPIO output - it sits at a constant ~1.5 V regardless of what is
  written, which the JYQD reads as below-threshold (= LOW always),
  so the right wheel can never reverse. We bench-confirmed this with
  `python3 -m nina.app.pin_probe --pin 22`. Whether pin 15 is held by
  an alt-function in the L4T device tree on this image, or whether
  the carrier board routes it to something else, isn't worth
  unwinding - we just use BCM 23 / pin 16 instead, which is plain
  GPIO on this same board and was the proven R-DIR pin in the
  pre-rewrite shared-PWM config. Override via NINA_NAV_R_DIR if a
  later image / carrier frees pin 15 back up.

One-time Jetson setup (per fresh install / new SD card):
  sudo /opt/nvidia/jetson-io/jetson-io.py
    -> "Configure Jetson 40-pin Header"
    -> "Configure header pins manually"
    -> enable both `pwm0` (pin 32) and `pwm2` (pin 33)
    -> save, reboot
After reboot, BCM 12 and BCM 13 are PWM-only (cannot also be GPIO);
all other pins above stay as plain GPIO.

Wiring rules:
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


log = logging.getLogger("nina.navigation")


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


# Default Nina pinout: 1:1 mirror of the working RPi reference build.
# Override any single pin via the corresponding NINA_NAV_* env var if a
# specific harness needs a different mapping (rare).
DEFAULT_PINS = NavigationPins(
    l_en=int(os.environ.get("NINA_NAV_L_EN", "18")),
    l_dir=int(os.environ.get("NINA_NAV_L_DIR", os.environ.get("NINA_NAV_L_ZF", "25"))),
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
        write each wheel's direction + speed.
        """
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        speed = self._resolve_speed(speed_percent)
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
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
        by the JYQD, so changing DIR mid-spin is safe.
        """
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        self._control_speed(self.SIDE_LEFT, True, left_speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, right_speed, right_dir)

    def stop(self) -> None:
        """Soft stop matching the RPi reference: PWM=0, EL stays HIGH.

        The JYQD samples DIR continuously, so direction changes work
        without dropping EL. `emergency_stop()` is the variant that
        drops EL=LOW for a true chip-disabled state.
        """
        self._control_speed(self.SIDE_LEFT, True, 0, self.DIR_FORWARD)
        self._control_speed(self.SIDE_RIGHT, True, 0, self.DIR_FORWARD)
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

    def _command_both(self, direction: str, speed: int) -> None:
        """Mirrors the RPi forward_forever / backward_forever sequence:
        stop, sleep settle, then set both wheels to the new direction."""
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._control_speed(self.SIDE_LEFT, True, speed, direction)
        self._control_speed(self.SIDE_RIGHT, True, speed, direction)

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
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
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
            if self.config.invert_left_dir:
                level = 0 if level else 1
            self._backend.write(pins.l_en, 1 if enable else 0)
            self._backend.write(pins.l_dir, level)
            self._backend.set_duty(pins.pwm_l, duty)
        else:
            # RPi reference: right wheel polarity is mirrored - forward = LOW.
            level = 0 if forward else 1
            if self.config.invert_right_dir:
                level = 0 if level else 1
            self._backend.write(pins.r_en, 1 if enable else 0)
            self._backend.write(pins.r_dir, level)
            self._backend.set_duty(pins.pwm_r, duty)

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
