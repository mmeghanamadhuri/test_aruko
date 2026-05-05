"""Pin the autonomy / SLAM / depth defaults to the values an
operator can actually drive against without crashing the bot.

These tests are cheap and exist purely to make accidental regressions
loud. The values here were chosen after live-bot feedback ("almost
hitting people", "banged into tables and boxes", "lidar map showing
almost nothing"). Lowering them again should be a deliberate choice
backed by a comment in the matching settings.py block - the test
will scream if someone bumps the numbers without updating both
sides.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nina.config.settings import load_settings
from nina.sensors import realsense_d435


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Strip every NINA_AUTO_* / NINA_SLAM_* / NINA_DEPTH_* /
    NINA_LIDAR_* env var so we test the *built-in* defaults, not
    whatever the dev box has set in its shell. The autonomy
    bring-up doc tells operators to set these in
    `desktop/nina-ui-kiosk.service`; we want the in-code defaults
    to be safe even if the unit file is missing."""
    for key in list(os.environ):
        if key.startswith((
            "NINA_AUTO_", "NINA_SLAM_", "NINA_DEPTH_", "NINA_LIDAR_",
        )):
            monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------
# Autonomy thresholds
# ---------------------------------------------------------------------


def test_cruise_speed_matches_manual_minimum(clean_env) -> None:
    """Autonomy cruise must match the GUI's manual-mode minimum so
    the wheels don't change pace when the operator hands off control."""
    s = load_settings(REPO_ROOT)
    assert s.autonomy.cruise_speed_pct == 10, (
        "autonomy cruise drifted from 10%; either the manual floor "
        "moved (update both) or someone bumped this for testing and "
        "forgot to revert"
    )


def test_forward_clearance_leaves_room_to_brake(clean_env) -> None:
    """Forward-commit clearance must leave the BLDCs room to actually
    stop. At typical low cruise PWM with ~125 ms tick + ~200 ms wheel
    coast, anything below ~1 m brings the bot inside arm's length of
    a person before it stops. 1200 mm is the empirical floor that
    keeps stops in personal-space rather than chest-bumping."""
    s = load_settings(REPO_ROOT)
    assert s.autonomy.forward_clear_mm >= 1000, (
        f"forward_clear_mm={s.autonomy.forward_clear_mm} is too tight; "
        "operators reported the bot getting within arm's length of "
        "people before it stopped. See REQUIREMENTS.md §5.3.5."
    )


def test_emergency_stop_actually_decelerates(clean_env) -> None:
    """Emergency stop must trigger with enough standoff that the
    backoff window can move the bot before bumper contact. The old
    300 mm default was too tight; 850 mm pairs with forward_clear=1200
    and dead-end backoff so reverse is usable in real clutter."""
    s = load_settings(REPO_ROOT)
    assert s.autonomy.emergency_stop_mm >= 500, (
        f"emergency_stop_mm={s.autonomy.emergency_stop_mm} is too "
        "tight; reverse fires after the bot is already in collision "
        "range"
    )
    assert s.autonomy.emergency_stop_mm >= 800, (
        f"emergency_stop_mm={s.autonomy.emergency_stop_mm}; below 800 "
        "the bot still coasts into bump range before e-stop reverse"
    )


def test_emergency_below_forward_clear(clean_env) -> None:
    """Emergency must be a STRICT subset of forward-blocked, otherwise
    the pilot's layered checks tangle: e.g. emergency >= forward_clear
    means the pilot would 'reverse' on every sensor read where the
    obstacle is in the [clear, emergency] band, never giving the
    turn-in-place layer a chance."""
    s = load_settings(REPO_ROOT)
    assert s.autonomy.emergency_stop_mm < s.autonomy.forward_clear_mm, (
        f"emergency_stop_mm ({s.autonomy.emergency_stop_mm}) must be "
        f"< forward_clear_mm ({s.autonomy.forward_clear_mm}); pilot "
        "decision layers depend on this ordering"
    )


def test_fwd_blocked_backup_sec_default(clean_env) -> None:
    """Dead-end timeout should stay in a sensible band for indoor use."""
    s = load_settings(REPO_ROOT)
    assert 0.5 <= s.autonomy.fwd_blocked_backup_sec <= 10.0, (
        f"fwd_blocked_backup_sec={s.autonomy.fwd_blocked_backup_sec}"
    )


def test_tick_rate_fast_enough_for_walking_speed(clean_env) -> None:
    """At low cruise PWM the bot moves a few cm per tick. <= 5 Hz (200 ms /
    tick) lets the bot coast multiple cm between decisions, which
    showed up in the field as 'didn't stop in time'. 8 Hz cuts the
    coast distance roughly in half."""
    s = load_settings(REPO_ROOT)
    assert s.autonomy.tick_hz >= 6, (
        f"tick_hz={s.autonomy.tick_hz}; 5 Hz was the field-tested "
        "floor where the bot started overshooting its turn decision"
    )


# ---------------------------------------------------------------------
# SLAM map sizing
# ---------------------------------------------------------------------


def test_slam_world_fits_indoor_lidar_range(clean_env) -> None:
    """Default world MUST be small enough that a typical room
    actually fills a usable fraction of the rendered map - but
    big enough to use the active lidar's range. The Slamtec S2E
    (current default) reliably ranges ~25 m indoors, so a 12 m
    world keeps an 8 m hallway loop visible end-to-end while
    still letting walls render as multi-pixel features. The cap
    at 16 m guards against accidental bumps that would push
    typical rooms back into the 'tiny central patch' failure
    mode the A1-era default fixed."""
    s = load_settings(REPO_ROOT)
    assert s.slam.map_size_meters <= 16.0, (
        f"map_size_meters={s.slam.map_size_meters} is too large; the "
        "rendered map looks empty because typical rooms only fill a "
        "small central patch. See REQUIREMENTS.md §5.3."
    )


def test_slam_resolution_fine_enough_for_walls(clean_env) -> None:
    """mm/px resolution must be fine enough that a single wall
    actually shows up as more than one pixel. ~25 mm/px (the old
    20 m / 800 px config) made walls < 1 px wide on the Perception
    pane after letterboxing into a small viewport. <= 15 mm/px is
    the empirical floor where walls read as walls."""
    s = load_settings(REPO_ROOT)
    px_per_mm = s.slam.map_size_pixels / (s.slam.map_size_meters * 1000.0)
    mm_per_px = 1.0 / px_per_mm
    assert mm_per_px <= 15.0, (
        f"SLAM resolution {mm_per_px:.1f} mm/px is too coarse; walls "
        "render as sub-pixel features after letterboxing into the "
        "Perception card"
    )


def test_slam_laser_model_matches_active_lidar(clean_env) -> None:
    """The BreezySLAM laser model parameters must match the physical
    lidar's effective range or the particle filter mis-weights long
    returns. The current build defaults to the Slamtec S2E (~28 m
    effective indoors, ~10 Hz, ~400 samples per sweep). If the
    factory defaults drift back to the A1-era 12 m / 5.5 Hz / 360
    samples without updating settings.py's branch, this test
    screams."""
    s = load_settings(REPO_ROOT)
    # The S2E branch sets these; the A1 branch sets different
    # numbers but is opt-in via NINA_LIDAR_MODEL=a1 (cleared by
    # clean_env) so we land on the S2E defaults.
    assert s.lidar.model == "s2e", (
        f"default lidar model is {s.lidar.model!r}; the build is "
        "supposed to ship configured for the Slamtec S2E now"
    )
    assert s.slam.laser_max_range_mm >= 20000, (
        f"laser_max_range_mm={s.slam.laser_max_range_mm} is shorter "
        "than the S2E's effective indoor range; BreezySLAM will "
        "clip valid mid-range returns"
    )
    assert 8.0 <= s.slam.laser_scan_rate_hz <= 20.0, (
        f"laser_scan_rate_hz={s.slam.laser_scan_rate_hz} is outside "
        "the S2E's typical 10-15 Hz envelope"
    )
    assert s.slam.laser_scan_size >= 360, (
        f"laser_scan_size={s.slam.laser_scan_size}; BreezySLAM's "
        "Markov filter needs at least ~1 deg angular resolution"
    )


def test_a1_legacy_path_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operators with the legacy A1M8 lidar set NINA_LIDAR_MODEL=a1.
    The branch must keep producing the proven 8 m / 800 px / 12 m
    laser-model defaults so existing bots don't suddenly start
    seeing the S2E-tuned numbers (which would clip A1 returns at
    28 m even though the A1 can't see that far)."""
    for key in list(os.environ):
        if key.startswith(("NINA_AUTO_", "NINA_SLAM_", "NINA_DEPTH_", "NINA_LIDAR_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NINA_LIDAR_MODEL", "a1")
    s = load_settings(REPO_ROOT)
    assert s.lidar.model == "a1"
    assert s.slam.map_size_meters == 8.0
    assert s.slam.map_size_pixels == 800
    assert s.slam.laser_max_range_mm == 12000
    assert s.slam.laser_scan_size == 360


# ---------------------------------------------------------------------
# Depth top-mask: must let table-tops through
# ---------------------------------------------------------------------


def test_depth_top_mask_sees_chest_height_obstacles(clean_env) -> None:
    """A 70 cm tabletop sitting 1.5 m in front of a 30 cm-tall
    forward-tilted D435 lands at image angle ~15 deg above the
    optical axis - row ~117 of a 480-row frame. A top-skip of >= 25%
    masks that row out, so the bot loses sight of the tabletop
    between 1 m and 2 m and drives into it. Cap the default skip
    at 15% so chest-height obstacles stay visible to the autonomy
    cone."""
    np = pytest.importorskip("numpy")

    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0

    h, w = 480, 640
    arr = np.full((h, w), 3000, dtype=np.uint16)  # background at 3 m
    # Drop a "tabletop" return at row 117 (the geometry above) right
    # in the central forward third of the frame, at 1500 mm.
    cx0, cx1 = w // 3, 2 * w // 3
    arr[110:130, cx0:cx1] = 1500

    drv._publish(np, arr)
    frame = drv.read()

    assert frame is not None
    assert frame.forward_min_mm == 1500, (
        f"forward_min_mm={frame.forward_min_mm}; the top mask is "
        "hiding chest-height obstacles. Lower NINA_DEPTH_TOP_SKIP_PCT."
    )


def test_depth_top_skip_default_value(clean_env) -> None:
    """Pin the value so a future bump back to 25 fails loudly.
    Operators have already shipped bots configured with the new
    10% default and we don't want the next merge to silently
    re-create the table-top blind spot."""
    # Re-import to pick up the (cleaned) env defaults via the module
    # constant the live driver uses.
    import importlib
    importlib.reload(realsense_d435)
    assert realsense_d435.DEFAULT_TOP_SKIP_PCT <= 15, (
        f"DEFAULT_TOP_SKIP_PCT={realsense_d435.DEFAULT_TOP_SKIP_PCT}; "
        "going above 15 hides chest-height obstacles"
    )
    assert realsense_d435.DEFAULT_BOT_SKIP_PCT >= 30, (
        f"DEFAULT_BOT_SKIP_PCT={realsense_d435.DEFAULT_BOT_SKIP_PCT}; "
        "going below 30 lets the floor back into the forward cone "
        "and the bot will spin in place"
    )
    assert 0.60 <= realsense_d435.DEFAULT_FWD_BAND_FRAC < 1.0, (
        f"DEFAULT_FWD_BAND_FRAC={realsense_d435.DEFAULT_FWD_BAND_FRAC}; "
        "set to 1.0 the forward cone again sees floor in the lower "
        "middle band on steep down-tilt (spin in place); above 0.60 "
        "keeps chair-leg-scale obstacles visible in the trimmed cone"
    )


# ---------------------------------------------------------------------
# Reflective-floor / IR-glint defaults
# ---------------------------------------------------------------------


def test_depth_min_range_above_d435_floor(clean_env) -> None:
    """The D435's reliable minimum (per Intel's datasheet) is ~280 mm.
    Below that the sensor mostly returns IR projector saturation and
    floor reflections, which on a polished/glossy floor look like
    real obstacles to the autonomy. Pin the floor at 300 mm so this
    can't drift back down to 200 (the original default that caused
    'bot spins forever even when surrounded by open space')."""
    import importlib
    importlib.reload(realsense_d435)
    assert realsense_d435.DEFAULT_MIN_RANGE_MM >= 280, (
        f"DEFAULT_MIN_RANGE_MM={realsense_d435.DEFAULT_MIN_RANGE_MM} "
        "is inside the D435's noise floor; reflective-floor glints "
        "will be reported as forward obstacles"
    )


def test_depth_min_cluster_px_filters_single_pixel_glints(clean_env) -> None:
    """Pin DEFAULT_MIN_CLUSTER_PX above 1 so a single-pixel reading
    can never drive the autonomy decision. The empirical threshold
    is ~25 (a 5x5 IR splash) -> 50 gives 2x headroom and still
    catches a chair leg cluster at typical cruise distance."""
    import importlib
    importlib.reload(realsense_d435)
    assert realsense_d435.DEFAULT_MIN_CLUSTER_PX >= 25, (
        f"DEFAULT_MIN_CLUSTER_PX={realsense_d435.DEFAULT_MIN_CLUSTER_PX}; "
        "below 25 a single-pixel IR-projector splash on a reflective "
        "floor can hijack forward_min_mm and the bot spins in place"
    )
