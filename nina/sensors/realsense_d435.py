"""Intel RealSense D435 (82635D435FDK) driver wrapper.

Mounting assumption: the D435 lives on the front of Nina's chassis,
~30 cm above the floor, tilted ~10 degrees down. That gives the
autonomous pilot a forward-cone obstacle layer (table edges, low
furniture, people's feet) that the head-mounted lidar misses, while
leaving the lidar unobstructed for SLAM.

We deliberately publish only an aggregate `DepthFrame` summary
through the worker boundary (forward-min, forward-avg, left-min,
right-min) instead of the raw point cloud - on Jetson Nano that's
what fits in the autonomy budget.

Software requirements:

  * `pyrealsense2` Python package
      - x86 / Mac:  `pip install pyrealsense2`
      - Jetson:     build librealsense from source against the JetPack
                    kernel, then install the matching pyrealsense2
                    wheel, see librealsense/doc/installation_jetson.md

  * a USB 3 port (USB 2 only enables low-rate / low-res depth)

If the dep is missing or no D435 is plugged in, the driver is
unavailable and the autonomy stack runs lidar+ultrasonic only.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Tuple

from nina.sensors.types import DepthFrame


log = logging.getLogger("nina.sensors.realsense")


DEFAULT_WIDTH = int(os.environ.get("NINA_DEPTH_WIDTH", "640"))
DEFAULT_HEIGHT = int(os.environ.get("NINA_DEPTH_HEIGHT", "480"))
DEFAULT_FPS = int(os.environ.get("NINA_DEPTH_FPS", "15"))
DEFAULT_MAX_RANGE_MM = int(os.environ.get("NINA_DEPTH_MAX_MM", "5000"))
DEFAULT_MIN_RANGE_MM = int(os.environ.get("NINA_DEPTH_MIN_MM", "200"))


def is_available() -> Tuple[bool, str]:
    if os.environ.get("NINA_DEPTH_DISABLE", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return False, "disabled via NINA_DEPTH_DISABLE"
    try:
        import pyrealsense2  # noqa: F401  type: ignore
    except Exception as exc:  # pragma: no cover
        return False, f"pyrealsense2 not installed ({exc})"
    return True, ""


class RealSenseD435:
    """Background-thread depth reader for the D435."""

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = DEFAULT_FPS,
    ) -> None:
        self._width = width
        self._height = height
        self._fps = fps
        self._pipeline = None        # rs.pipeline | None
        self._scale_mm = 1000.0      # mm per metre, overridden after start
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[DepthFrame] = None
        self._connected = False
        self._message = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            import pyrealsense2 as rs  # type: ignore
        except Exception as exc:
            self._message = f"pyrealsense2 not installed ({exc})"
            raise RuntimeError(self._message) from exc

        try:
            self._pipeline = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(
                rs.stream.depth, self._width, self._height,
                rs.format.z16, self._fps,
            )
            profile = self._pipeline.start(cfg)
            depth_sensor = profile.get_device().first_depth_sensor()
            depth_scale_m = depth_sensor.get_depth_scale()  # m / unit
            self._scale_mm = depth_scale_m * 1000.0
        except Exception as exc:
            self._pipeline = None
            self._message = f"D435 start failed: {exc}"
            raise RuntimeError(self._message) from exc

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="RealSenseD435", daemon=True
        )
        self._thread.start()
        self._connected = True
        self._message = (
            f"D435 {self._width}x{self._height}@{self._fps}fps"
        )

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                pass
            self._pipeline = None
        self._connected = False
        self._message = "disconnected"

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def read(self) -> Optional[DepthFrame]:
        with self._lock:
            return self._latest

    def status(self) -> Tuple[bool, str]:
        return self._connected, self._message

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            import numpy as np  # type: ignore
        except Exception as exc:
            self._message = f"numpy not available: {exc}"
            self._connected = False
            return

        pipeline = self._pipeline
        if pipeline is None:
            return

        while not self._stop_evt.is_set():
            try:
                frames = pipeline.wait_for_frames(timeout_ms=2000)
                depth = frames.get_depth_frame()
                if not depth:
                    continue
                arr = np.asanyarray(depth.get_data())
            except Exception as exc:
                log.debug("D435 frame error: %s", exc)
                time.sleep(0.05)
                continue

            self._publish(np, arr)

    def _publish(self, np, arr) -> None:
        h, w = arr.shape
        # Forward cone: horizontally centred 1/3 width, vertically the
        # bottom half (cuts out ceiling lights / sky pixels).
        cx0 = w // 3
        cx1 = 2 * w // 3
        cy0 = h // 3
        forward = arr[cy0:, cx0:cx1]
        left = arr[cy0:, : cx0]
        right = arr[cy0:, cx1:]

        forward_min = self._region_min(np, forward)
        forward_avg = self._region_avg(np, forward)
        left_min = self._region_min(np, left)
        right_min = self._region_min(np, right)

        frame = DepthFrame(
            forward_min_mm=forward_min,
            forward_avg_mm=forward_avg,
            left_min_mm=left_min,
            right_min_mm=right_min,
            timestamp_s=time.monotonic(),
            width=w,
            height=h,
        )
        with self._lock:
            self._latest = frame

    def _region_min(self, np, region) -> Optional[int]:
        valid = region[(region > 0)]
        if valid.size == 0:
            return None
        units = int(valid.min())
        mm = int(units * self._scale_mm)
        if mm < DEFAULT_MIN_RANGE_MM or mm > DEFAULT_MAX_RANGE_MM:
            return None
        return mm

    def _region_avg(self, np, region) -> Optional[int]:
        valid = region[(region > 0)]
        if valid.size == 0:
            return None
        mean_units = float(valid.mean())
        mm = int(mean_units * self._scale_mm)
        if mm <= 0 or mm > DEFAULT_MAX_RANGE_MM * 2:
            return None
        return mm
