"""Qt facade over the SLAM engine.

Owns the active lidar driver (Slamtec S2E by default; legacy RPLIDAR
A1M8 selectable via ``NINA_LIDAR_MODEL=a1``) and the BreezySLAM
engine, runs a background thread that pulls scans from the lidar,
feeds them to the engine, and emits Qt signals for the UI:

    snapshot_changed(dict)   # SlamSnapshot serialised
    status_changed(dict)     # connection / health / fallback flags
    pose_changed(dict)       # x_mm, y_mm, theta_deg, updated_at

The lidar is also exposed through `latest_scan()` so the AutonomyController
can read the same scans without opening a second connection (a duplicate
UDP session on the S2E would either be refused by the device or
desynchronise the scan stream).

Like the rest of the stack, this worker degrades gracefully:

  * Lidar not present         -> lidar.connected = False, no SLAM updates
  * BreezySLAM not installed  -> engine runs in fallback rasteriser mode
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from nina.config.settings import LidarSettings, SlamSettings
from nina.sensors.lidar_factory import LidarLike, build_lidar, model_label
from nina.sensors.types import LidarScan
from nina.slam.engine import (
    SlamEngine,
    SlamSnapshot,
    lidar_to_distance_array,
)


log = logging.getLogger("sirena_ui.slam")


def _snapshot_to_dict(snap: SlamSnapshot) -> dict:
    return {
        "width": snap.width,
        "height": snap.height,
        "scale_mm_per_px": snap.scale_mm_per_px,
        "pose": {
            "x_mm": snap.pose.x_mm,
            "y_mm": snap.pose.y_mm,
            "theta_deg": snap.pose.theta_deg,
        },
        "updated_at": snap.updated_at,
        # grid_bytes is large; we ship it separately via snapshot_object()
        # to avoid round-tripping through the signal queue every tick.
    }


class SlamWorker(QObject):
    snapshot_changed = pyqtSignal(dict)   # excludes grid bytes
    status_changed = pyqtSignal(dict)
    pose_changed = pyqtSignal(dict)

    def __init__(
        self,
        slam_settings: SlamSettings,
        lidar_settings: Optional[LidarSettings] = None,
        lidar: Optional[LidarLike] = None,
        engine: Optional[SlamEngine] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        # `lidar_settings` is optional purely to keep callers that
        # don't care about the hardware variant (most tests) working
        # without ceremony. When omitted we fall back to the env-var
        # driven default in `build_lidar()`.
        self._lidar_settings = lidar_settings
        self._lidar_model = (
            (lidar_settings.model if lidar_settings is not None else None)
            or "auto"
        )
        self._lidar = lidar or build_lidar(self._lidar_model)
        self._lidar_label = model_label(self._lidar_model)
        # The laser model parameters live on SlamSettings now; pass
        # them through so the engine builds a BreezySLAM Laser that
        # actually matches whichever lidar is plugged in.
        self._engine = engine or SlamEngine(
            map_size_pixels=slam_settings.map_size_pixels,
            map_size_meters=slam_settings.map_size_meters,
            hole_width_mm=slam_settings.hole_width_mm,
            random_seed=slam_settings.random_seed,
            laser_max_range_mm=slam_settings.laser_max_range_mm,
            laser_scan_size=slam_settings.laser_scan_size,
            laser_scan_rate_hz=slam_settings.laser_scan_rate_hz,
        )
        self._slam_scan_size = int(slam_settings.laser_scan_size)
        self._update_period = 1.0 / max(0.5, float(slam_settings.update_hz))
        self._running = False
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_snapshot: Optional[SlamSnapshot] = None
        self._lock = threading.RLock()
        self._status = {
            "lidar_connected": False,
            "lidar_message": "idle",
            "lidar_model": self._lidar_label,
            "slam_fallback": False,
            "slam_message": "",
            "running": False,
            "scans_processed": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._stop_evt.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="SlamWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        try:
            self._lidar.close()
        except Exception:
            pass
        try:
            self._engine.close()
        except Exception:
            pass
        with self._lock:
            self._status["lidar_connected"] = False
            self._status["lidar_message"] = "stopped"
            self._status["running"] = False
        self._emit_status()

    def shutdown(self) -> None:
        self.stop()

    def latest_scan(self) -> Optional[LidarScan]:
        return self._lidar.read()

    def latest_snapshot(self) -> Optional[SlamSnapshot]:
        with self._lock:
            return self._latest_snapshot

    def latest_pose(self) -> Optional[dict]:
        """Just the pose part of the latest snapshot, as a dict.

        Used by the goto pilot which needs pose every tick but doesn't
        want to copy the full grid bytes through `latest_snapshot()`.
        Returns ``None`` when the SLAM worker hasn't produced a
        snapshot yet (cold boot, lidar disconnected, etc.).
        """
        with self._lock:
            snap = self._latest_snapshot
        if snap is None:
            return None
        return {
            "x_mm": snap.pose.x_mm,
            "y_mm": snap.pose.y_mm,
            "theta_deg": snap.pose.theta_deg,
            "updated_at": snap.updated_at,
        }

    def latest_grid_view(self) -> Optional[dict]:
        """Compact view of the SLAM grid for path planning.

        Returns a dict with ``grid_bytes / width / height /
        scale_mm_per_px`` so the goto planner can run A* without
        importing `SlamSnapshot` or holding a snapshot reference
        across replans. Returns ``None`` until the first scan has
        been processed.
        """
        with self._lock:
            snap = self._latest_snapshot
        if snap is None:
            return None
        return {
            "grid_bytes": snap.grid_bytes,
            "width": snap.width,
            "height": snap.height,
            "scale_mm_per_px": snap.scale_mm_per_px,
        }

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def save_map(self, path: Path) -> bool:
        """Dump the current occupancy grid to a PGM file. Returns True
        on success.
        """
        snap = self.latest_snapshot()
        if snap is None:
            return False
        try:
            with open(path, "wb") as fh:
                header = (
                    f"P5\n{snap.width} {snap.height}\n255\n"
                ).encode("ascii")
                fh.write(header)
                fh.write(snap.grid_bytes)
            return True
        except Exception as exc:
            log.exception("save_map %s: %s", path, exc)
            return False

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        # 1) Try to bring up the lidar.
        try:
            self._lidar.open()
            with self._lock:
                self._status["lidar_connected"] = True
                self._status["lidar_message"] = (
                    f"{self._lidar_label} connected"
                )
        except Exception as exc:
            log.warning("%s open failed: %s", self._lidar_label, exc)
            with self._lock:
                self._status["lidar_connected"] = False
                self._status["lidar_message"] = f"sim - {exc}"

        # 2) Bring up the SLAM engine.
        try:
            self._engine.open()
            with self._lock:
                self._status["slam_fallback"] = self._engine.is_fallback()
                self._status["slam_message"] = (
                    self._engine.fallback_reason()
                    if self._engine.is_fallback() else "running"
                )
        except Exception as exc:
            log.warning("SLAM open failed: %s", exc)
            with self._lock:
                self._status["slam_fallback"] = True
                self._status["slam_message"] = f"sim - {exc}"

        with self._lock:
            self._status["running"] = True
        self._emit_status()

        # 3) Pull-feed-publish loop.
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            scan = self._lidar.read()
            if scan is not None:
                bins = self._engine._map_size_px  # noqa: SLF001 - private but stable
                # BreezySLAM's Laser scan_size is configured per
                # lidar (S2E: 400, A1: 360). Resample the driver's
                # bin count to whatever the engine was built with.
                resampled = lidar_to_distance_array(
                    scan, n_bins=self._slam_scan_size
                )
                if any(resampled):
                    feed_scan = LidarScan(
                        distances_mm=resampled,
                        timestamp_s=scan.timestamp_s,
                        rpm=scan.rpm,
                        quality=scan.quality,
                    )
                    self._engine.update(feed_scan)
                    snap = self._engine.snapshot()
                    with self._lock:
                        self._latest_snapshot = snap
                        self._status["scans_processed"] = self._engine.stats()[
                            "scans_processed"
                        ]
                        # Re-publish slam fallback/reason since update()
                        # might have switched modes.
                        self._status["slam_fallback"] = self._engine.is_fallback()
                        self._status["slam_message"] = (
                            self._engine.fallback_reason()
                            if self._engine.is_fallback() else "running"
                        )
                    self.snapshot_changed.emit(_snapshot_to_dict(snap))
                    self.pose_changed.emit({
                        "x_mm": snap.pose.x_mm,
                        "y_mm": snap.pose.y_mm,
                        "theta_deg": snap.pose.theta_deg,
                        "updated_at": snap.updated_at,
                    })
                    self._emit_status()
            elapsed = time.monotonic() - t0
            if elapsed < self._update_period:
                self._stop_evt.wait(self._update_period - elapsed)

    def _emit_status(self) -> None:
        with self._lock:
            payload = dict(self._status)
        self.status_changed.emit(payload)
