"""Regression tests for the D435 floor-mask + obstacle-field
per-source breakdown.

Both pieces exist to fix one bug: in autonomous mode the bot was
reading the floor (the bottom rows of the D435 frame) as a phantom
forward wall at ~480 mm and spinning in place forever. The fix is
two-part:

1. `RealSenseD435._publish` slices a MIDDLE vertical band, not the
   bottom 2/3 of the frame, so floor pixels never reach
   `forward_min_mm`. The defaults skip top 25% (sky/ceiling) and
   bottom 35% (floor in front of bot).

2. `obstacle_field.fuse()` records a per-source forward-min map so
   the autonomy log can spell out exactly which sensor pulled
   forward below the clearance threshold ("depth=480mm even though
   lidar=2100mm"). Without this the operator just sees "forward
   blocked" and has no way to tell whether it's lidar, depth, or
   sonar.
"""

from __future__ import annotations

import pytest

from nina.navigation.obstacle_field import (
    SECTOR_FORWARD,
    fuse,
)
from nina.sensors import realsense_d435
from nina.sensors.types import (
    DepthFrame,
    LidarScan,
    UltrasonicReading,
)


# ---------------------------------------------------------------------
# D435 floor-mask tests
# ---------------------------------------------------------------------


def _drv() -> realsense_d435.RealSenseD435:
    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0  # 1 unit = 1 mm so ints can be read literally
    return drv


def test_floor_pixels_excluded_from_forward_min() -> None:
    """The bottom rows of a tilted-down D435 frame are the floor at
    ~480 mm. They MUST NOT reach forward_min_mm or the autonomy will
    permanently believe a wall sits 0.5 m ahead and spin in place
    forever instead of cruising."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 30
    arr = np.full((h, w), 2000, dtype=np.uint16)  # 2 m everywhere
    # Bottom 35% of rows (rows 65..99) are the "floor", reading ~480 mm.
    arr[65:, :] = 480

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm is not None
    assert frame.forward_min_mm >= 2000, (
        f"forward_min_mm={frame.forward_min_mm} - the floor rows leaked "
        f"into the forward cone. Autonomy would spin forever."
    )


def test_top_rows_excluded_from_forward_min() -> None:
    """The top 25% of rows are sky / ceiling lights (very far for the
    sensor, often dropouts). They shouldn't pollute the forward cone
    with random shorts either."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 30
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # Place a phantom 'ceiling' return in the top 25% of rows.
    arr[:25, :] = 250

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm is not None
    assert frame.forward_min_mm >= 2000, (
        f"forward_min_mm={frame.forward_min_mm} - top rows leaked into "
        f"the forward cone"
    )


def test_real_obstacle_in_middle_band_is_seen() -> None:
    """An obstacle at chassis height (the middle band of the depth
    image) MUST still be reported, otherwise the floor mask would
    have made the autonomy blind to actual things in its path."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 30
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # Drop a 600 mm obstacle in the middle horizontal third (the
    # forward cone) at the middle vertical band.
    cx0, cx1 = w // 3, 2 * w // 3
    arr[40:55, cx0:cx1] = 600

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm == 600, (
        f"Real middle-band obstacle at 600 mm wasn't reported "
        f"(got {frame.forward_min_mm}); floor mask is over-aggressive"
    )


def test_skip_pcts_are_clamped_safely() -> None:
    """Operators may set bogus skip values via env (e.g. 90/90 = no
    rows left). The slicing math must still produce a non-empty
    region instead of crashing or silently passing an empty array
    to numpy.min()."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 4, 6
    arr = np.full((h, w), 1500, dtype=np.uint16)

    # Should not raise even on a tiny (4-row) frame where both skip
    # pcts could otherwise round to overlapping rows.
    drv._publish(np, arr)
    frame = drv.read()
    assert frame is not None
    assert frame.forward_min_mm == 1500


# ---------------------------------------------------------------------
# Obstacle-field per-source breakdown tests
# ---------------------------------------------------------------------


def test_forward_by_source_records_each_input() -> None:
    """fuse() must attach a per-source map showing what each sensor
    contributed to the forward sector. This is what the autonomy log
    uses to print 'forward blocked: depth=480 mm, lidar=2100 mm' so
    the operator can diagnose mis-mounted depth cameras at a glance."""
    # Lidar reporting 2.1 m straight ahead (idx 0 = 0 deg = forward).
    distances = [0] * 360
    distances[0] = 2100
    lidar = LidarScan(distances_mm=distances, timestamp_s=1.0)

    # Depth (D435) reporting a closer wall at 800 mm.
    depth = DepthFrame(
        forward_min_mm=800,
        forward_avg_mm=900,
        left_min_mm=None,
        right_min_mm=None,
        timestamp_s=1.0,
        width=640,
        height=480,
    )

    field = fuse(lidar=lidar, depth=depth)

    assert field.forward_by_source.get("lidar") == 2100, (
        "lidar contribution missing from forward_by_source"
    )
    assert field.forward_by_source.get("depth") == 800, (
        "depth contribution missing from forward_by_source"
    )
    # Fused minimum still does the right thing.
    assert field.forward_mm == 800


def test_forward_by_source_includes_forward_ultrasonic() -> None:
    """A front-centre sonar contributes to the forward cone too."""
    sonar = UltrasonicReading(
        position="front_centre",
        distance_mm=550,
        timestamp_s=1.0,
    )
    # Lidar pointing into open space.
    distances = [0] * 360
    distances[0] = 3000
    lidar = LidarScan(distances_mm=distances, timestamp_s=1.0)

    field = fuse(lidar=lidar, ultrasonics=[sonar])

    assert field.forward_by_source.get("ultrasonic") == 550, (
        "front-centre sonar should contribute to forward_by_source"
    )
    assert field.forward_by_source.get("lidar") == 3000
    assert field.forward_mm == 550


def test_forward_by_source_omits_silent_sensors() -> None:
    """Sensors that didn't see anything in the forward cone shouldn't
    appear in the breakdown at all - operators reading the autonomy
    log should be able to tell 'depth not connected' from 'depth
    connected, reports far'."""
    # Lidar with all returns in the rear hemisphere.
    distances = [0] * 360
    distances[180] = 1500  # 180 deg = directly behind
    lidar = LidarScan(distances_mm=distances, timestamp_s=1.0)

    field = fuse(lidar=lidar)

    assert "lidar" not in field.forward_by_source, (
        "lidar with no forward returns shouldn't appear in the "
        "forward_by_source breakdown"
    )


def test_forward_by_source_serialised_in_as_dict() -> None:
    """as_dict() (the snapshot the Map / Perception screens render)
    must include forward_by_source so the UI can surface the same
    breakdown the autonomy log prints."""
    distances = [0] * 360
    distances[0] = 1800
    lidar = LidarScan(distances_mm=distances, timestamp_s=1.0)

    field = fuse(lidar=lidar)
    snap = field.as_dict()

    assert "forward_by_source" in snap
    assert snap["forward_by_source"] == {"lidar": 1800}
