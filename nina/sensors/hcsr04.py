"""HC-SR04 ultrasonic ranging array driver.

The HC-SR04 needs microsecond-precision GPIO timing for the trigger
pulse and echo measurement. The shared `gpio_backend.py` is too
high-level for that, so we talk to `Jetson.GPIO` directly here. On
non-Jetson hosts (developer Macs / Linux desktops) the driver is
unavailable in a clean way and the autonomy stack runs in simulation.

Default mounting on Nina (BCM pin numbers, override via env vars):

    front_left   trig=BCM19  echo=BCM 9
    front_right  trig=BCM 7  echo=BCM 8
    rear_left    trig=BCM11  echo=BCM 4
    rear_right   trig=BCM 6  echo=BCM26

These pin choices avoid the navigation pins for Nina's Orin Nano
harness (see `nina.controllers.navigation_manager` for why a few of
these differ from the RPi reference - some pins are claimed by the
Orin Nano device tree or are unusable as plain GPIO on this carrier):
    - JYQD enable / EL :  BCM 24 (L), BCM 10 (R)
    - JYQD direction   :  BCM 25 (L), BCM 23 (R)
    - JYQD speed / VR  :  BCM 12 (L, hardware PWM0),
                          BCM 13 (R, hardware PWM2)
    - Status LEDs      :  BCM 16, 20, 21
    - E-stop           :  BCM 5, 17

The JYQD "Signal" screw is intentionally NOT driven on this build (the
RPi reference proves the chip commutates fine with Signal floating), so
no GPIO is reserved for it.

These defaults also avoid the Dynamixel UART (BCM 14 / 15) and the I2C
bus the GP2Y0E02B IR cliff sensor uses (BCM 2 / 3). Set env
`NINA_HCSR04_DISABLE=1` to skip the array entirely.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from nina.sensors.types import UltrasonicReading


log = logging.getLogger("nina.sensors.hcsr04")


SOUND_SPEED_MM_PER_S = 343_000.0
PULSE_TIMEOUT_S = 0.030       # ~5 m round-trip
TRIGGER_PULSE_S = 1e-5        # 10 us trigger pulse


@dataclass(frozen=True)
class _Channel:
    position: str
    trig: int
    echo: int


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else default
    except Exception:
        return default


_DEFAULT_CHANNELS: Tuple[_Channel, ...] = (
    _Channel(
        position="front_left",
        # BCM 19 (physical pin 35) for trig and BCM 9 (physical pin 21)
        # for echo - both free GPIOs clear of the JYQD navigation pins
        # in the RPi-mirror baseline (BCM 10/12/13/18/23/25).
        trig=_env_int("NINA_HCSR04_FL_TRIG", 19),
        echo=_env_int("NINA_HCSR04_FL_ECHO", 9),
    ),
    _Channel(
        position="front_right",
        trig=_env_int("NINA_HCSR04_FR_TRIG", 7),
        echo=_env_int("NINA_HCSR04_FR_ECHO", 8),
    ),
    _Channel(
        position="rear_left",
        # BCM 11 (physical pin 23) - free GPIO clear of the navigation pins.
        trig=_env_int("NINA_HCSR04_RL_TRIG", 11),
        echo=_env_int("NINA_HCSR04_RL_ECHO", 4),
    ),
    _Channel(
        position="rear_right",
        trig=_env_int("NINA_HCSR04_RR_TRIG", 6),
        echo=_env_int("NINA_HCSR04_RR_ECHO", 26),
    ),
)


def is_available() -> Tuple[bool, str]:
    if os.environ.get("NINA_HCSR04_DISABLE", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return False, "disabled via NINA_HCSR04_DISABLE"
    try:
        import Jetson.GPIO  # noqa: F401  type: ignore
    except Exception as exc:  # pragma: no cover
        return False, f"Jetson.GPIO not installed ({exc})"
    return True, ""


class HCSR04Array:
    """Polling driver for an array of HC-SR04 sensors.

    Each sensor is fired in turn (~30 ms timeout each) on a background
    thread, so a 4-sensor ring updates at ~8 Hz - more than fast enough
    for reactive obstacle avoidance.
    """

    def __init__(self, channels: Optional[List[_Channel]] = None) -> None:
        self._channels = list(channels) if channels else list(_DEFAULT_CHANNELS)
        self._gpio = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._readings: Dict[str, UltrasonicReading] = {}
        self._connected = False
        self._message = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            import Jetson.GPIO as GPIO  # type: ignore
        except Exception as exc:
            self._message = f"Jetson.GPIO not installed ({exc})"
            raise RuntimeError(self._message) from exc

        # Must match gpio_backend.py / pin_probe.py - Jetson.GPIO maps
        # BCM numbers to physical pins via a per-model SoC pad table,
        # and the wrong model silently routes writes to a different pad.
        # Override with NINA_JETSON_MODEL=JETSON_NANO only on the older
        # T210 dev kit.
        os.environ.setdefault(
            "JETSON_MODEL_NAME",
            os.environ.get("NINA_JETSON_MODEL", "JETSON_ORIN_NANO"),
        )
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for ch in self._channels:
                GPIO.setup(ch.trig, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(ch.echo, GPIO.IN)
        except Exception as exc:
            self._message = f"setup failed: {exc}"
            raise RuntimeError(self._message) from exc

        self._gpio = GPIO
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="HCSR04Array", daemon=True
        )
        self._thread.start()
        self._connected = True
        self._message = f"{len(self._channels)} HC-SR04 channels online"

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._gpio is not None:
            try:
                for ch in self._channels:
                    self._gpio.output(ch.trig, self._gpio.LOW)
            except Exception:
                pass
            # We DON'T call cleanup() here - the navigation manager owns
            # the global cleanup so we don't yank its PWM pins.
            self._gpio = None
        self._connected = False
        self._message = "disconnected"

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def read_all(self) -> List[UltrasonicReading]:
        with self._lock:
            return [self._readings[ch.position] for ch in self._channels
                    if ch.position in self._readings]

    def read(self, position: str) -> Optional[UltrasonicReading]:
        with self._lock:
            return self._readings.get(position)

    def status(self) -> List[Tuple[str, bool, str]]:
        out: List[Tuple[str, bool, str]] = []
        with self._lock:
            for ch in self._channels:
                r = self._readings.get(ch.position)
                if r is None:
                    out.append((ch.position, False, "no reading yet"))
                elif r.distance_mm is None:
                    out.append((ch.position, False, "echo timeout"))
                else:
                    out.append((ch.position, True, f"{r.distance_mm} mm"))
        return out

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            for ch in self._channels:
                if self._stop_evt.is_set():
                    break
                distance = self._ping(ch)
                with self._lock:
                    self._readings[ch.position] = UltrasonicReading(
                        position=ch.position,
                        distance_mm=distance,
                        timestamp_s=time.monotonic(),
                    )
                # Mandatory inter-ping silence so adjacent sensors don't
                # hear each other's echoes.
                time.sleep(0.04)

    def _ping(self, ch: _Channel) -> Optional[int]:
        gpio = self._gpio
        if gpio is None:
            return None
        try:
            gpio.output(ch.trig, gpio.LOW)
            time.sleep(2e-6)
            gpio.output(ch.trig, gpio.HIGH)
            time.sleep(TRIGGER_PULSE_S)
            gpio.output(ch.trig, gpio.LOW)

            t_start = time.monotonic()
            t_deadline = t_start + PULSE_TIMEOUT_S

            while gpio.input(ch.echo) == 0:
                if time.monotonic() > t_deadline:
                    return None
            t_rise = time.monotonic()

            while gpio.input(ch.echo) == 1:
                if time.monotonic() > t_deadline:
                    return None
            t_fall = time.monotonic()

            duration = t_fall - t_rise
            mm = int(duration * SOUND_SPEED_MM_PER_S / 2)
            if mm <= 0 or mm > 5000:
                return None
            return mm
        except Exception as exc:
            log.debug("HC-SR04 %s ping failed: %s", ch.position, exc)
            return None
