"""Tests for ``nina.navigation.path_planner``.

The planner is a pure function over a BreezySLAM bytemap, so we
build small synthetic grids by hand and assert the topology of the
resulting path. Concrete cases:

  * **Open room**: any free->free plan should succeed and emit a
    monotonically-shortening route to the goal.
  * **Corridor**: a vertical wall with a gap forces the path to
    bend through the gap. We verify the path actually crosses the
    gap row rather than ghost-walking through the wall.
  * **U-shape**: a nested wall makes the goal reachable only via a
    detour. The planner must NOT report ``no_path``.
  * **Goal in wall**: A* refuses, but ``snap_radius_mm`` should
    nudge the pin to the nearest free cell.
  * **Footprint inflation**: with a fat footprint, a corridor too
    narrow for the bot has no path; with a slim footprint the same
    grid does.
  * **Coordinate round-trip**: ``world_to_pixel`` and
    ``pixel_to_world`` are exact inverses on the cell centre.

We use 80x80 pixel grids at 50 mm/px (so 4 m x 4 m), which keeps
A* under 5000 expansions even in the worst test case.
"""

from __future__ import annotations

import math
from typing import List, Tuple

import pytest

from nina.navigation.path_planner import (
    OCCUPIED_THRESHOLD,
    PlanResult,
    pixel_to_world,
    plan_path,
    world_to_pixel,
)


# Grey "unknown" byte BreezySLAM uses on init. Anything in [110, 145]
# is treated as unknown by the planner; we match the engine default
# the runtime uses.
GREY = 127
FREE = 255
WALL = 0


def _open_grid(w: int, h: int) -> bytearray:
    """All-free bytemap (no walls, no unknowns)."""
    return bytearray([FREE] * (w * h))


def _grey_grid(w: int, h: int) -> bytearray:
    """All-unknown bytemap (BreezySLAM init state)."""
    return bytearray([GREY] * (w * h))


def _set(grid: bytearray, w: int, x: int, y: int, val: int) -> None:
    grid[y * w + x] = val


def _draw_vline(grid: bytearray, w: int, x: int, y0: int, y1: int) -> None:
    for y in range(y0, y1 + 1):
        _set(grid, w, x, y, WALL)


def _draw_hline(grid: bytearray, w: int, y: int, x0: int, x1: int) -> None:
    for x in range(x0, x1 + 1):
        _set(grid, w, x, y, WALL)


# ----------------------------------------------------------------------
# Coordinate transforms
# ----------------------------------------------------------------------


def test_world_pixel_round_trip_at_origin():
    px, py = world_to_pixel(0.0, 0.0, 80, 80, 50.0)
    x, y = pixel_to_world(px, py, 80, 80, 50.0)
    assert (px, py) == (40, 40)
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(0.0, abs=1e-9)


def test_world_pixel_y_axis_inverted():
    # +y_mm forward should land on a smaller py (rows go down).
    _, py_top = world_to_pixel(0.0, 1000.0, 80, 80, 50.0)
    _, py_bottom = world_to_pixel(0.0, -1000.0, 80, 80, 50.0)
    assert py_top < py_bottom


# ----------------------------------------------------------------------
# Open room sanity
# ----------------------------------------------------------------------


def test_open_room_plans_decreasing_distance():
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    result = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=0,
    )
    assert result.ok, result.reason
    assert len(result.waypoints_mm) >= 2
    # Last waypoint should be close to (and not past) the goal cell.
    last = result.waypoints_mm[-1]
    assert math.hypot(last[0] - 1000.0, last[1]) < scale * 1.5
    # Path length matches a roughly-straight 2 m run; allow 25 % slack.
    assert 1500.0 <= result.path_length_mm <= 2500.0


# ----------------------------------------------------------------------
# Corridor: forces a detour through a gap in a vertical wall
# ----------------------------------------------------------------------


def test_corridor_path_crosses_gap_row():
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    # Vertical wall at x=40, gap from y=70..72 (low part of the map).
    _draw_vline(grid, w, 40, 0, 69)
    _draw_vline(grid, w, 40, 73, h - 1)
    result = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),     # left of wall, near map centre
        goal_mm=(1000.0, 0.0),       # right of wall, near map centre
        footprint_radius_mm=0,
        min_passage_width_mm=0,
    )
    assert result.ok, result.reason
    pixel_path = [
        world_to_pixel(x, y, w, h, scale)
        for x, y in result.waypoints_mm
    ]
    crosses_gap_row = any(70 <= py <= 72 and px == 40 for px, py in pixel_path)
    crosses_via_corner = any(70 <= py <= 72 for px, py in pixel_path)
    assert crosses_gap_row or crosses_via_corner, (
        f"Path did not route through the gap: {pixel_path}"
    )


# ----------------------------------------------------------------------
# U-shape: goal reachable but only via a detour
# ----------------------------------------------------------------------


def test_u_shape_not_reported_unreachable():
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    # U-shape that opens upward (toward smaller y -> +y mm in world):
    #   left vertical wall  x = 35,  y in [40..70]
    #   right vertical wall x = 45,  y in [40..70]
    #   bottom horizontal wall y = 70, x in [35..45]
    _draw_vline(grid, w, 35, 40, 70)
    _draw_vline(grid, w, 45, 40, 70)
    _draw_hline(grid, w, 70, 35, 45)
    # Place the goal INSIDE the U (just above the bottom wall) and
    # the start outside; the planner must route around.
    result = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1500.0, 0.0),
        goal_mm=(0.0, -1300.0),   # inside the U (y_mm < 0)
        footprint_radius_mm=0,
        min_passage_width_mm=0,
    )
    assert result.ok, result.reason
    # Length must exceed the geometric chord (sqrt(1.5^2 + 1.3^2) m)
    # because the planner had to go around the bottom of the U.
    chord_mm = math.hypot(1500.0, 1300.0)
    assert result.path_length_mm > chord_mm * 1.1


# ----------------------------------------------------------------------
# Goal-in-wall handling
# ----------------------------------------------------------------------


def test_goal_in_wall_snaps_to_nearest_free():
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    # Two-pixel-thick wall at x=60 so the snap can't just pick the
    # next pixel over (it'd still be inside the wall).
    _draw_vline(grid, w, 60, 30, 50)
    _draw_vline(grid, w, 61, 30, 50)
    # Goal sits on the 2-px wall; snap_radius generous so the planner
    # snaps it free.
    result = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),       # px=60, py=40 -> wall
        footprint_radius_mm=0,
        min_passage_width_mm=0,
        snap_radius_mm=500,
    )
    assert result.ok, result.reason
    assert result.snapped_goal_px is not None
    sx, sy = result.snapped_goal_px
    # The byte at the snapped pixel must be free.
    assert grid[sy * w + sx] == FREE


def test_goal_in_wall_without_snap_returns_goal_in_wall():
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    _draw_vline(grid, w, 60, 30, 50)
    _draw_vline(grid, w, 61, 30, 50)
    result = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=0,
        snap_radius_mm=0,
    )
    assert not result.ok
    assert result.reason == "goal_in_wall"


# ----------------------------------------------------------------------
# Footprint inflation behaviour
# ----------------------------------------------------------------------


def test_footprint_inflation_blocks_narrow_corridor():
    """A 1-px-wide gap is fine with no inflation, blocked with
    footprint=200 mm at scale=50 mm/px (= 4 px dilation).
    """
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    # Solid vertical wall with a single-pixel gap at y=40.
    _draw_vline(grid, w, 40, 0, 39)
    _draw_vline(grid, w, 40, 41, h - 1)

    slim = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=0,
    )
    assert slim.ok, slim.reason

    fat = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=200,    # 4 px dilation
        min_passage_width_mm=0,
        snap_radius_mm=0,
    )
    assert not fat.ok
    # Either no_path (gap too narrow after dilation) or the goal/
    # start ended up dilated into a wall - both are correct refusals.
    assert fat.reason in {"no_path", "start_in_wall", "goal_in_wall"}


# ----------------------------------------------------------------------
# Minimum passage width (2 ft / 610 mm default)
# ----------------------------------------------------------------------


def test_passage_width_default_blocks_one_foot_corridor():
    """At scale=50 mm/px, a 6-px-wide gap is ~300 mm of corridor -
    less than the default 2-ft / 610 mm passage. The planner must
    refuse it even with `footprint_radius_mm=0`, because the passage
    width policy is independent of the body geometry.
    """
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    # 6-px-wide horizontal gap (rows 38..43) in an otherwise solid
    # vertical wall at x=40. That's 300 mm of corridor.
    _draw_vline(grid, w, 40, 0, 37)
    _draw_vline(grid, w, 40, 44, h - 1)

    refused = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=610,    # default 2 ft
        snap_radius_mm=0,
    )
    assert not refused.ok, "1-foot gap should be refused by 2-ft policy"
    assert refused.reason in {"no_path", "start_in_wall", "goal_in_wall"}


def test_passage_width_default_allows_two_foot_corridor():
    """A 14-px gap = 700 mm = 2 ft 3.5 in of corridor must pass the
    default 2-ft policy with footprint_radius_mm=0.
    """
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    # 14-px-wide gap centred around y=40 (rows 33..46).
    _draw_vline(grid, w, 40, 0, 32)
    _draw_vline(grid, w, 40, 47, h - 1)

    ok = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=610,
    )
    assert ok.ok, ok.reason


def test_passage_width_overrides_smaller_footprint():
    """Even if the operator sets a tiny footprint, the passage-width
    floor still bites. This is the safety-policy use case: the
    policy is wider than the bot, by intent.
    """
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    _draw_vline(grid, w, 40, 0, 37)
    _draw_vline(grid, w, 40, 44, h - 1)   # 300 mm gap

    refused = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=50,   # tiny "1 px" body
        min_passage_width_mm=610,
        snap_radius_mm=0,
    )
    assert not refused.ok
    assert refused.reason in {"no_path", "start_in_wall", "goal_in_wall"}


def test_passage_width_zero_disables_policy():
    """Setting `min_passage_width_mm=0` falls back to footprint-only
    behaviour - useful for tests + for the rare deployment where
    operator policy says "thread anything you fit through".
    """
    w = h = 80
    scale = 50.0
    grid = _open_grid(w, h)
    _draw_vline(grid, w, 40, 0, 37)
    _draw_vline(grid, w, 40, 44, h - 1)   # 300 mm gap

    ok = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-1000.0, 0.0),
        goal_mm=(1000.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=0,
    )
    assert ok.ok, ok.reason


# ----------------------------------------------------------------------
# Empty / malformed input
# ----------------------------------------------------------------------


def test_empty_grid_returns_empty_grid():
    result = plan_path(b"", 0, 0, 50.0, (0.0, 0.0), (0.0, 0.0))
    assert not result.ok
    assert result.reason == "empty_grid"


def test_unknown_grid_can_still_route():
    """A pristine SLAM map (all grey) should still let the bot plan
    a route - we just penalise grey pixels in the cost.
    """
    w = h = 60
    scale = 50.0
    grid = _grey_grid(w, h)
    result = plan_path(
        bytes(grid), w, h, scale,
        start_mm=(-500.0, 0.0),
        goal_mm=(500.0, 0.0),
        footprint_radius_mm=0,
        min_passage_width_mm=0,
    )
    assert result.ok, result.reason
    assert len(result.waypoints_mm) >= 2
