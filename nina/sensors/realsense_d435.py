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

# Vertical band of the depth image used for the obstacle-cone summary.
# Defaults skip the top 25% (sky / ceiling lights, which the lidar
# can't see and which the autonomy doesn't care about) AND the bottom
# 35% (the floor right in front of the bot, which a forward-tilted
# D435 reads at ~480 mm and which would otherwise be fused as a
# permanent forward obstacle - the bot would spin in place forever
# while reading floor as 'wall ahead'). The middle band keeps
# standing obstacles (people, walls, table edges) visible while
# letting the bot actually drive forward in an open room.
#
# Operators with a different mount geometry (camera lower / tilted
# more or less) can override these. A camera mounted at face height
# tilted 0 deg should set NINA_DEPTH_BOT_SKIP_PCT=10 (let the bottom
# rows back in - they no longer see the floor right in front).
DEFAULT_TOP_SKIP_PCT = int(os.environ.get("NINA_DEPTH_TOP_SKIP_PCT", "25"))
DEFAULT_BOT_SKIP_PCT = int(os.environ.get("NINA_DEPTH_BOT_SKIP_PCT", "35"))


def _import_pyrealsense2():
    """Return the pyrealsense2 module that actually has the C bindings.

    librealsense ships its Python package in two layouts depending on
    how it was built / packaged:

    1. **Flat / re-exported**:
        site-packages/pyrealsense2.cpython-XX.so
            -OR-
        site-packages/pyrealsense2/__init__.py  (does
            `from .pyrealsense2 import *`)
        In this layout, `import pyrealsense2 as rs` gives you
        `rs.pipeline`, `rs.context`, `rs.stream`, ... directly.

    2. **Submodule-only (cmake BUILD_PYTHON_BINDINGS default on
       newer librealsense, e.g. v2.55+)**:
        site-packages/pyrealsense2/__init__.py        (empty / minimal)
        site-packages/pyrealsense2/pyrealsense2.cpython-XX.so
        In this layout, `import pyrealsense2 as rs` gives you a near-
        empty package; the actual C symbols live at
        `pyrealsense2.pyrealsense2`. Code that does `rs.pipeline()`
        crashes with AttributeError.

    Our Jetson installer (scripts/install-realsense-jetson.sh) hits
    layout (2). Rather than patch every install in the field, we
    detect both at import time and return whichever one exposes the
    expected `pipeline` symbol. The two callers below
    (`is_available()` and `RealSenseD435.open()`) use this helper so
    the rest of the file stays one-import-line clean.
    """
    import importlib

    candidates = ("pyrealsense2.pyrealsense2", "pyrealsense2")
    last_exc: Optional[Exception] = None
    for name in candidates:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:
            last_exc = exc
            continue
        if hasattr(mod, "pipeline") and hasattr(mod, "config"):
            return mod
    if last_exc is not None:
        raise last_exc
    raise ImportError(
        "pyrealsense2 imported but neither the top-level package nor "
        "the .pyrealsense2 submodule exposes the C bindings - check "
        "the install (the package's __init__.py probably needs "
        "`from .pyrealsense2 import *`)."
    )


def is_available() -> Tuple[bool, str]:
    if os.environ.get("NINA_DEPTH_DISABLE", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return False, "disabled via NINA_DEPTH_DISABLE"
    try:
        _import_pyrealsense2()
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
        # Colorized BGR888 image of the most recent depth frame, kept
        # opt-in via `set_color_publish(True)` so the per-frame numpy /
        # cv2 colorize cost is only paid when a UI is actually
        # subscribed (Perception screen). Stored as
        # (width, height, bgr_bytes) so the consumer can hand the raw
        # buffer straight to QImage(Format_BGR888) without copying.
        self._color_publish_enabled = False
        self._latest_color: Optional[Tuple[int, int, bytes]] = None
        # Set to True the first time we successfully colorize, used to
        # explain "colorize disabled" vs "cv2 missing" in the message.
        self._cv2_warned = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            rs = _import_pyrealsense2()
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
    # Visualization opt-in
    # ------------------------------------------------------------------

    def set_color_publish(self, enabled: bool) -> None:
        """Toggle on-the-fly JET colorization of the depth stream.

        Off by default - only the Perception screen flips this on while
        it's the visible screen so the autonomy hot path doesn't pay
        the ~5-10 ms / frame cv2.applyColorMap cost when nobody is
        watching. Disabling also clears the cached frame so a stale
        thumbnail can't stay on screen if the consumer forgot to call
        latest_color_image() one last time before navigating away.
        """
        enabled = bool(enabled)
        with self._lock:
            self._color_publish_enabled = enabled
            if not enabled:
                self._latest_color = None

    def latest_color_image(self) -> Optional[Tuple[int, int, bytes]]:
        """Return (width, height, BGR888-bytes) for the most recent
        colorized depth frame, or None if colorization is off / no
        frame has been published yet / cv2 is unavailable.

        Caller turns this into a QImage with
        `QImage(buf, w, h, w*3, QImage.Format_BGR888)`. The bytes
        object is reference-counted by Python so QImage's "the buffer
        must outlive the QImage" requirement is satisfied as long as
        the caller holds a reference to the tuple while the QImage is
        in use.
        """
        with self._lock:
            return self._latest_color

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
        # Forward / left / right cones: horizontally split the image
        # into thirds, vertically use the MIDDLE band (skip both the
        # sky/ceiling at the top AND the floor at the bottom). The
        # floor mask is the critical piece: a D435 mounted ~30 cm up
        # tilted ~10 deg down has the floor right in front of the bot
        # at the bottom of every frame, ~480 mm away. Without
        # skipping those rows, every frame reports ~480 mm forward
        # and the autonomy stack reads that as 'forward blocked' and
        # spins on the spot forever.
        cx0 = w // 3
        cx1 = 2 * w // 3
        cy0 = max(0, min(h - 1, int(h * DEFAULT_TOP_SKIP_PCT / 100)))
        cy1 = max(cy0 + 1, min(h, int(h * (100 - DEFAULT_BOT_SKIP_PCT) / 100)))
        forward = arr[cy0:cy1, cx0:cx1]
        left = arr[cy0:cy1, : cx0]
        right = arr[cy0:cy1, cx1:]

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

        color: Optional[Tuple[int, int, bytes]] = None
        # Snapshot the toggle under the lock so a Perception-screen
        # set_color_publish(False) racing the worker thread can't make
        # us colorize and then immediately throw the result away.
        with self._lock:
            want_color = self._color_publish_enabled
        if want_color:
            color = self._colorize(np, arr)

        with self._lock:
            self._latest = frame
            if color is not None:
                self._latest_color = color
            elif not self._color_publish_enabled:
                # Toggle was flipped off mid-frame; respect that.
                self._latest_color = None

    def _colorize(self, np, arr) -> Optional[Tuple[int, int, bytes]]:
        """Build a JET-coloured BGR888 image of the depth array.

        - Pixels outside [DEFAULT_MIN_RANGE_MM, DEFAULT_MAX_RANGE_MM]
          (and the literal 0 = "no return" sentinel) are forced to
          black, so the operator can visually tell "no data" apart
          from "very close" (which is the JET red end).
        - Everything in-range is normalised to 0..255 across the
          configured envelope (NOT per-frame min/max) so the colour of
          a wall at 1.5 m doesn't shift between frames.

        Returns None if cv2 is unavailable - in that case the worker
        keeps publishing DepthFrame summaries to the autonomy stack
        without paying any visualization cost. We log the missing-cv2
        condition exactly once so launch.log doesn't drown.
        """
        try:
            import cv2  # type: ignore
        except Exception as exc:
            if not self._cv2_warned:
                log.info(
                    "Depth colorization disabled: cv2 import failed (%s). "
                    "Install opencv-python-headless if you want the "
                    "Perception screen depth panel.", exc,
                )
                self._cv2_warned = True
            return None

        try:
            mm = arr.astype(np.float32) * float(self._scale_mm)
            in_range = (mm >= float(DEFAULT_MIN_RANGE_MM)) & (
                mm <= float(DEFAULT_MAX_RANGE_MM)
            )
            span = float(DEFAULT_MAX_RANGE_MM - DEFAULT_MIN_RANGE_MM)
            if span <= 0:
                return None
            # Clip + normalise. We do this on the FULL image (not just
            # the in_range mask) so the resulting uint8 is well-defined
            # everywhere; the mask is only used to black out the
            # out-of-range / no-return pixels in a final pass.
            clipped = np.clip(
                mm - float(DEFAULT_MIN_RANGE_MM), 0.0, span
            )
            normalised = (clipped * (255.0 / span)).astype(np.uint8)
            color_bgr = cv2.applyColorMap(normalised, cv2.COLORMAP_JET)
            # Force out-of-range / no-return to black.
            color_bgr[~in_range] = (0, 0, 0)
            # Ensure C-contiguous so QImage's stride math (w*3) is
            # valid. cv2 outputs are usually contiguous but be defensive
            # against future numpy slicing additions in this function.
            if not color_bgr.flags["C_CONTIGUOUS"]:
                color_bgr = np.ascontiguousarray(color_bgr)
            h, w, _ = color_bgr.shape
            # tobytes() copies into a Python buffer the consumer can
            # hand to QImage without worrying about the underlying
            # numpy array being reallocated on the next frame.
            return (int(w), int(h), color_bgr.tobytes())
        except Exception as exc:
            log.debug("Depth colorize failed: %s", exc)
            return None

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
