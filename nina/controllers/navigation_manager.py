"""
NavigationManager for Nina (5ft wheeled bot).

Drives 2x JYQD_V7.3E2 BLDC drivers (one per wheel) directly from the Jetson Nano.

The pinout and per-side direction polarity below mirror the known-good
Raspberry Pi build (carbot navigation_bldc.py) so the Jetson behaves
identically:

  Left wheel : L_EN=BCM18  L_DIR=BCM25  PWM_L=BCM12 (HW PWM0)
  Right wheel: R_EN=BCM10  R_DIR=BCM22  PWM_R=BCM13 (HW PWM2)
  Status LED : RED=BCM21   GREEN=BCM20  BLUE=BCM16
  E-stop     : ESTOP1=BCM17 ESTOP2=BCM5

Direction polarity (matches the Pi):
  Left  forward => L_DIR HIGH
  Right forward => R_DIR LOW   (right side is mirrored, so opposite level)

JYQD_V7.3E2 control pins (per channel):
- EL  (enable)   : digital input on JYQD, we drive it HIGH to enable
- ZF / DIR       : digital input on JYQD, HIGH/LOW selects rotation
- VR  (speed)    : PWM input on JYQD, 0..100% duty maps to motor speed
- M / BRK        : optional digital input, NOT wired in this build
- Signal (PG)    : pulse OUTPUT from JYQD (Hall feedback), unused

There is NO separate F/R input on this driver. Direction goes through the
single direction pin (called ZF on some silkscreens, DIR on others).
There is no software brake unless the BRK pin is wired, so "brake" here
means disable EL and let the motor coast to a stop.

Hardware PWM on Jetson Nano is only available on:
- BCM 12 (physical pin 32) -> PWM0
- BCM 13 (physical pin 33) -> PWM2

Enable PWM once via: sudo /opt/nvidia/jetson-io/jetson-io.py
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from nina.controllers.gpio_backend import GpioBackend, create_backend


log = logging.getLogger("nina.navigation")


@dataclass(frozen=True)
class NavigationPins:
    """All BCM pin numbers for navigation hardware.

    l_dir / r_dir map to the JYQD's ZF pin (direction). The legacy l_zf /
    r_zf fields are kept so older configs / env overrides keep working,
    but they're aliases for the same physical pin.
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
        return self.l_dir

    @property
    def r_zf(self) -> int:
        return self.r_dir


@dataclass(frozen=True)
class NavigationConfig:
    pins: NavigationPins
    backend_name: str = "jetson"
    pwm_frequency_hz: int = 2000
    default_speed_percent: int = 15
    turn_duration_sec: float = 2.3
    settle_delay_sec: float = 0.1
    # Deadband compensation: linearly remaps the user-facing 0..100 speed
    # range to [min_duty_percent, max_duty_percent] on the actual PWM output.
    # On a 3.3V Jetson driving a 5V JYQD VR input, the motor often needs
    # ~70-80% real duty before it starts spinning; setting min_duty_percent
    # to ~70 lets `--speed 5` actually move the wheel.
    min_duty_percent: float = 0.0
    max_duty_percent: float = 100.0
    # Kick-start: BLDC motors need a brief high-duty pulse to break static
    # friction and let the controller sense rotor position. Drives the PWM
    # at kick_start_duty_percent for kick_start_duration_sec, then drops
    # to the requested speed. Set duration to 0 to disable.
    kick_start_duty_percent: float = 100.0
    kick_start_duration_sec: float = 0.25
    # Direction polarity per side. JYQD ZF=HIGH usually means one direction
    # and ZF=LOW the other, but which is "forward" depends on motor wiring.
    # Flip these if a side spins the opposite of expected.
    invert_left_dir: bool = False
    invert_right_dir: bool = False


# Default Nina pinout (BCM numbering). These match the working Raspberry Pi
# build exactly - same physical wires can be moved between Pi and Jetson
# without resoldering. PWM pins MUST be BCM 12 and BCM 13 to use hardware
# PWM on Jetson Nano.
DEFAULT_PINS = NavigationPins(
    l_en=int(os.environ.get("NINA_NAV_L_EN", "18")),
    l_dir=int(os.environ.get("NINA_NAV_L_DIR", os.environ.get("NINA_NAV_L_ZF", "25"))),
    pwm_l=int(os.environ.get("NINA_NAV_L_PWM", "12")),
    r_en=int(os.environ.get("NINA_NAV_R_EN", "10")),
    r_dir=int(os.environ.get("NINA_NAV_R_DIR", os.environ.get("NINA_NAV_R_ZF", "22"))),
    pwm_r=int(os.environ.get("NINA_NAV_R_PWM", "13")),
    led_red=21,
    led_green=20,
    led_blue=16,
    estop_1=17,
    estop_2=5,
)


class NavigationManager:
    """
    BLDC navigation controller for two-wheel differential drive using JYQD_V7.3E2 drivers.
    """

    SIDE_LEFT = "left"
    SIDE_RIGHT = "right"
    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"

    def __init__(self, config: Optional[NavigationConfig] = None,
                 backend: Optional[GpioBackend] = None) -> None:
        self.config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._backend: GpioBackend = backend or create_backend(self.config.backend_name)
        self._is_initialized = False

    def initialize(self) -> None:
        if self._is_initialized:
            return

        self._backend.setup()
        pins = self.config.pins

        for pin in (pins.estop_1, pins.estop_2):
            try:
                self._backend.configure_output(pin)
            except Exception:
                log.debug("E-stop pin %s left as input", pin)

        for pin in (pins.led_red, pins.led_green, pins.led_blue,
                    pins.l_en, pins.l_dir,
                    pins.r_en, pins.r_dir):
            self._backend.configure_output(pin)

        self._backend.configure_pwm(pins.pwm_l, self.config.pwm_frequency_hz)
        self._backend.configure_pwm(pins.pwm_r, self.config.pwm_frequency_hz)

        self._backend.write(pins.l_dir, 0)
        self._backend.write(pins.r_dir, 0)
        self._backend.write(pins.l_en, 0)
        self._backend.write(pins.r_en, 0)

        self._is_initialized = True
        log.info(
            "NavigationManager initialized backend=%s pins: L_EN=BCM%d L_ZF/DIR=BCM%d L_PWM=BCM%d "
            "R_EN=BCM%d R_ZF/DIR=BCM%d R_PWM=BCM%d invert_left=%s invert_right=%s",
            self._backend.name,
            pins.l_en, pins.l_dir, pins.pwm_l,
            pins.r_en, pins.r_dir, pins.pwm_r,
            self.config.invert_left_dir, self.config.invert_right_dir,
        )

    def shutdown(self) -> None:
        if not self._is_initialized:
            return
        try:
            self.stop()
            self._set_enable(self.SIDE_LEFT, False)
            self._set_enable(self.SIDE_RIGHT, False)
        finally:
            try:
                self._backend.shutdown()
            except Exception:
                log.warning("backend shutdown raised; ignoring")
            self._is_initialized = False
            log.info("NavigationManager shutdown")

    def forward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_FORWARD, speed)
        log.info(
            "forward speed=%s%% (L_DIR=BCM%d R_DIR=BCM%d)",
            speed, self.config.pins.l_dir, self.config.pins.r_dir,
        )

    def backward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_BACKWARD, speed)
        log.info(
            "backward speed=%s%% (L_DIR=BCM%d R_DIR=BCM%d)",
            speed, self.config.pins.l_dir, self.config.pins.r_dir,
        )

    def turn_left(self, speed_percent: Optional[int] = None,
                  duration: Optional[float] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(left_dir=self.DIR_BACKWARD, right_dir=self.DIR_FORWARD,
                         speed=speed, duration=duration)
        log.info(
            "turn_left speed=%s%% (L=backward R=forward)", speed,
        )

    def turn_right(self, speed_percent: Optional[int] = None,
                   duration: Optional[float] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(left_dir=self.DIR_FORWARD, right_dir=self.DIR_BACKWARD,
                         speed=speed, duration=duration)
        log.info(
            "turn_right speed=%s%% (L=forward R=backward)", speed,
        )

    def drive_continuous(self, left_dir: str, right_dir: str,
                         speed_percent: Optional[int] = None) -> None:
        """Start (or update) per-wheel motion that does NOT auto-stop.

        Includes the same settle + kick-start as forward()/backward() so
        a BLDC at rest catches reliably, but unlike turn_left/turn_right
        this method returns as soon as steady-state PWM is set and
        leaves the wheels running until stop() is called. Used by the
        GUI's held D-pad buttons so left/right turns last as long as
        the operator holds the key down.
        """
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        speed = self._resolve_speed(speed_percent)
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._set_direction(self.SIDE_LEFT, left_dir)
        self._set_direction(self.SIDE_RIGHT, right_dir)
        time.sleep(0.02)  # let JYQD latch direction before EL/PWM ramps
        self._kick_start(left_dir=left_dir, right_dir=right_dir,
                         target_speed=speed)
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
        log.info(
            "drive_continuous L=%s R=%s speed=%s%% (L_DIR=BCM%d R_DIR=BCM%d)",
            left_dir, right_dir, speed,
            self.config.pins.l_dir, self.config.pins.r_dir,
        )

    def stop(self) -> None:
        self._control_speed(self.SIDE_LEFT, True, 0, self.DIR_FORWARD)
        self._control_speed(self.SIDE_RIGHT, True, 0, self.DIR_FORWARD)
        time.sleep(self.config.settle_delay_sec)
        log.info("stop")

    def set_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Apply per-wheel direction + speed without any settle / kick-start
        / timed-turn behaviour. Returns immediately so a closed-loop
        autonomy controller can call this at 5-20 Hz without each call
        blocking on internal sleeps.

        Speeds are 0..100 (deadband-corrected just like `forward()` /
        `backward()`). Pass speed=0 to coast that wheel.
        """
        self._control_speed(self.SIDE_LEFT, True, left_speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, right_speed, right_dir)

    def emergency_stop(self) -> None:
        log.warning("EMERGENCY STOP requested")
        try:
            self.stop()
            self.engage_brake()
            self._set_enable(self.SIDE_LEFT, False)
            self._set_enable(self.SIDE_RIGHT, False)
            self._set_status_led(red=True, green=True, blue=True)
        except Exception as exc:
            log.exception("emergency_stop failed: %s", exc)

    def engage_brake(self) -> None:
        """Coast-stop both wheels by disabling EL on each driver. JYQD_V7.3E2
        has no software brake unless its BRK pin is wired separately."""
        self._control_speed(self.SIDE_LEFT, False, 0, self.DIR_FORWARD)
        self._control_speed(self.SIDE_RIGHT, False, 0, self.DIR_FORWARD)
        log.info("brake (EL disable) engaged - motors will coast to stop")

    def release_brake(self) -> None:
        """Re-enable EL on both drivers (does not start motion - VR/PWM
        still drives the speed)."""
        pins = self.config.pins
        self._backend.write(pins.l_en, 1)
        self._backend.write(pins.r_en, 1)
        log.info("brake released (EL re-enabled)")

    def set_status(self, mode: str) -> None:
        if not self._is_initialized:
            return
        self._set_status_led(red=False, green=False, blue=False)
        if mode == "CONNECTED":
            self._set_status_led(green=True)
        elif mode == "ERROR":
            self._set_status_led(red=True)
        elif mode == "WAITING":
            self._set_status_led(blue=True)

    def _command_both(self, direction: str, speed: int) -> None:
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._set_direction(self.SIDE_LEFT, direction)
        self._set_direction(self.SIDE_RIGHT, direction)
        time.sleep(0.02)  # let JYQD latch direction before EL/PWM ramps
        self._kick_start(left_dir=direction, right_dir=direction, target_speed=speed)
        self._control_speed(self.SIDE_LEFT, True, speed, direction)
        self._control_speed(self.SIDE_RIGHT, True, speed, direction)

    def _timed_turn(self, left_dir: str, right_dir: str,
                    speed: int, duration: Optional[float]) -> None:
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._set_direction(self.SIDE_LEFT, left_dir)
        self._set_direction(self.SIDE_RIGHT, right_dir)
        time.sleep(0.02)
        self._kick_start(left_dir=left_dir, right_dir=right_dir, target_speed=speed)
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
        time.sleep(duration if duration is not None else self.config.turn_duration_sec)
        self.stop()

    def _kick_start(self, left_dir: str, right_dir: str, target_speed: int) -> None:
        """Brief high-duty pulse to overcome static friction and rotor sensing."""
        if target_speed <= 0:
            return
        kick_dur = float(self.config.kick_start_duration_sec)
        if kick_dur <= 0:
            return
        kick_speed = max(target_speed, int(self.config.kick_start_duty_percent))
        kick_speed = max(0, min(100, kick_speed))
        self._control_speed(self.SIDE_LEFT, True, kick_speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, kick_speed, right_dir)
        time.sleep(kick_dur)
        log.info("kick-start %d%% for %.2fs", kick_speed, kick_dur)

    def _control_speed(self, side: str, enable: bool,
                       speed_percent: int, direction: str) -> None:
        self._require_initialized()
        if side not in (self.SIDE_LEFT, self.SIDE_RIGHT):
            raise ValueError(f"Invalid side '{side}'")
        if direction not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid direction '{direction}'")

        speed_percent = max(0, min(100, int(speed_percent)))
        duty_percent = self._apply_deadband(speed_percent)
        pins = self.config.pins

        self._set_direction(side, direction)
        if side == self.SIDE_LEFT:
            self._backend.write(pins.l_en, 1 if enable else 0)
            self._backend.set_duty(pins.pwm_l, duty_percent)
        else:
            self._backend.write(pins.r_en, 1 if enable else 0)
            self._backend.set_duty(pins.pwm_r, duty_percent)

    def _set_direction(self, side: str, direction: str) -> None:
        """Write the direction pin for the given side. Defaults match the
        proven Pi setup: LEFT forward = HIGH, RIGHT forward = LOW (the
        right wheel is mirrored, so opposite logic level). Per-side
        invert_*_dir flags flip these for unusual motor mountings.

        Emits a DEBUG-level log line (lifted to INFO when env var
        ``NINA_NAV_LOG_DIR=1`` is set) so a user can confirm whether the
        ZF/DIR pin actually toggles between forward and backward
        commands. Useful when "all keys spin the wheels the same way"
        because the JYQD ZF input isn't seeing any logic-level change.
        """
        pins = self.config.pins
        forward = (direction == self.DIR_FORWARD)
        if side == self.SIDE_LEFT:
            level = 1 if forward else 0
            if self.config.invert_left_dir:
                level = 0 if level else 1
            self._backend.write(pins.l_dir, level)
            pin = pins.l_dir
        else:
            level = 0 if forward else 1
            if self.config.invert_right_dir:
                level = 0 if level else 1
            self._backend.write(pins.r_dir, level)
            pin = pins.r_dir
        if os.environ.get("NINA_NAV_LOG_DIR", "").strip() in ("1", "true", "yes"):
            log.info(
                "DIR %s -> %s (BCM%d=%d, invert_%s=%s)",
                side, direction, pin, level, side,
                self.config.invert_left_dir if side == self.SIDE_LEFT
                else self.config.invert_right_dir,
            )
        else:
            log.debug(
                "DIR %s -> %s (BCM%d=%d)",
                side, direction, pin, level,
            )

    def _apply_deadband(self, speed_percent: int) -> float:
        """Map user-facing 0..100 speed to actual PWM duty using deadband config."""
        if speed_percent <= 0:
            return 0.0
        lo = max(0.0, min(100.0, float(self.config.min_duty_percent)))
        hi = max(0.0, min(100.0, float(self.config.max_duty_percent)))
        if hi <= lo:
            return hi
        return lo + (speed_percent / 100.0) * (hi - lo)

    def _set_enable(self, side: str, enable: bool) -> None:
        pins = self.config.pins
        pin = pins.l_en if side == self.SIDE_LEFT else pins.r_en
        self._backend.write(pin, 1 if enable else 0)

    def _set_status_led(self, red: bool = False,
                        green: bool = False, blue: bool = False) -> None:
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
            raise RuntimeError("NavigationManager is not initialized. Call initialize() first.")
