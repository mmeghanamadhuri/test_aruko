"""Headless SLAM + lidar thread for nina-link (no Qt; mirrors ``sirena_ui.workers.SlamWorker``)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from nina.config.settings import SlamSettings
from nina.sensors.rplidar_a1 import RPLidarA1
from nina.sensors.types import LidarScan
from nina.slam.engine import SlamEngine, SlamSnapshot, lidar_to_distance_array

log = logging.getLogger("nina.link_daemon.slam_bridge")


def _snapshot_meta_dict(snap: SlamSnapshot) -> Dict[str, Any]:
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
    }


class SlamBridge:
    """Background lidar + SLAM loop; safe to start/stop once per process."""

    def __init__(self, slam_settings: SlamSettings) -> None:
        self._lidar = RPLidarA1()
        self._engine = SlamEngine(
            map_size_pixels=slam_settings.map_size_pixels,
            map_size_meters=slam_settings.map_size_meters,
            hole_width_mm=slam_settings.hole_width_mm,
            random_seed=slam_settings.random_seed,
        )
        self._update_period = 1.0 / max(0.5, float(slam_settings.update_hz))
        self._running = False
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_snapshot: Optional[SlamSnapshot] = None
        self._lock = threading.RLock()
        self._status: Dict[str, Any] = {
            "lidar_connected": False,
            "lidar_message": "idle",
            "slam_fallback": False,
            "slam_message": "",
            "running": False,
            "scans_processed": 0,
        }

    def start(self) -> None:
        if self._running:
            return
        self._stop_evt.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="SlamBridge", daemon=True
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
        log.info("SlamBridge stopped")

    def latest_scan(self) -> Optional[LidarScan]:
        return self._lidar.read()

    def latest_snapshot(self) -> Optional[SlamSnapshot]:
        with self._lock:
            return self._latest_snapshot

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def snapshot_json(self) -> Optional[Dict[str, Any]]:
        snap = self.latest_snapshot()
        if snap is None:
            return None
        return _snapshot_meta_dict(snap)

    def occupancy_bytes(self) -> Optional[bytes]:
        snap = self.latest_snapshot()
        if snap is None:
            return None
        return snap.grid_bytes

    def save_map(self, path: Path) -> bool:
        snap = self.latest_snapshot()
        if snap is None:
            return False
        try:
            with open(path, "wb") as fh:
                header = (f"P5\n{snap.width} {snap.height}\n255\n").encode("ascii")
                fh.write(header)
                fh.write(snap.grid_bytes)
            return True
        except Exception as exc:
            log.exception("save_map %s: %s", path, exc)
            return False

    def _run(self) -> None:
        try:
            self._lidar.open()
            with self._lock:
                self._status["lidar_connected"] = True
                self._status["lidar_message"] = "connected"
        except Exception as exc:
            log.warning("RPLIDAR open failed: %s", exc)
            with self._lock:
                self._status["lidar_connected"] = False
                self._status["lidar_message"] = f"sim - {exc}"

        try:
            self._engine.open()
            with self._lock:
                self._status["slam_fallback"] = self._engine.is_fallback()
                self._status["slam_message"] = (
                    self._engine.fallback_reason()
                    if self._engine.is_fallback()
                    else "running"
                )
        except Exception as exc:
            log.warning("SLAM open failed: %s", exc)
            with self._lock:
                self._status["slam_fallback"] = True
                self._status["slam_message"] = f"sim - {exc}"

        with self._lock:
            self._status["running"] = True

        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            scan = self._lidar.read()
            if scan is not None:
                resampled = lidar_to_distance_array(scan, n_bins=360)
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
                        self._status["slam_fallback"] = self._engine.is_fallback()
                        self._status["slam_message"] = (
                            self._engine.fallback_reason()
                            if self._engine.is_fallback()
                            else "running"
                        )
            elapsed = time.monotonic() - t0
            if elapsed < self._update_period:
                self._stop_evt.wait(self._update_period - elapsed)


_bridge: Optional[SlamBridge] = None
_bridge_lock = threading.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_bridge() -> Optional[SlamBridge]:
    return _bridge


def ensure_bridge_started() -> SlamBridge:
    """Construct (once) and start the slam thread."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            from nina.config.settings import load_settings

            settings = load_settings(_repo_root())
            _bridge = SlamBridge(settings.slam)
        _bridge.start()
        return _bridge


def stop_bridge() -> None:
    global _bridge
    with _bridge_lock:
        if _bridge is not None:
            _bridge.stop()
