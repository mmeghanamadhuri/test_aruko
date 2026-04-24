"""
NavigationManager for Nina (5ft wheeled bot).

Drives 2x JYQD_V7.3E2 BLDC drivers (one per wheel) directly from the Jetson Nano.

Per-side wired lines:
- EL (enable):  digital output
- Signal (F/R direction): digital output
- ZF (brake):   digital output, HIGH = brake
- VR (speed):   PWM input (hardware PWM on BCM 12 / BCM 13)
- GND

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
    """All BCM pin numbers for navigation hardware."""
    l_en: int
    l_dir: int
    l_zf: int
    pwm_l: int
    r_en: int
    r_dir: int
    r_zf: int
    pwm_r: int
    led_red: int
    led_green: int
    led_blue: int
    estop_1: int
    estop_2: int


@dataclass(frozen=True)
class NavigationConfig:
    pins: NavigationPins
    backend_name: str = "jetson"
    pwm_frequency_hz: int = 2000
    default_speed_percent: int = 15
    turn_duration_sec: float = 2.3
    settle_delay_sec: float = 0.1


# Default Nina pinout (BCM numbering on Jetson Nano).
# PWM pins MUST be BCM 12 and BCM 13 to use hardware PWM on Nano.
DEFAULT_PINS = NavigationPins(
    l_en=18,
    l_dir=25,
    l_zf=int(os.environ.get("NINA_NAV_L_ZF", "23")),
    pwm_l=12,
    r_en=10,
    r_dir=22,
    r_zf=int(os.environ.get("NINA_NAV_R_ZF", "24")),
    pwm_r=13,
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
                    pins.l_en, pins.l_dir, pins.l_zf,
                    pins.r_en, pins.r_dir, pins.r_zf):
            self._backend.configure_output(pin)

        self._backend.configure_pwm(pins.pwm_l, self.config.pwm_frequency_hz)
        self._backend.configure_pwm(pins.pwm_r, self.config.pwm_frequency_hz)

        self.release_brake()
        self._is_initialized = True
        log.info("NavigationManager initialized with backend=%s", self._backend.name)

    def shutdown(self) -> None:
        if not self._is_initialized:
            return
        try:
            self.stop()
            self._set_enable(self.SIDE_LEFT, False)
            self._set_enable(self.SIDE_RIGHT, False)
            self.engage_brake()
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
        log.info("forward speed=%s%%", speed)

    def backward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_BACKWARD, speed)
        log.info("backward speed=%s%%", speed)

    def turn_left(self, speed_percent: Optional[int] = None,
                  duration: Optional[float] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(left_dir=self.DIR_BACKWARD, right_dir=self.DIR_FORWARD,
                         speed=speed, duration=duration)
        log.info("turn_left speed=%s%%", speed)

    def turn_right(self, speed_percent: Optional[int] = None,
                   duration: Optional[float] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(left_dir=self.DIR_FORWARD, right_dir=self.DIR_BACKWARD,
                         speed=speed, duration=duration)
        log.info("turn_right speed=%s%%", speed)

    def stop(self) -> None:
        self._control_speed(self.SIDE_LEFT, True, 0, self.DIR_FORWARD)
        self._control_speed(self.SIDE_RIGHT, True, 0, self.DIR_FORWARD)
        time.sleep(self.config.settle_delay_sec)
        log.info("stop")

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
        pins = self.config.pins
        self._backend.write(pins.l_zf, 1)
        self._backend.write(pins.r_zf, 1)
        log.info("brake engaged")

    def release_brake(self) -> None:
        pins = self.config.pins
        self._backend.write(pins.l_zf, 0)
        self._backend.write(pins.r_zf, 0)
        log.info("brake released")

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
        self.release_brake()
        self._control_speed(self.SIDE_LEFT, True, speed, direction)
        self._control_speed(self.SIDE_RIGHT, True, speed, direction)

    def _timed_turn(self, left_dir: str, right_dir: str,
                    speed: int, duration: Optional[float]) -> None:
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self.release_brake()
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
        time.sleep(duration if duration is not None else self.config.turn_duration_sec)
        self.stop()

    def _control_speed(self, side: str, enable: bool,
                       speed_percent: int, direction: str) -> None:
        self._require_initialized()
        if side not in (self.SIDE_LEFT, self.SIDE_RIGHT):
            raise ValueError(f"Invalid side '{side}'")
        if direction not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid direction '{direction}'")

        speed_percent = max(0, min(100, int(speed_percent)))
        pins = self.config.pins

        if side == self.SIDE_LEFT:
            self._backend.write(pins.l_en, 1 if enable else 0)
            self._backend.write(pins.l_dir, 1 if direction == self.DIR_FORWARD else 0)
            self._backend.set_duty(pins.pwm_l, speed_percent)
        else:
            self._backend.write(pins.r_en, 1 if enable else 0)
            self._backend.write(pins.r_dir, 0 if direction == self.DIR_FORWARD else 1)
            self._backend.set_duty(pins.pwm_r, speed_percent)

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
