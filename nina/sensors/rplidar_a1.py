"""SLAMTEC RPLIDAR A1M8 driver wrapper.

The A1M8 is a 360-degree 2D laser scanner connected via USB-serial
(the bundled adapter board exposes a CP2102 / CH340). Stock spec:

  * detection range ~12 m
  * angular resolution ~1 deg (360 samples / rev)
  * scan rate 5.5 Hz (configurable 5 - 10 Hz on the A1M8)
  * baudrate 115200

We use the `rplidar` Python package (Roboticia port of the SLAMTEC
SDK). On dev hosts where the package isn't installed, or the device
file isn't present, the driver gracefully reports unavailability so
the rest of the stack can keep running in simulation mode.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional, Tuple

from nina.sensors.types import LidarScan


log = logging.getLogger("nina.sensors.rplidar")


DEFAULT_PORT = os.environ.get("NINA_LIDAR_PORT", "/dev/ttyUSB0")
DEFAULT_BAUD = int(os.environ.get("NINA_LIDAR_BAUD", "115200"))
DEFAULT_BINS = int(os.environ.get("NINA_LIDAR_BINS", "360"))


def is_available() -> Tuple[bool, str]:
    try:
        import rplidar  # noqa: F401  type: ignore
    except Exception as exc:  # pragma: no cover - depends on host
        return False, f"rplidar package not installed ({exc})"
    if not os.path.exists(DEFAULT_PORT):
        return False, f"{DEFAULT_PORT} not present"
    return True, ""


class RPLidarA1:
    """Background-thread RPLIDAR A1 reader.

    Spawns one thread that pulls scans from `iter_scans()` and stores
    the latest distance array as a `LidarScan`. `read()` returns the
    most recent scan (or None) without blocking.
    """

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUD,
        bins: int = DEFAULT_BINS,
    ) -> None:
        self._port = port
        self._baud = baudrate
        self._bins = max(72, int(bins))     # don't go below 5-deg resolution
        self._lidar = None                  # rplidar.RPLidar | None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[LidarScan] = None
        self._connected = False
        self._message = ""
        self._scans_received = 0
        self._last_scan_at = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            from rplidar import RPLidar  # type: ignore
        except Exception as exc:
            self._message = f"rplidar package not installed ({exc})"
            raise RuntimeError(self._message) from exc

        try:
            self._lidar = RPLidar(self._port, baudrate=self._baud, timeout=2.0)
            # Probe the device. Older rplidar packages don't expose
            # get_info() reliably; the first iter_scans call will surface
            # the actual error if there is one.
            try:
                info = self._lidar.get_info()
                log.info("RPLIDAR info: %s", info)
            except Exception:
                pass
        except Exception as exc:
            self._lidar = None
            self._message = f"open {self._port}: {exc}"
            raise RuntimeError(self._message) from exc

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="RPLidarA1", daemon=True
        )
        self._thread.start()
        self._connected = True
        self._message = f"connected on {self._port}"

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._lidar is not None:
            try:
                self._lidar.stop()
                self._lidar.stop_motor()
                self._lidar.disconnect()
            except Exception as exc:
                log.warning("rplidar close: %s", exc)
            self._lidar = None
        self._connected = False
        self._message = "disconnected"

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def read(self) -> Optional[LidarScan]:
        with self._lock:
            return self._latest

    def status(self) -> Tuple[bool, str]:
        return self._connected, self._message

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        assert self._lidar is not None
        try:
            for scan in self._lidar.iter_scans(min_len=80, max_buf_meas=5000):
                if self._stop_evt.is_set():
                    break
                self._publish(scan)
        except Exception as exc:
            self._message = f"scan loop error: {exc}"
            self._connected = False
            log.warning("rplidar scan loop: %s", exc)

    def _publish(self, raw_scan) -> None:
        bins = self._bins
        bucket = [0] * bins
        valid = 0
        for measurement in raw_scan:
            # iter_scans yields (quality, angle_deg, distance_mm)
            try:
                _q, angle, distance = measurement
            except Exception:
                continue
            if distance <= 0:
                continue
            idx = int(angle / 360.0 * bins) % bins
            existing = bucket[idx]
            distance_int = int(distance)
            if existing == 0 or distance_int < existing:
                bucket[idx] = distance_int
                if existing == 0:
                    valid += 1

        now = time.monotonic()
        rpm = 0.0
        if self._last_scan_at > 0:
            dt = now - self._last_scan_at
            if dt > 0:
                rpm = 60.0 / dt

        scan = LidarScan(
            distances_mm=bucket,
            timestamp_s=now,
            rpm=rpm,
            quality=valid / float(bins),
        )
        with self._lock:
            self._latest = scan
        self._scans_received += 1
        self._last_scan_at = now


def latest_distances(scan: Optional[LidarScan]) -> List[int]:
    """Convenience helper for SLAM / autonomy: returns the distance
    array (or an empty list when the lidar hasn't produced a scan yet)."""
    if scan is None:
        return []
    return list(scan.distances_mm)
