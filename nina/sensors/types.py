"""Sensor data types shared by drivers, SLAM and autonomy.

Distances are millimetres, angles are degrees (clockwise positive,
0 = forward) so they line up with BreezySLAM's lidar conventions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class LidarScan:
    """One full 360-degree sweep from the active 2D lidar.

    Drivers using this type today: the Slamtec S2E (`SlamtecS2E`,
    Ethernet/UDP, 400-bin default) and the legacy RPLIDAR A1M8
    (`RPLidarA1`, USB-serial, 360-bin default). The SLAM engine and
    autonomy fusion code are bin-count agnostic - they convert idx
    -> angle on the fly.

    `distances_mm` is laid out so index `i` corresponds to angle
    `i * (360 / len(distances_mm))` degrees (typically 360 or 400
    entries = ~1-degree resolution). A value of 0 means "no return"
    / out of range, which is the convention BreezySLAM expects.
    """

    distances_mm: List[int]
    timestamp_s: float
    rpm: float = 0.0
    quality: float = 0.0  # 0..1, share of bins with a valid return

    def num_points(self) -> int:
        return sum(1 for d in self.distances_mm if d > 0)

    def as_dict(self) -> dict:
        return {
            "n": len(self.distances_mm),
            "valid": self.num_points(),
            "rpm": round(self.rpm, 2),
            "quality": round(self.quality, 3),
            "timestamp_s": self.timestamp_s,
        }


@dataclass
class UltrasonicReading:
    """Single ping from one HC-SR04 unit.

    `position` is a free-form name (e.g. 'front_left') so the autonomy
    layer can reason about geometry without hard-coding pin numbers.
    `distance_mm` is `None` if the echo timed out (no obstacle within
    range, or sensor disconnected).
    """

    position: str
    distance_mm: Optional[int]
    timestamp_s: float


@dataclass
class IRReading:
    """Single sample from the GP2Y0E02B IR distance sensor.

    Used as a cliff / very-near obstacle sensor on Nina (the
    GP2Y0E02B's useful range is 4-50 cm).
    """

    position: str
    distance_mm: Optional[int]
    timestamp_s: float


@dataclass
class DepthFrame:
    """Forward-facing summary from the RealSense D435.

    We deliberately don't ship the raw point cloud through the worker
    boundary - on Jetson Nano that's expensive to copy at 30 Hz. The
    autonomy layer only needs a small set of forward-cone statistics,
    and the Map screen visualiser renders the same summary.
    """

    forward_min_mm: Optional[int]   # closest point in the central forward cone
    forward_avg_mm: Optional[int]   # average distance in the central forward cone
    left_min_mm: Optional[int]
    right_min_mm: Optional[int]
    timestamp_s: float
    width: int = 0
    height: int = 0


@dataclass
class SensorHealth:
    """Coarse-grained, UI-friendly sensor status.

    Each per-sensor field is a `(connected, message)` tuple where
    `message` is empty on success or carries the human-readable error
    when `connected` is False.
    """

    lidar: Tuple[bool, str] = (False, "")
    ultrasonic: List[Tuple[str, bool, str]] = field(default_factory=list)
    ir: Tuple[bool, str] = (False, "")
    depth: Tuple[bool, str] = (False, "")

    def as_dict(self) -> dict:
        return {
            "lidar": {"connected": self.lidar[0], "message": self.lidar[1]},
            "ultrasonic": [
                {"position": p, "connected": c, "message": m}
                for p, c, m in self.ultrasonic
            ],
            "ir": {"connected": self.ir[0], "message": self.ir[1]},
            "depth": {"connected": self.depth[0], "message": self.depth[1]},
        }
