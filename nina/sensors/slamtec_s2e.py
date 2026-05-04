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

**GIL / GUI freeze:** The published `pyrplidarsdk` wheel binds the
Slamtec SDK without releasing CPython's GIL around `connect()` /
`get_scan_data()`. Those calls block for hundreds of ms per sweep on
blocking ``grabScanDataHq`` timeouts. When the slam reader runs in a
``threading.Thread`` inside the *same* interpreter as Qt, the GIL
serialises everything → the Map / Perception screens appear frozen
for seconds or indefinitely. **Mitigation (default ON):** we run the
SDK in a separate interpreter via ``multiprocessing`` ``spawn``, and
the parent only unpickles scan batches off a Queue (I/O releases the
GIL). Set ``NINA_SLAMTEC_S2E_SUBPROCESS=0`` to force the legacy
in-process driver (useful for embedded debuggers that can't follow a
child process).
"""

from __future__ import annotations

import logging
import math
import multiprocessing
import os
import queue
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


def _use_s2e_subprocess() -> bool:
    """True unless operator opts out with NINA_SLAMTEC_S2E_SUBPROCESS=0.

    Default ON: pyrplidarsdk's blocking C++ calls hold the GIL and freeze
    Qt when the reader shares the interpreter with the GUI.
    """
    v = os.environ.get("NINA_SLAMTEC_S2E_SUBPROCESS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _s2e_scan_child_main(cmd_q, data_q, host: str, port: int) -> None:
    # noqa: D401 - imperative name for multiprocessing spawn pickle
    """Slamtec SDK loop (separate interpreter — does NOT share Qt's GIL)."""
    import queue as std_queue

    try:
        import pyrplidarsdk  # type: ignore
    except Exception as exc:
        data_q.put(("status", "error", f"import pyrplidarsdk: {exc}"))
        return

    drv = pyrplidarsdk.RplidarDriver(ip_address=host, udp_port=port)
    if not drv.connect():
        data_q.put(("status", "error", "connect() returned False"))
        return
    try:
        info = drv.get_device_info()
        if info is not None:
            print(
                f"[SlamtecS2E child] model={info.model} fw={info.firmware_version} "
                f"hw={info.hardware_version} sn={info.serial_number}",
                flush=True,
            )
    except Exception:
        pass
    if not drv.start_scan():
        data_q.put(("status", "error", "start_scan() returned False"))
        return

    data_q.put(("status", "ready"))
    idle = 0.002
    while True:
        try:
            cmd = cmd_q.get_nowait()
            if cmd == "stop":
                break
        except std_queue.Empty:
            pass
        try:
            batch = drv.get_scan_data()
        except Exception as exc:
            try:
                data_q.put_nowait(("exc", str(exc)))
            except Exception:
                pass
            time.sleep(idle)
            continue
        if batch:
            try:
                data_q.put(("batch", batch), timeout=0.25)
            except Exception:
                # Parent saturated — drop frame (better than blocking child).
                pass
        else:
            time.sleep(idle)

    try:
        drv.stop_scan()
    except Exception:
        pass
    try:
        drv.disconnect()
    except Exception:
        pass


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

        self._driver = None  # pyrplidarsdk.RplidarDriver | None (in-process)
        self._subprocess_mode = False
        self._mp_ctx = None
        self._mp_proc: Optional[multiprocessing.Process] = None
        self._mp_cmd_q = None  # multiprocessing.Queue | None
        self._mp_data_q = None
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
        if _use_s2e_subprocess():
            self._open_subprocess()
        else:
            self._open_inprocess()

    def _open_inprocess(self) -> None:
        self._subprocess_mode = False
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

    def _open_subprocess(self) -> None:
        """Run pyrplidarsdk in a spawned interpreter (see module doc)."""
        self._terminate_subprocess()
        self._subprocess_mode = True
        self._driver = None
        self._sweep_acc.clear()
        self._angle_scale_to_deg = None
        try:
            ctx = multiprocessing.get_context("spawn")
        except Exception as exc:
            self._subprocess_mode = False
            self._message = f"multiprocessing spawn unavailable: {exc}"
            raise RuntimeError(self._message) from exc

        self._mp_ctx = ctx
        self._mp_cmd_q = ctx.Queue()
        self._mp_data_q = ctx.Queue(maxsize=8)
        self._mp_proc = ctx.Process(
            target=_s2e_scan_child_main,
            args=(self._mp_cmd_q, self._mp_data_q, self._host, self._udp_port),
            name="SlamtecS2EScan",
            daemon=True,
        )
        self._mp_proc.start()

        deadline = time.monotonic() + 45.0
        err_msg: Optional[str] = None
        while time.monotonic() < deadline:
            try:
                msg = self._mp_data_q.get(timeout=0.5)
            except queue.Empty:
                if self._mp_proc is None or not self._mp_proc.is_alive():
                    code = (
                        self._mp_proc.exitcode
                        if self._mp_proc is not None else None
                    )
                    err_msg = (
                        f"lidar scan process exited early (exitcode={code})"
                    )
                    break
                continue
            if msg[0] == "status":
                if msg[1] == "ready":
                    err_msg = None
                    break
                if msg[1] == "error":
                    err_msg = str(msg[2])
                    break
        else:
            err_msg = "timeout waiting for lidar scan subprocess (45s)"

        if err_msg:
            self._terminate_subprocess()
            self._subprocess_mode = False
            self._message = err_msg
            raise RuntimeError(err_msg)

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run_subprocess,
            name="SlamtecS2E-Parent",
            daemon=True,
        )
        self._thread.start()
        self._connected = True
        self._message = (
            f"connected (subprocess) on udp://{self._host}:{self._udp_port}"
        )

    def _terminate_subprocess(self) -> None:
        q = self._mp_cmd_q
        self._mp_cmd_q = None
        if q is not None:
            try:
                q.put_nowait("stop")
            except Exception:
                pass
        proc = self._mp_proc
        self._mp_proc = None
        self._mp_data_q = None
        self._mp_ctx = None
        if proc is not None:
            proc.join(timeout=3.0)
            if proc.is_alive():
                log.warning("S2E scan child ignored stop; terminating")
                proc.terminate()
                proc.join(timeout=2.0)

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._subprocess_mode:
            self._terminate_subprocess()
            self._subprocess_mode = False
        elif self._driver is not None:
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
        # Cold-start of the S2E motor takes ~0.5-1.5 s after start_scan,
        # during which `grabScanDataHq` returns SL_RESULT_OPERATION_TIMEOUT
        # (which the wrapper raises as a Python exception or returns
        # None for, depending on build). Don't kick the lidar offline
        # on every transient warmup error - count consecutive failures
        # and only give up after FAULT_BUDGET in a row, which means
        # ~5 s of solid silence (FAULT_BUDGET * SDK timeout).
        FAULT_BUDGET = 20
        consecutive_errors = 0
        while not self._stop_evt.is_set():
            try:
                batch = self._driver.get_scan_data()
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors >= FAULT_BUDGET:
                    self._message = (
                        f"scan loop error after {consecutive_errors} "
                        f"consecutive failures: {exc}"
                    )
                    self._connected = False
                    log.warning(
                        "S2E scan loop giving up after %d errors: %s",
                        consecutive_errors, exc,
                    )
                    return
                # Transient: log once, keep going. The SDK prints its
                # own 'Failed to grab scan data' on stderr per call;
                # we don't double up.
                if consecutive_errors == 1:
                    log.debug(
                        "S2E transient grab failure (%s); will retry "
                        "up to %d times before giving up",
                        exc, FAULT_BUDGET,
                    )
                time.sleep(idle_sleep)
                continue

            if not batch:
                # nothing yet; let the lidar fill the queue. Empty
                # batches are benign - the SDK can legitimately return
                # three empty vectors when every point in the buffer
                # was clipped (all-zero ranges, out-of-range, etc.).
                # The SlamWorker above us already surfaces "no scans"
                # as the 'Lidar sim' status pill, so we don't need to
                # tear down the connection on quiet periods.
                time.sleep(idle_sleep)
                continue

            # Got a real batch - clear the warmup-error counter.
            consecutive_errors = 0

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

    def _run_subprocess(self) -> None:
        """Parent-side reader: unpickle scan batches (GIL released on queue I/O)."""
        assert self._mp_data_q is not None
        data_q = self._mp_data_q
        proc = self._mp_proc
        FAULT_BUDGET = 20
        consecutive_errors = 0
        while not self._stop_evt.is_set():
            try:
                msg = data_q.get(timeout=0.35)
            except queue.Empty:
                if proc is not None and not proc.is_alive():
                    self._message = "S2E scan subprocess exited"
                    self._connected = False
                    log.warning("S2E scan subprocess died mid-run")
                    return
                continue
            if msg[0] == "exc":
                consecutive_errors += 1
                if consecutive_errors >= FAULT_BUDGET:
                    self._message = (
                        f"S2E scan subprocess fault ({consecutive_errors}x): "
                        f"{msg[1]}"
                    )
                    self._connected = False
                    log.warning(self._message)
                    return
                if consecutive_errors == 1:
                    log.debug(
                        "S2E subprocess grab error (%s); retrying", msg[1]
                    )
                time.sleep(0.005)
                continue
            if msg[0] != "batch":
                continue
            consecutive_errors = 0
            batch = msg[1]
            try:
                angles, ranges, qualities = batch
            except Exception:
                if isinstance(batch, list) and batch and len(batch[0]) >= 2:
                    angles = [m[0] for m in batch]
                    ranges = [m[1] for m in batch]
                    qualities = [
                        m[2] if len(m) > 2 else 0 for m in batch
                    ]
                else:
                    log.warning(
                        "S2E: unexpected scan_data shape from child: %r",
                        type(batch),
                    )
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
