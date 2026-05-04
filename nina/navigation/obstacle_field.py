"""Sensor fusion for reactive obstacle avoidance.

`ObstacleField` collapses the raw multi-sensor readings into a small
set of per-sector minimum distances (in mm) so the autonomous pilot
can ask one question:

    field.min_mm("forward")   # cone in front of the robot
    field.min_mm("left")      # ~45..135 deg
    field.min_mm("right")     # ~225..315 deg
    field.min_mm("rear")      # ~135..225 deg

Sources:

  * Lidar (head-mounted Slamtec S2E by default; legacy RPLIDAR A1M8
    selectable via NINA_LIDAR_MODEL=a1) - long-range, 360 deg coverage.
    Drives the sector minimums for "forward / left / right / rear".

  * Depth camera (chassis-front D435) - tightens the forward cone with
    short-range obstacles the lidar misses (e.g. low coffee tables that
    sit below the lidar plane).

  * Ultrasonic ring (4x HC-SR04) - secondary near-field obstacle layer
    for the corresponding chassis sectors.

  * IR cliff sensor (GP2Y0E02B) - emergency layer: if the IR distance
    drops below a "no floor" threshold the field reports zero forward
    clearance regardless of what the other sensors say, so the pilot
    aborts a forward step before it falls off a stair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from nina.sensors.types import (
    DepthFrame,
    IRReading,
    LidarScan,
    UltrasonicReading,
)


SECTOR_FORWARD = "forward"
SECTOR_LEFT = "left"
SECTOR_RIGHT = "right"
SECTOR_REAR = "rear"

ALL_SECTORS = (SECTOR_FORWARD, SECTOR_LEFT, SECTOR_RIGHT, SECTOR_REAR)


# Forward-cone half-width in degrees on either side of straight ahead.
_FORWARD_HALF_DEG = 25
# Side cones are 45 deg wide centred on +/- 90 from forward.
_SIDE_HALF_DEG = 45
# Rear cone is 60 deg wide centred on 180.
_REAR_HALF_DEG = 30

# Ultrasonic position -> primary sector mapping. Anything not listed
# is ignored.
_ULTRA_SECTOR = {
    "front_left": SECTOR_LEFT,
    "front_right": SECTOR_RIGHT,
    "rear_left": SECTOR_REAR,
    "rear_right": SECTOR_REAR,
}

# A single front-centre sonar (if anyone wires one up later) maps to
# the forward cone too.
_ULTRA_FORWARD_HINTS = {"front_centre", "front_center"}


@dataclass
class ObstacleField:
    """Per-sector minimum-distance summary."""

    forward_mm: Optional[int] = None
    left_mm: Optional[int] = None
    right_mm: Optional[int] = None
    rear_mm: Optional[int] = None

    cliff_distance_mm: Optional[int] = None
    cliff_alarm: bool = False

    sources: Dict[str, str] = None  # type: ignore[assignment]
    # Per-source contribution to the forward sector, kept around so
    # the autonomy log line can spell out "forward blocked because
    # depth=480 mm even though lidar=2100 mm" - the most common
    # autonomy bug shape on a fresh bot bring-up. Filled by fuse();
    # other sectors omitted (forward is the one that gates motion).
    forward_by_source: Dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sources is None:
            self.sources = {}
        if self.forward_by_source is None:
            self.forward_by_source = {}

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def min_mm(self, sector: str) -> Optional[int]:
        return {
            SECTOR_FORWARD: self.forward_mm,
            SECTOR_LEFT: self.left_mm,
            SECTOR_RIGHT: self.right_mm,
            SECTOR_REAR: self.rear_mm,
        }.get(sector)

    def is_clear(self, sector: str, threshold_mm: int) -> bool:
        d = self.min_mm(sector)
        if d is None:
            return False
        return d >= threshold_mm

    def as_dict(self) -> dict:
        return {
            "forward_mm": self.forward_mm,
            "left_mm": self.left_mm,
            "right_mm": self.right_mm,
            "rear_mm": self.rear_mm,
            "cliff_distance_mm": self.cliff_distance_mm,
            "cliff_alarm": self.cliff_alarm,
            "sources": dict(self.sources),
            "forward_by_source": dict(self.forward_by_source),
        }


def _min_or_none(values: Iterable[Optional[int]]) -> Optional[int]:
    out: Optional[int] = None
    for v in values:
        if v is None:
            continue
        if out is None or v < out:
            out = v
    return out


def _sector_for_angle_deg(angle_deg: float) -> Optional[str]:
    """Map a 0..360 lidar angle to a sector name. 0 deg = forward,
    +90 = right, -90 / 270 = left.
    """
    a = angle_deg % 360.0
    if a > 180.0:
        a -= 360.0
    if abs(a) <= _FORWARD_HALF_DEG:
        return SECTOR_FORWARD
    if 90 - _SIDE_HALF_DEG <= a <= 90 + _SIDE_HALF_DEG:
        return SECTOR_RIGHT
    if -(90 + _SIDE_HALF_DEG) <= a <= -(90 - _SIDE_HALF_DEG):
        return SECTOR_LEFT
    if abs(a) >= 180 - _REAR_HALF_DEG:
        return SECTOR_REAR
    return None


def fuse(
    *,
    lidar: Optional[LidarScan],
    ultrasonics: Iterable[UltrasonicReading] = (),
    ir: Optional[IRReading] = None,
    depth: Optional[DepthFrame] = None,
    cliff_min_mm: int = 60,
) -> ObstacleField:
    """Build an ObstacleField from the latest readings of every sensor.

    Any of the inputs may be `None` / empty - the fusion just skips
    that source and records what it did use in `field.sources`.
    """
    forward_candidates: List[Optional[int]] = []
    left_candidates: List[Optional[int]] = []
    right_candidates: List[Optional[int]] = []
    rear_candidates: List[Optional[int]] = []
    sources: Dict[str, str] = {}
    # Per-source forward-sector minima, attached to the field so the
    # autonomy log can break down WHO drove the forward decision.
    forward_by_source: Dict[str, int] = {}

    # ---- lidar ----
    if lidar is not None and lidar.distances_mm:
        sources["lidar"] = f"{lidar.num_points()} returns"
        n = len(lidar.distances_mm)
        lidar_forward_min: Optional[int] = None
        for idx, dist in enumerate(lidar.distances_mm):
            if dist <= 0:
                continue
            angle = (idx / float(n)) * 360.0
            sector = _sector_for_angle_deg(angle)
            if sector == SECTOR_FORWARD:
                forward_candidates.append(int(dist))
                if lidar_forward_min is None or dist < lidar_forward_min:
                    lidar_forward_min = int(dist)
            elif sector == SECTOR_LEFT:
                left_candidates.append(int(dist))
            elif sector == SECTOR_RIGHT:
                right_candidates.append(int(dist))
            elif sector == SECTOR_REAR:
                rear_candidates.append(int(dist))
        if lidar_forward_min is not None:
            forward_by_source["lidar"] = lidar_forward_min

    # ---- ultrasonics ----
    ultra_used = 0
    ultra_forward_min: Optional[int] = None
    for reading in ultrasonics:
        if reading.distance_mm is None:
            continue
        if reading.position in _ULTRA_FORWARD_HINTS:
            forward_candidates.append(reading.distance_mm)
            if ultra_forward_min is None or reading.distance_mm < ultra_forward_min:
                ultra_forward_min = int(reading.distance_mm)
            ultra_used += 1
            continue
        sector = _ULTRA_SECTOR.get(reading.position)
        if sector is None:
            continue
        if sector == SECTOR_LEFT:
            left_candidates.append(reading.distance_mm)
        elif sector == SECTOR_RIGHT:
            right_candidates.append(reading.distance_mm)
        elif sector == SECTOR_REAR:
            rear_candidates.append(reading.distance_mm)
        ultra_used += 1
    if ultra_used > 0:
        sources["ultrasonic"] = f"{ultra_used} sensors"
    if ultra_forward_min is not None:
        forward_by_source["ultrasonic"] = ultra_forward_min

    # ---- depth ----
    if depth is not None:
        if depth.forward_min_mm is not None:
            forward_candidates.append(depth.forward_min_mm)
            forward_by_source["depth"] = int(depth.forward_min_mm)
        if depth.left_min_mm is not None:
            left_candidates.append(depth.left_min_mm)
        if depth.right_min_mm is not None:
            right_candidates.append(depth.right_min_mm)
        sources["depth"] = "D435 forward cone"

    # ---- IR cliff ----
    cliff_distance: Optional[int] = None
    cliff_alarm = False
    if ir is not None:
        cliff_distance = ir.distance_mm
        sources["ir"] = f"{ir.position}: {ir.distance_mm} mm" \
            if ir.distance_mm is not None else f"{ir.position}: no echo"
        # GP2Y0E02B always sees the floor when stationary; a sudden
        # drop-out (no echo) or a reading shorter than the configured
        # floor distance both indicate a cliff.
        if ir.distance_mm is None or ir.distance_mm < cliff_min_mm:
            cliff_alarm = True

    field = ObstacleField(
        forward_mm=_min_or_none(forward_candidates),
        left_mm=_min_or_none(left_candidates),
        right_mm=_min_or_none(right_candidates),
        rear_mm=_min_or_none(rear_candidates),
        cliff_distance_mm=cliff_distance,
        cliff_alarm=cliff_alarm,
        sources=sources,
        forward_by_source=forward_by_source,
    )
    if cliff_alarm:
        # Cliff alarm collapses the forward sector to zero: the pilot
        # must NOT keep going forward regardless of what lidar / depth
        # think.
        field.forward_mm = 0
    return field
