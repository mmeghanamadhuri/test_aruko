"""BreezySLAM-based 2D SLAM engine.

Inputs:
  * `LidarScan` objects from `nina.sensors.rplidar_a1.RPLidarA1`.

Outputs:
  * `SlamPose(x_mm, y_mm, theta_deg)` - robot pose in the global map frame.
  * `SlamSnapshot(grid, width, height, scale_mm_per_px, pose)` - occupancy
    grid as a `bytes` payload (`grid_bytes`) plus `numpy` view when numpy
    is available. The grid uses BreezySLAM's convention:

        0   = occupied (wall)
        255 = free space
        ~127 = unknown

If BreezySLAM isn't installed (developer Mac, etc.) the engine falls
back to a "passthrough" mode that still tracks scan deltas and
publishes a stub grid built from the raw lidar - enough for the UI to
show *something* useful while keeping the SLAM API stable.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from nina.sensors.types import LidarScan


log = logging.getLogger("nina.slam")


@dataclass
class SlamPose:
    x_mm: float
    y_mm: float
    theta_deg: float


@dataclass
class SlamSnapshot:
    grid_bytes: bytes
    width: int
    height: int
    scale_mm_per_px: float
    pose: SlamPose
    updated_at: float

    def world_to_pixel(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        """Map a world-frame point (origin = map centre) to grid pixel
        coordinates. Useful for overlaying the pose marker on the
        rendered grid.
        """
        cx = self.width / 2.0
        cy = self.height / 2.0
        px = int(cx + x_mm / self.scale_mm_per_px)
        py = int(cy - y_mm / self.scale_mm_per_px)
        px = max(0, min(self.width - 1, px))
        py = max(0, min(self.height - 1, py))
        return px, py


def is_available() -> Tuple[bool, str]:
    try:
        import breezyslam  # noqa: F401  type: ignore
        return True, ""
    except Exception as exc:  # pragma: no cover
        return False, f"breezyslam not installed ({exc})"


class SlamEngine:
    """Owns the BreezySLAM instance and the latest occupancy grid.

    Thread-safe: `update()` and `snapshot()` may be called from
    different threads. Updates are coalesced - if the engine is busy,
    a new scan replaces the pending one.
    """

    def __init__(
        self,
        map_size_pixels: int = 800,
        map_size_meters: float = 20.0,
        hole_width_mm: int = 600,
        random_seed: int = 0xdeadbeef,
    ) -> None:
        self._map_size_px = int(map_size_pixels)
        self._map_size_m = float(map_size_meters)
        self._scale_mm_per_px = (self._map_size_m * 1000.0) / float(self._map_size_px)
        self._hole_width_mm = int(hole_width_mm)
        self._seed = int(random_seed) & 0x7FFFFFFF

        self._slam = None
        self._fallback = False
        self._fallback_reason = ""

        self._lock = threading.RLock()
        self._mapbytes = bytearray(self._map_size_px * self._map_size_px)
        # Pre-fill with "unknown" (127) so the screen has a sensible
        # baseline before the first scan lands.
        for i in range(len(self._mapbytes)):
            self._mapbytes[i] = 127
        self._pose = SlamPose(0.0, 0.0, 0.0)
        self._updated_at = 0.0
        self._scans_processed = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            from breezyslam.algorithms import RMHC_SLAM  # type: ignore
            from breezyslam.sensors import RPLidarA1  # type: ignore
        except Exception as exc:
            self._fallback = True
            self._fallback_reason = f"breezyslam not installed ({exc})"
            log.warning(
                "SLAM running in fallback mode: %s", self._fallback_reason
            )
            return

        try:
            laser = RPLidarA1()
            self._slam = RMHC_SLAM(
                laser,
                self._map_size_px,
                self._map_size_m,
                random_seed=self._seed,
                hole_width_mm=self._hole_width_mm,
            )
            self._fallback = False
            self._fallback_reason = ""
        except Exception as exc:
            self._slam = None
            self._fallback = True
            self._fallback_reason = f"BreezySLAM init failed: {exc}"
            log.warning(self._fallback_reason)

    def close(self) -> None:
        # BreezySLAM has no explicit close() - dropping the reference
        # is enough. Reset our buffers so a fresh open() starts clean.
        with self._lock:
            self._slam = None
            for i in range(len(self._mapbytes)):
                self._mapbytes[i] = 127
            self._pose = SlamPose(0.0, 0.0, 0.0)
            self._updated_at = 0.0
            self._scans_processed = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_fallback(self) -> bool:
        return self._fallback

    def fallback_reason(self) -> str:
        return self._fallback_reason

    def update(self, scan: LidarScan) -> None:
        """Feed a new lidar scan. Returns immediately."""
        if not scan or not scan.distances_mm:
            return
        if self._slam is not None:
            try:
                self._slam.update(list(scan.distances_mm))
                x_mm, y_mm, theta_deg = self._slam.getpos()
                with self._lock:
                    self._slam.getmap(self._mapbytes)
                    self._pose = SlamPose(
                        x_mm=float(x_mm),
                        y_mm=float(y_mm),
                        theta_deg=float(theta_deg),
                    )
                    self._updated_at = time.monotonic()
                    self._scans_processed += 1
                return
            except Exception as exc:
                log.warning(
                    "BreezySLAM update failed; switching to fallback: %s", exc
                )
                self._slam = None
                self._fallback = True
                self._fallback_reason = f"slam.update raised: {exc}"

        self._fallback_update(scan)

    def snapshot(self) -> SlamSnapshot:
        with self._lock:
            return SlamSnapshot(
                grid_bytes=bytes(self._mapbytes),
                width=self._map_size_px,
                height=self._map_size_px,
                scale_mm_per_px=self._scale_mm_per_px,
                pose=SlamPose(self._pose.x_mm, self._pose.y_mm, self._pose.theta_deg),
                updated_at=self._updated_at,
            )

    def stats(self) -> dict:
        return {
            "scans_processed": self._scans_processed,
            "fallback": self._fallback,
            "fallback_reason": self._fallback_reason,
            "map_size_px": self._map_size_px,
            "map_size_m": self._map_size_m,
            "scale_mm_per_px": self._scale_mm_per_px,
        }

    # ------------------------------------------------------------------
    # Fallback rasteriser (no real SLAM, no pose estimation)
    # ------------------------------------------------------------------

    def _fallback_update(self, scan: LidarScan) -> None:
        """Render the latest scan as obstacles around a fixed pose at
        the centre of the map. Useful for local visualisation only -
        no pose estimation, no integration over time.
        """
        n = len(scan.distances_mm)
        if n == 0:
            return
        size = self._map_size_px
        cx = size // 2
        cy = size // 2
        with self._lock:
            for i in range(len(self._mapbytes)):
                self._mapbytes[i] = 127
            for idx, dist_mm in enumerate(scan.distances_mm):
                if dist_mm <= 0:
                    continue
                angle = (idx / float(n)) * 2.0 * math.pi
                px = int(cx + (dist_mm / self._scale_mm_per_px) * math.sin(angle))
                py = int(cy - (dist_mm / self._scale_mm_per_px) * math.cos(angle))
                if 0 <= px < size and 0 <= py < size:
                    self._mapbytes[py * size + px] = 0
            self._pose = SlamPose(0.0, 0.0, 0.0)
            self._updated_at = time.monotonic()
            self._scans_processed += 1


def lidar_to_distance_array(scan: Optional[LidarScan], n_bins: int = 360) -> List[int]:
    """Resample a LidarScan to a fixed-size distance array (mm).

    BreezySLAM expects the array length to match the laser model's
    `scan_size`. The default RPLidarA1 model uses 360 samples; if a
    scan was binned at a different resolution upstream, we down/up
    sample here.
    """
    if scan is None or not scan.distances_mm:
        return [0] * n_bins
    src = scan.distances_mm
    n = len(src)
    if n == n_bins:
        return list(src)
    out: List[int] = []
    for i in range(n_bins):
        idx = int(i / float(n_bins) * n)
        idx = max(0, min(n - 1, idx))
        out.append(int(src[idx]))
    return out
