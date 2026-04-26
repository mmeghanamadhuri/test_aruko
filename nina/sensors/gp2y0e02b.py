"""Sharp GP2Y0E02B IR distance sensor driver.

The GP2Y0E02B is an I2C distance sensor with a useful range of
4-50 cm. On Nina it's mounted under the front bumper as a cliff /
very-near obstacle sensor: it detects the floor when stationary and
loses the floor at the edge of a stairwell - perfect for emergency
stopping autonomous nav.

I2C details:
  * 7-bit address: 0x40 (default; Sharp ships the part with this fixed)
  * shift register at 0x35 controls the resolution
  * distance register pair at 0x5E / 0x5F:
        distance_cm = ( (0x5E << 4) | (0x5F & 0x0F) ) / 16 / shift_factor
    where shift_factor is 1 for 0xFE = 0x01 (factory default) and 2 for
    0xFE = 0x02. We read the shift register on connect to scale
    correctly.

Default I2C bus is `/dev/i2c-1` on Jetson Nano (40-pin header), bus 0
on the J41 alt header. Override via NINA_IR_I2C_BUS / NINA_IR_I2C_ADDR.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Tuple

from nina.sensors.types import IRReading


log = logging.getLogger("nina.sensors.gp2y0e02b")


DEFAULT_BUS = int(os.environ.get("NINA_IR_I2C_BUS", "1"))
DEFAULT_ADDR = int(os.environ.get("NINA_IR_I2C_ADDR", "0x40"), 0)
DEFAULT_POSITION = os.environ.get("NINA_IR_POSITION", "front_cliff")


def is_available() -> Tuple[bool, str]:
    if os.environ.get("NINA_IR_DISABLE", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return False, "disabled via NINA_IR_DISABLE"
    try:
        import smbus2  # noqa: F401  type: ignore
    except Exception as exc:  # pragma: no cover
        return False, f"smbus2 not installed ({exc})"
    if not os.path.exists(f"/dev/i2c-{DEFAULT_BUS}"):
        return False, f"/dev/i2c-{DEFAULT_BUS} not present"
    return True, ""


class GP2Y0E02B:
    """Polling driver for a single GP2Y0E02B.

    Spawns a background thread that samples at ~10 Hz and stores the
    latest reading. `read()` returns the most recent reading without
    blocking.
    """

    def __init__(
        self,
        bus: int = DEFAULT_BUS,
        address: int = DEFAULT_ADDR,
        position: str = DEFAULT_POSITION,
    ) -> None:
        self._bus_num = bus
        self._addr = address
        self._position = position
        self._bus = None  # smbus2.SMBus | None
        self._shift = 1
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[IRReading] = None
        self._connected = False
        self._message = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            import smbus2  # type: ignore
        except Exception as exc:
            self._message = f"smbus2 not installed ({exc})"
            raise RuntimeError(self._message) from exc

        try:
            self._bus = smbus2.SMBus(self._bus_num)
            shift = self._bus.read_byte_data(self._addr, 0x35) & 0x07
            self._shift = max(1, 1 << shift)
        except Exception as exc:
            self._bus = None
            self._message = f"open i2c {self._bus_num}@0x{self._addr:02X}: {exc}"
            raise RuntimeError(self._message) from exc

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="GP2Y0E02B", daemon=True
        )
        self._thread.start()
        self._connected = True
        self._message = f"i2c {self._bus_num}@0x{self._addr:02X} ready"

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._connected = False
        self._message = "disconnected"

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def read(self) -> Optional[IRReading]:
        with self._lock:
            return self._latest

    def status(self) -> Tuple[bool, str]:
        return self._connected, self._message

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            distance = self._sample()
            with self._lock:
                self._latest = IRReading(
                    position=self._position,
                    distance_mm=distance,
                    timestamp_s=time.monotonic(),
                )
            time.sleep(0.1)

    def _sample(self) -> Optional[int]:
        bus = self._bus
        if bus is None:
            return None
        try:
            high = bus.read_byte_data(self._addr, 0x5E)
            low = bus.read_byte_data(self._addr, 0x5F) & 0x0F
            cm = ((high << 4) | low) / 16.0 / float(self._shift)
            mm = int(cm * 10.0)
            if mm <= 0 or mm > 1500:
                return None
            return mm
        except Exception as exc:
            log.debug("GP2Y0E02B sample failed: %s", exc)
            return None
