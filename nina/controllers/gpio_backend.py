"""
GPIO backend abstraction for Nina navigation.

Two implementations are provided:
- JetsonBackend: uses Jetson.GPIO with BCM numbering and hardware PWM on
  pins 32 (BCM 12 / PWM0) and 33 (BCM 13 / PWM2). Default for Nina on
  Jetson Orin Nano. Both PWM pins must be enabled once via:
      sudo /opt/nvidia/jetson-io/jetson-io.py
- PigpioBackend: uses pigpio. The original Sirena reference build
  ("nina/app/navigation_bldc.py" prototype + the "/Downloads"
  motor_control.py / navigation_bldc.py pair) used pigpio on a
  Raspberry Pi; this backend lets the same code run on a Pi for A/B
  testing.

The backend is intentionally minimal: digital write, PWM init, PWM duty update,
and shutdown. NavigationManager owns all logic on top of it.
"""

import logging
import os
import warnings
from typing import Any, Dict, Optional, Protocol


log = logging.getLogger("nina.gpio")


class GpioBackend(Protocol):
    name: str

    def setup(self) -> None: ...

    def configure_output(self, pin: int) -> None: ...

    def write(self, pin: int, value: int) -> None: ...

    def configure_pwm(self, pin: int, frequency_hz: int) -> None: ...

    def set_duty(self, pin: int, duty_percent: float) -> None: ...

    def shutdown(self) -> None: ...


class JetsonBackend:
    """
    Jetson.GPIO backend using BCM numbering.

    Hardware PWM is only available on physical pins 32 and 33
    (BCM 12 / PWM0, BCM 13 / PWM2). These must be enabled once via:
        sudo /opt/nvidia/jetson-io/jetson-io.py
    -> "Configure Jetson 40-pin Header"
    -> "Configure header pins manually"
    -> enable both `pwm0` (pin 32) and `pwm2` (pin 33), reboot.
    """

    name = "jetson"

    def __init__(self) -> None:
        self._gpio = None
        self._pwm: Dict[int, Any] = {}
        self._pwm_freq: Dict[int, int] = {}

    def setup(self) -> None:
        # Hint Jetson.GPIO about the SoC so it skips its carrier-board check
        # (which prints scary warnings on third-party / custom carrier boards).
        # Default is Orin Nano because that is the only Jetson Nina ships on;
        # override with NINA_JETSON_MODEL=JETSON_NANO if you ever run this on
        # the older T210 Nano dev kit.
        os.environ.setdefault(
            "JETSON_MODEL_NAME",
            os.environ.get("NINA_JETSON_MODEL", "JETSON_ORIN_NANO"),
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                import Jetson.GPIO as GPIO  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "Jetson.GPIO is required on Jetson Nano. Install with: pip install Jetson.GPIO"
                ) from exc

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        self._gpio = GPIO
        log.info("Jetson.GPIO initialized with BCM numbering")

    def configure_output(self, pin: int) -> None:
        self._require_setup()
        self._gpio.setup(pin, self._gpio.OUT, initial=self._gpio.LOW)

    def write(self, pin: int, value: int) -> None:
        self._require_setup()
        self._gpio.output(pin, self._gpio.HIGH if value else self._gpio.LOW)

    def configure_pwm(self, pin: int, frequency_hz: int) -> None:
        self._require_setup()
        if pin in self._pwm:
            return
        self._gpio.setup(pin, self._gpio.OUT, initial=self._gpio.LOW)
        try:
            pwm = self._gpio.PWM(pin, frequency_hz)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to start hardware PWM on BCM {pin}. "
                "Hardware PWM is only available on BCM 12 (pin 32) and BCM 13 (pin 33). "
                "Enable PWM via: sudo /opt/nvidia/jetson-io/jetson-io.py"
            ) from exc
        pwm.start(0.0)
        self._pwm[pin] = pwm
        self._pwm_freq[pin] = frequency_hz

    def set_duty(self, pin: int, duty_percent: float) -> None:
        self._require_setup()
        if pin not in self._pwm:
            raise RuntimeError(f"PWM pin {pin} is not configured. Call configure_pwm first.")
        duty = max(0.0, min(100.0, float(duty_percent)))
        self._pwm[pin].ChangeDutyCycle(duty)

    def shutdown(self) -> None:
        for pin, pwm in self._pwm.items():
            try:
                pwm.stop()
            except Exception:
                log.warning("Failed to stop PWM on pin %s", pin)
        self._pwm.clear()
        self._pwm_freq.clear()
        if self._gpio is not None:
            try:
                self._gpio.cleanup()
            except Exception:
                log.warning("Jetson.GPIO cleanup raised; ignoring during shutdown")
            self._gpio = None
        log.info("Jetson.GPIO shutdown complete")

    def _require_setup(self) -> None:
        if self._gpio is None:
            raise RuntimeError("Jetson.GPIO backend is not initialized. Call setup() first.")


class PigpioBackend:
    """
    pigpio backend (Raspberry Pi only). Useful as a fallback for non-Jetson hosts.
    """

    name = "pigpio"

    def __init__(self) -> None:
        self._pigpio = None
        self._pi: Optional[Any] = None
        self._pwm_freq: Dict[int, int] = {}

    def setup(self) -> None:
        try:
            import pigpio  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pigpio is required for the pigpio backend. Install with: pip install pigpio"
            ) from exc
        self._pigpio = pigpio
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError("Could not connect to pigpiod. Start it with: sudo systemctl start pigpiod")
        log.info("pigpio backend initialized")

    def configure_output(self, pin: int) -> None:
        self._require_setup()
        self._pi.set_mode(pin, self._pigpio.OUTPUT)

    def write(self, pin: int, value: int) -> None:
        self._require_setup()
        self._pi.write(pin, 1 if value else 0)

    def configure_pwm(self, pin: int, frequency_hz: int) -> None:
        self._require_setup()
        self._pwm_freq[pin] = frequency_hz
        self._pi.hardware_PWM(pin, frequency_hz, 0)

    def set_duty(self, pin: int, duty_percent: float) -> None:
        self._require_setup()
        freq = self._pwm_freq.get(pin)
        if freq is None:
            raise RuntimeError(f"PWM pin {pin} is not configured. Call configure_pwm first.")
        duty = max(0.0, min(100.0, float(duty_percent)))
        self._pi.hardware_PWM(pin, freq, int(duty * 10000))

    def shutdown(self) -> None:
        if self._pi is not None:
            try:
                self._pi.stop()
            except Exception:
                log.warning("pigpio stop() raised; ignoring during shutdown")
            self._pi = None
        log.info("pigpio backend shutdown complete")

    def _require_setup(self) -> None:
        if self._pi is None:
            raise RuntimeError("pigpio backend is not initialized. Call setup() first.")


def create_backend(name: str) -> GpioBackend:
    name = (name or "jetson").strip().lower()
    if name == "jetson":
        return JetsonBackend()
    if name == "pigpio":
        return PigpioBackend()
    raise ValueError(f"Unknown navigation backend '{name}'. Expected 'jetson' or 'pigpio'.")
