"""SLAMTEC RPLIDAR S2E (Ethernet) driver wrapper.

The S2E is the **Ethernet variant** of the Slamtec S2 dToF lidar. It
ships from the factory listening for the SLAMTEC standard
communication protocol over **UDP**, default IP **192.168.11.2** /
port **8089**, with these published specs:

  * detection range up to ~30 m (vs ~12 m on the A1M8)
  * sample rate ~32 kHz (vs ~8 kHz on the A1M8)
  * scan rate 10-15 Hz (configurable)
  * IP65, dToF (immune to ambient light, works outdoors)

We talk to the device through `pyrplidarsdk`, a thin nanobind wrapper
around Slamtec's official C++ rplidar_sdk. That SDK is what
`rplidar_sdk` / `sllidar_ros2` use under the hood, so it's the only
Python option that reliably supports BOTH the S-series ultra-capsule
scan packet format AND the UDP transport. The legacy `rplidar` package
(used for the A1M8) speaks serial only and predates the S-series
protocol.

On dev hosts where `pyrplidarsdk` isn't installed or the device isn't
reachable, the driver gracefully reports unavailability so the rest
of the stack keeps running in simulation mode.

Bring-up gotchas worth knowing about (see also
`scripts/install-slamtec-s2e-jetson.sh`):

  * The Jetson's Ethernet interface MUST be in the lidar's subnet.
    Default lidar IP is 192.168.11.2; we use 192.168.11.10 for the
    Jetson side. `ping 192.168.11.2` is the first thing to verify.
  * UDP port 8089 must be reachable - the install script opens it in
    the local firewall (ufw) when present.
  * The S2E expects 12 V via its barrel jack; USB will not power it.
  * `pyrplidarsdk` builds a C extension on install; on Jetson aarch64
    the wheel is provided but you still need `python3-dev` for the
    nanobind build path the install script falls back to.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import List, Optional, Tuple

from nina.sensors.types import LidarScan


log = logging.getLogger("nina.sensors.slamtec_s2e")


DEFAULT_HOST = os.environ.get("NINA_LIDAR_HOST", "192.168.11.2")
DEFAULT_UDP_PORT = int(os.environ.get("NINA_LIDAR_UDP_PORT", "8089"))
DEFAULT_BINS = int(os.environ.get("NINA_LIDAR_BINS", "400"))
# Most environments saturate the S2E's 30 m range with multipath /
# wall-bounce returns past ~25 m indoors. Clip past 28 m so a phantom
# "reflection of a reflection" doesn't open the SLAM map up by half a
# building. Override via NINA_LIDAR_MAX_RANGE_MM if you actually want
# the full 30 m envelope (outdoor loops, large warehouses).
DEFAULT_MAX_RANGE_MM = int(os.environ.get("NINA_LIDAR_MAX_RANGE_MM", "28000"))
# Below this we treat returns as the lidar seeing its own housing /
# the bot's own structure. The S2E's blind zone is ~50 mm; 100 mm is
# a comfortable margin that still lets us see things right at the
# bumper.
DEFAULT_MIN_RANGE_MM = int(os.environ.get("NINA_LIDAR_MIN_RANGE_MM", "100"))


def _import_sdk():
    """Import pyrplidarsdk lazily so dev hosts don't pay the import
    cost just to find out they don't have it installed."""
    import pyrplidarsdk  # type: ignore
    return pyrplidarsdk


def is_available() -> Tuple[bool, str]:
    """Cheap probe: does the package import and is the device pingable?

    We don't open a UDP session here - that would block the GUI for up
    to 5 s on a missing lidar. The SlamWorker calls `open()` from its
    background thread so a real connection attempt happens off the UI
    main loop.
    """
    try:
        _import_sdk()
    except Exception as exc:  # pragma: no cover - depends on host
        return False, (
            f"pyrplidarsdk not installed ({exc}). Run "
            "scripts/install-slamtec-s2e-jetson.sh on the Jetson."
        )
    return True, ""


class SlamtecS2E:
    """Background-thread Slamtec S2E reader (UDP transport).

    Spawns one worker thread that calls `get_scan_data()` in a tight
    loop and stores the latest distance array as a `LidarScan`.
    `read()` returns the most recent scan (or None) without blocking.

    The driver is connection-keepalive aware: if the UDP session
    drops (lidar power-cycle, cable yank), the worker logs the error,
    flips the connected flag back to False, and exits. The SlamWorker
    above us will surface that as "Lidar offline" in the UI status
    pill, and the next operator-initiated open() retries the
    connection.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        udp_port: int = DEFAULT_UDP_PORT,
        bins: int = DEFAULT_BINS,
        max_range_mm: int = DEFAULT_MAX_RANGE_MM,
        min_range_mm: int = DEFAULT_MIN_RANGE_MM,
    ) -> None:
        self._host = host
        self._udp_port = int(udp_port)
        # Don't go below ~5 deg resolution even if the operator asks
        # for less; downstream BreezySLAM resampling assumes >= 72.
        self._bins = max(72, int(bins))
        self._max_range_mm = int(max_range_mm)
        self._min_range_mm = int(min_range_mm)

        self._driver = None  # pyrplidarsdk.RplidarDriver | None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[LidarScan] = None
        self._connected = False
        self._message = ""
        self._scans_received = 0
        self._last_scan_at = 0.0
        # We accumulate measurements until a sweep is complete (the
        # lidar publishes points at ~32 kHz; we slice them into 1-rev
        # buckets here to feed BreezySLAM with whole sweeps).
        self._sweep_acc: List[Tuple[float, float, int]] = []
        self._sweep_started_at: float = 0.0
        # Auto-detected on the first batch (some SDK builds publish
        # degrees, some radians). Stays None until we've seen one
        # batch; after that it's a multiplier from SDK-units to
        # degrees (1.0 or 180/pi).
        self._angle_scale_to_deg: Optional[float] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            sdk = _import_sdk()
        except Exception as exc:
            self._message = (
                f"pyrplidarsdk not installed ({exc}); "
                "run scripts/install-slamtec-s2e-jetson.sh"
            )
            raise RuntimeError(self._message) from exc

        try:
            self._driver = sdk.RplidarDriver(
                ip_address=self._host,
                udp_port=self._udp_port,
            )
            if not self._driver.connect():
                raise RuntimeError(
                    f"connect() returned False - is {self._host}:"
                    f"{self._udp_port} reachable? Try "
                    f"`ping {self._host}` from the Jetson."
                )
            try:
                info = self._driver.get_device_info()
                if info is not None:
                    log.info(
                        "Slamtec lidar info: model=%s fw=%s hw=%s sn=%s",
                        info.model, info.firmware_version,
                        info.hardware_version, info.serial_number,
                    )
            except Exception:
                # get_device_info() is advisory; pressing on without
                # it is safe.
                pass
            try:
                health = self._driver.get_health()
                if health is not None and getattr(health, "status", 0) != 0:
                    log.warning(
                        "S2E health non-zero (status=%s, error=%s); "
                        "send a reset cycle if scans look corrupt",
                        health.status, getattr(health, "error_code", "?"),
                    )
            except Exception:
                pass
            if not self._driver.start_scan():
                raise RuntimeError("start_scan() returned False")
        except Exception as exc:
            self._driver = None
            self._message = f"open {self._host}:{self._udp_port}: {exc}"
            raise RuntimeError(self._message) from exc

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="SlamtecS2E", daemon=True
        )
        self._thread.start()
        self._connected = True
        self._message = (
            f"connected on udp://{self._host}:{self._udp_port}"
        )

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._driver is not None:
            try:
                self._driver.stop_scan()
            except Exception as exc:
                log.warning("S2E stop_scan: %s", exc)
            try:
                self._driver.disconnect()
            except Exception as exc:
                log.warning("S2E disconnect: %s", exc)
            self._driver = None
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
        assert self._driver is not None
        # Empty-batch backoff so we don't spin the CPU when the lidar
        # is mid-sweep and the SDK's get_scan_data() returns 0 points.
        idle_sleep = 0.005
        while not self._stop_evt.is_set():
            try:
                batch = self._driver.get_scan_data()
            except Exception as exc:
                self._message = f"scan loop error: {exc}"
                self._connected = False
                log.warning("S2E scan loop: %s", exc)
                return

            if not batch:
                # nothing yet; let the lidar fill the queue
                time.sleep(idle_sleep)
                continue

            try:
                angles, ranges, qualities = batch
            except Exception:
                # Defensive: some SDK builds return a list of tuples
                # instead of three parallel arrays. Adapt.
                if isinstance(batch, list) and batch and len(batch[0]) >= 2:
                    angles = [m[0] for m in batch]
                    ranges = [m[1] for m in batch]
                    qualities = [
                        m[2] if len(m) > 2 else 0 for m in batch
                    ]
                else:
                    log.warning(
                        "S2E: unexpected scan_data shape: %r",
                        type(batch),
                    )
                    time.sleep(idle_sleep)
                    continue

            self._ingest(angles, ranges, qualities)

    def _ingest(self, angles, ranges, qualities) -> None:
        """Slice the SDK's continuous point stream into one-rev sweeps.

        `pyrplidarsdk.get_scan_data()` returns whatever points the SDK
        has buffered at the moment of the call - it doesn't align on
        sweep boundaries. We watch for a wraparound in the angle
        stream and flush a complete sweep into a `LidarScan` every
        time we see one.

        The SDK's docs leave the angle unit slightly ambiguous (some
        builds publish degrees, some radians). We auto-detect on the
        first batch by looking at the angle envelope: anything <= 2π
        is treated as radians, anything in (2π, 360+] as degrees.
        Once locked in we keep the same unit for the lifetime of the
        connection.
        """
        if not angles:
            return
        if self._angle_scale_to_deg is None:
            try:
                amax = max(float(a) for a in angles if a is not None)
            except Exception:
                amax = 0.0
            if amax <= (2.0 * math.pi + 0.1):
                # SDK is publishing radians.
                self._angle_scale_to_deg = 180.0 / math.pi
                log.debug("S2E: SDK angles are radians; scaling to degrees")
            else:
                self._angle_scale_to_deg = 1.0
                log.debug("S2E: SDK angles are degrees")

        scale = self._angle_scale_to_deg
        prev_angle: Optional[float] = self._sweep_acc[-1][0] if self._sweep_acc else None
        if not self._sweep_started_at:
            self._sweep_started_at = time.monotonic()

        for a, r, q in zip(angles, ranges, qualities):
            try:
                a = float(a) * scale
                r = float(r)
            except Exception:
                continue
            a = a % 360.0
            # pyrplidarsdk reports ranges in metres; normalise to mm
            # and clip to the configured envelope. Convert NaN / 0
            # to "no return" (BreezySLAM convention).
            if not math.isfinite(r) or r <= 0:
                dist_mm = 0
            else:
                dist_mm = int(round(r * 1000.0))
                if dist_mm < self._min_range_mm:
                    dist_mm = 0
                elif dist_mm > self._max_range_mm:
                    dist_mm = 0
            # Wraparound detection: angle stream is monotone within
            # a sweep; a sudden drop > 5 deg means we crossed 360
            # -> 0 and a sweep boundary is here.
            if prev_angle is not None and a + 5.0 < prev_angle:
                self._publish_sweep()
                self._sweep_acc.clear()
                self._sweep_started_at = time.monotonic()
            self._sweep_acc.append((a, r, dist_mm))
            prev_angle = a

    def _publish_sweep(self) -> None:
        if not self._sweep_acc:
            return
        bins = self._bins
        bucket = [0] * bins
        valid = 0
        for a, _r, dist_mm in self._sweep_acc:
            if dist_mm <= 0:
                continue
            idx = int(a / 360.0 * bins) % bins
            existing = bucket[idx]
            if existing == 0 or dist_mm < existing:
                bucket[idx] = dist_mm
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
    array (or an empty list when the lidar hasn't produced a scan yet).
    """
    if scan is None:
        return []
    return list(scan.distances_mm)
