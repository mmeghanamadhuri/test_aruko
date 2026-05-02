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
    # Take the configured bot-skip pct and paint floor in the
    # bottom half of that band - lets the test survive a tuning
    # nudge within reason without needing to be rewritten.
    bot_skip_rows = max(1, int(h * realsense_d435.DEFAULT_BOT_SKIP_PCT / 100) // 2)
    arr[h - bot_skip_rows:, :] = 480

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm is not None
    assert frame.forward_min_mm >= 2000, (
        f"forward_min_mm={frame.forward_min_mm} - the bottom "
        f"{bot_skip_rows} rows (well within the "
        f"{realsense_d435.DEFAULT_BOT_SKIP_PCT}% bot-skip band) "
        "leaked into the forward cone. Autonomy would spin forever."
    )


def test_top_rows_excluded_from_forward_min() -> None:
    """The top DEFAULT_TOP_SKIP_PCT% of rows are sky / ceiling
    lights / direct overhead glare. They shouldn't pollute the
    forward cone with random shorts. (Default skip is small - just
    enough to drop direct ceiling returns - so chest-height objects
    a couple of metres ahead stay visible.)"""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 30
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # Place a phantom 'ceiling' return in the rows we KNOW are
    # masked - take the configured top-skip pct and only paint the
    # top half of that band so the test stays correct if someone
    # tweaks the default within reason.
    skip_rows = max(1, int(h * realsense_d435.DEFAULT_TOP_SKIP_PCT / 100) // 2)
    arr[:skip_rows, :] = 250

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm is not None
    assert frame.forward_min_mm >= 2000, (
        f"forward_min_mm={frame.forward_min_mm} - the top {skip_rows} "
        f"rows (well within the {realsense_d435.DEFAULT_TOP_SKIP_PCT}% "
        "top-skip band) leaked into the forward cone"
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


def test_skip_pcts_are_clamped_safely(monkeypatch) -> None:
    """Operators may set bogus skip values via env (e.g. 90/90 = no
    rows left). The slicing math must still produce a non-empty
    region instead of crashing or silently passing an empty array
    to numpy.min()."""
    np = pytest.importorskip("numpy")

    # Pin cluster size to 1 for this test - we're exercising the
    # row-slicing math on a deliberately tiny (4x6) frame, not the
    # glint cluster filter (which would otherwise reject the
    # forward cone for being too small).
    monkeypatch.setattr(realsense_d435, "DEFAULT_MIN_CLUSTER_PX", 1)

    drv = _drv()
    h, w = 4, 6
    arr = np.full((h, w), 1500, dtype=np.uint16)

    drv._publish(np, arr)
    frame = drv.read()
    assert frame is not None
    assert frame.forward_min_mm == 1500


# ---------------------------------------------------------------------
# Glint / cluster filter tests
# ---------------------------------------------------------------------
#
# These cover the second-half fix for "bot spins in place even when
# nothing's in front of it": on a reflective floor (polished concrete,
# vinyl, glossy tile) the D435's IR projector splashes single-pixel
# hot returns into the middle band of the depth image. A naive
# region.min() picks one up at ~300 mm and the autonomy reads the
# forward cone as permanently blocked. The cluster filter requires
# at least DEFAULT_MIN_CLUSTER_PX (50) pixels to agree before treating
# a reading as a real obstacle.


def test_single_pixel_glint_does_not_trigger_forward_block() -> None:
    """A single saturated 320 mm pixel in an otherwise-clear forward
    cone is the reflective-floor failure mode. With the cluster
    filter it must be ignored - the bot must report ~2000 mm clear,
    not ~320 mm blocked."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 90  # forward cone = 30 cols x ~55 rows = 1650 px
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # Plant ONE glint pixel at 320 mm in the middle of the forward cone.
    arr[50, w // 2] = 320

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm is not None
    assert frame.forward_min_mm >= 1900, (
        f"forward_min_mm={frame.forward_min_mm} - a single 320 mm "
        f"glint pixel hijacked the forward cone. The cluster filter "
        f"(DEFAULT_MIN_CLUSTER_PX={realsense_d435.DEFAULT_MIN_CLUSTER_PX}) "
        f"is not catching reflective-floor IR splash; on the real bot "
        f"this is 'spin in place even when surrounded by open space'."
    )


def test_real_obstacle_cluster_still_passes_filter() -> None:
    """A genuine obstacle that occupies more than DEFAULT_MIN_CLUSTER_PX
    pixels at the same range MUST still be reported - we'd rather a
    bot that occasionally over-reacts to a real obstacle than one
    that drives through a chair leg because the glint filter was too
    aggressive."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 90
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # 10x10 = 100 pixel obstacle at 700 mm in the middle band - 2x
    # the cluster threshold, comfortably above the noise floor.
    arr[45:55, w // 2 - 5:w // 2 + 5] = 700

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm == 700, (
        f"100-px real obstacle at 700 mm not reported "
        f"(got {frame.forward_min_mm}); cluster filter is dropping "
        f"obstacles it should be passing through"
    )


def test_min_range_floor_rejects_below_threshold() -> None:
    """DEFAULT_MIN_RANGE_MM was bumped from 200 -> 300 to drop the
    range where the D435 mostly returns IR-projector saturation /
    floor reflections rather than real distance. Even a real
    cluster of pixels at 250 mm should NOT contribute to forward_min_mm
    - if something is really inside the camera's reliable minimum,
    the depth sensor can't measure it accurately anyway."""
    np = pytest.importorskip("numpy")

    drv = _drv()
    h, w = 100, 90
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # Cluster of 200 pixels at 250 mm in the middle band - well
    # above the cluster threshold but BELOW the new MIN_MM=300.
    arr[40:60, w // 2 - 5:w // 2 + 5] = 250

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    # The cluster filter sees 200 pixels at 250 mm (below MIN_MM)
    # and skips them; everything else is at 2000 mm so that's what
    # gets reported. The autonomy uses other sensors (lidar /
    # ultrasonic) for the sub-300mm regime where the D435 isn't
    # reliable.
    assert frame.forward_min_mm is not None
    assert frame.forward_min_mm >= 1900, (
        f"forward_min_mm={frame.forward_min_mm} - a 200-pixel cluster "
        f"at 250 mm (below DEFAULT_MIN_RANGE_MM="
        f"{realsense_d435.DEFAULT_MIN_RANGE_MM}) was reported as the "
        f"forward distance. The MIN_MM floor is meant to drop the "
        f"unreliable near-field range where the D435 mostly returns "
        f"floor reflections / IR splash."
    )


def test_min_cluster_px_env_override_is_honoured(monkeypatch) -> None:
    """An operator with an unusual sensor / floor combo can re-tune
    the cluster size via NINA_DEPTH_MIN_CLUSTER_PX. The driver must
    actually re-read the constant at publish time (or be patched
    cleanly) - we expose the constant and test that monkeypatching
    it changes the threshold."""
    np = pytest.importorskip("numpy")

    monkeypatch.setattr(realsense_d435, "DEFAULT_MIN_CLUSTER_PX", 1)

    drv = _drv()
    h, w = 100, 90
    arr = np.full((h, w), 2000, dtype=np.uint16)
    # ONE pixel at 700 mm. With CLUSTER=1 it should be reported (the
    # operator explicitly opted out of cluster filtering for their
    # known-good environment).
    arr[50, w // 2] = 700

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm == 700, (
        f"With DEFAULT_MIN_CLUSTER_PX=1 the single-pixel min should "
        f"win (got {frame.forward_min_mm})"
    )


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
