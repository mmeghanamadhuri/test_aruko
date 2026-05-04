"""A* path planner over the BreezySLAM occupancy bytemap.

Inputs (from `SlamSnapshot`):

  * `grid_bytes`       - flat bytes, width*height entries.
  * `width`, `height`  - pixel dimensions (square in our case, but we
                         don't assume it).
  * `scale_mm_per_px`  - world millimetres per pixel.

BreezySLAM convention for a byte:

      0   = occupied (wall)
    127   = unknown (initial fill)
    255   = free space

We treat anything <= `occupancy_threshold` as a wall (configurable so
operators can crank the strictness), then **dilate** the wall set by
`ceil(footprint_radius_mm / scale_mm_per_px)` pixels so paths leave a
bot-sized buffer between Nina and the geometry.

Cost model:

  * Step cost = euclidean (1 for axis moves, sqrt(2) for diagonals)
                multiplied by 1.0 for known-free cells and
                `unknown_pixel_cost` for grey/unknown cells. So A*
                will happily route into unexplored regions but will
                prefer to stay in mapped corridors when given a
                choice.
  * Heuristic = octile (admissible for 8-connected grids, dominates
                manhattan, never overestimates, faster to expand than
                pure euclidean).

Output: a list of waypoints in **world millimetres** (origin = map
centre, +x right, +y up - the same frame `SlamSnapshot.world_to_pixel`
uses), simplified by collinear-point removal so the goto pilot
doesn't pump tiny corrections into the wheels.

Failure modes are surfaced as a typed `PlanResult`:

  * `ok=True`   -> waypoints + reason (e.g. "8 waypoints, 4.2 m").
  * `ok=False`  -> reason in {"goal_in_wall", "start_in_wall",
                              "no_path", "out_of_bounds", "empty_grid"}
                   so the caller (UI / pilot) can localise the message.

This module is pure-python on purpose: it has to run on the Jetson
without numpy/scipy on dev hosts that don't import cv2 (laptop CI). A
1000x1000 occupancy grid plans in well under 100 ms; we don't need a
C extension here.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


log = logging.getLogger("nina.navigation.path_planner")


# ----------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------


# BreezySLAM occupancy semantics. The numbers are public API of the
# bytemap so we name them here rather than scattering literals.
OCCUPIED_THRESHOLD = 80     # <= this byte = wall (default; tunable)
UNKNOWN_LO = 110            # 110..145 = unknown grey region
UNKNOWN_HI = 145
FREE_THRESHOLD = 200        # >= this byte = definitely free


@dataclass
class PlanResult:
    """Outcome of a single planner invocation."""

    ok: bool
    waypoints_mm: List[Tuple[float, float]] = field(default_factory=list)
    reason: str = ""
    # When ok=False but we managed to snap the goal to a nearby free
    # cell, the snapped pixel comes back so the UI can render an
    # "actual goal" pin at a slightly different spot than the click.
    snapped_goal_px: Optional[Tuple[int, int]] = None
    # Diagnostics for tests / logs.
    nodes_expanded: int = 0
    path_length_mm: float = 0.0


# ----------------------------------------------------------------------
# Coordinate helpers (mirror SlamSnapshot.world_to_pixel exactly)
# ----------------------------------------------------------------------


def world_to_pixel(
    x_mm: float, y_mm: float, width: int, height: int, scale_mm_per_px: float
) -> Tuple[int, int]:
    """World mm -> pixel coords. Origin at the map centre; +x right,
    +y up; the grid stores y growing DOWN, so we flip.
    """
    cx = width / 2.0
    cy = height / 2.0
    px = int(round(cx + x_mm / max(scale_mm_per_px, 1e-6)))
    py = int(round(cy - y_mm / max(scale_mm_per_px, 1e-6)))
    return px, py


def pixel_to_world(
    px: int, py: int, width: int, height: int, scale_mm_per_px: float
) -> Tuple[float, float]:
    """Inverse of `world_to_pixel`. Returns the mm coordinates of the
    pixel's CENTRE (not its top-left corner).
    """
    cx = width / 2.0
    cy = height / 2.0
    x_mm = (px - cx) * scale_mm_per_px
    y_mm = (cy - py) * scale_mm_per_px
    return x_mm, y_mm


# ----------------------------------------------------------------------
# Grid preprocessing (occupancy + dilation)
# ----------------------------------------------------------------------


def _idx(px: int, py: int, width: int) -> int:
    return py * width + px


def _build_occupied_mask(
    grid_bytes: bytes,
    width: int,
    height: int,
    occupancy_threshold: int,
) -> bytearray:
    """Returns a flat 0/1 mask where 1 = wall (raw, no dilation yet)."""
    mask = bytearray(width * height)
    for i, b in enumerate(grid_bytes):
        if b <= occupancy_threshold:
            mask[i] = 1
    return mask


def _dilate(
    mask: bytearray, width: int, height: int, radius_px: int
) -> bytearray:
    """Square dilation by `radius_px`. Returns a new mask. We use a
    Manhattan-rectangle structuring element rather than a euclidean
    disk because (a) it's a single bitwise pass, (b) the difference
    in the resulting "buffer" is sub-pixel for radii up to ~10 px,
    which covers any sane footprint vs scale_mm_per_px combo.

    Using two 1-D passes (horizontal then vertical) so the work is
    O(width*height*radius) rather than the naive O(width*height*radius^2).
    """
    if radius_px <= 0:
        return bytearray(mask)

    # Horizontal pass: for each row, mark a pixel as occupied if any
    # pixel within `radius_px` to the left or right is occupied in
    # the input.
    horiz = bytearray(width * height)
    for y in range(height):
        row_start = y * width
        # Build a small running count of occupied cells in the
        # sliding window so each pixel is O(1).
        count = 0
        for x in range(min(radius_px + 1, width)):
            count += mask[row_start + x]
        for x in range(width):
            horiz[row_start + x] = 1 if count > 0 else 0
            # advance window: drop leftmost, add new right edge
            left_idx = x - radius_px
            right_idx = x + radius_px + 1
            if left_idx >= 0:
                count -= mask[row_start + left_idx]
            if right_idx < width:
                count += mask[row_start + right_idx]

    # Vertical pass over `horiz` -> final dilation.
    out = bytearray(width * height)
    for x in range(width):
        count = 0
        for y in range(min(radius_px + 1, height)):
            count += horiz[y * width + x]
        for y in range(height):
            out[y * width + x] = 1 if count > 0 else 0
            top_idx = y - radius_px
            bot_idx = y + radius_px + 1
            if top_idx >= 0:
                count -= horiz[top_idx * width + x]
            if bot_idx < height:
                count += horiz[bot_idx * width + x]
    return out


def _unknown_mask(grid_bytes: bytes, width: int, height: int) -> bytearray:
    """1 where the cell is in BreezySLAM's "unknown grey" band."""
    mask = bytearray(width * height)
    for i, b in enumerate(grid_bytes):
        if UNKNOWN_LO <= b <= UNKNOWN_HI:
            mask[i] = 1
    return mask


def _nearest_free(
    occ: bytearray,
    width: int,
    height: int,
    px: int,
    py: int,
    max_radius_px: int,
) -> Optional[Tuple[int, int]]:
    """BFS-grow a square ring around (px, py) and return the closest
    cell that is NOT occupied. Returns None if everything within
    `max_radius_px` is a wall.
    """
    if not (0 <= px < width and 0 <= py < height):
        return None
    if occ[_idx(px, py, width)] == 0:
        return px, py
    for r in range(1, max_radius_px + 1):
        x0 = max(0, px - r)
        x1 = min(width - 1, px + r)
        y0 = max(0, py - r)
        y1 = min(height - 1, py + r)
        # Walk the ring perimeter - top + bottom rows then left/right
        # columns of just this radius (avoids re-checking interior).
        candidates: List[Tuple[int, int]] = []
        for x in range(x0, x1 + 1):
            candidates.append((x, y0))
            if y1 != y0:
                candidates.append((x, y1))
        for y in range(y0 + 1, y1):
            candidates.append((x0, y))
            if x1 != x0:
                candidates.append((x1, y))
        # Pick the candidate with the smallest euclidean distance to
        # (px, py) so "nearest" is geometrically nearest, not
        # "first-visited in scan order".
        best: Optional[Tuple[int, int]] = None
        best_d2 = -1
        for cx, cy in candidates:
            if occ[_idx(cx, cy, width)] != 0:
                continue
            d2 = (cx - px) * (cx - px) + (cy - py) * (cy - py)
            if best is None or d2 < best_d2:
                best = (cx, cy)
                best_d2 = d2
        if best is not None:
            return best
    return None


# ----------------------------------------------------------------------
# A* core
# ----------------------------------------------------------------------


# 8-connected neighbours: (dx, dy, base_cost).
_NEIGHBOURS = (
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)),
    (-1, -1, math.sqrt(2)),
)


def _octile(ax: int, ay: int, bx: int, by: int) -> float:
    """Octile distance heuristic, admissible for 8-connected grids."""
    dx = abs(ax - bx)
    dy = abs(ay - by)
    return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)


def _astar(
    occ: bytearray,
    unknown: bytearray,
    width: int,
    height: int,
    start: Tuple[int, int],
    goal: Tuple[int, int],
    unknown_cost: float,
    max_nodes: int,
) -> Tuple[List[Tuple[int, int]], int]:
    """Returns (pixel_path, nodes_expanded). Empty path -> no route."""
    sx, sy = start
    gx, gy = goal
    if start == goal:
        return [start], 0

    open_heap: List[Tuple[float, int, int, int]] = []
    counter = 0
    heapq.heappush(open_heap, (_octile(sx, sy, gx, gy), counter, sx, sy))

    came_from = {(sx, sy): None}
    g_score = {(sx, sy): 0.0}
    expanded = 0

    while open_heap:
        _, _, cx, cy = heapq.heappop(open_heap)
        if (cx, cy) == (gx, gy):
            # Reconstruct.
            path: List[Tuple[int, int]] = []
            cur: Optional[Tuple[int, int]] = (cx, cy)
            while cur is not None:
                path.append(cur)
                cur = came_from[cur]
            path.reverse()
            return path, expanded

        expanded += 1
        if expanded > max_nodes:
            log.info(
                "A* aborted: nodes_expanded=%d > max_nodes=%d",
                expanded, max_nodes,
            )
            break
        cur_g = g_score[(cx, cy)]
        for dx, dy, base in _NEIGHBOURS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if occ[_idx(nx, ny, width)]:
                continue
            # Diagonal blocked by an axis wall? Skip -- otherwise the
            # bot would "cut a corner" through a 1-px gap that its
            # actual chassis can't fit through. The dilation step
            # already gives us the safety margin, so this is just
            # belt-and-braces for the 1-px-thick remnant.
            if dx != 0 and dy != 0:
                if (
                    occ[_idx(cx + dx, cy, width)]
                    or occ[_idx(cx, cy + dy, width)]
                ):
                    continue
            step = base
            if unknown[_idx(nx, ny, width)]:
                step *= unknown_cost
            tentative = cur_g + step
            existing = g_score.get((nx, ny))
            if existing is not None and tentative >= existing:
                continue
            came_from[(nx, ny)] = (cx, cy)
            g_score[(nx, ny)] = tentative
            counter += 1
            f = tentative + _octile(nx, ny, gx, gy)
            heapq.heappush(open_heap, (f, counter, nx, ny))

    return [], expanded


# ----------------------------------------------------------------------
# Path simplification (collinear-point removal)
# ----------------------------------------------------------------------


def _simplify(path_px: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Drop intermediate points that lie on the same straight line as
    their neighbours. The pure-pursuit follower only needs the
    direction-change vertices, and a simplified path is much cheaper
    to render on the UI grid + ship over the link daemon.
    """
    if len(path_px) <= 2:
        return list(path_px)
    simplified: List[Tuple[int, int]] = [path_px[0]]
    for i in range(1, len(path_px) - 1):
        prev = simplified[-1]
        cur = path_px[i]
        nxt = path_px[i + 1]
        # Cross product of (cur-prev) x (nxt-cur). Zero -> collinear.
        cross = (cur[0] - prev[0]) * (nxt[1] - cur[1]) - (
            cur[1] - prev[1]
        ) * (nxt[0] - cur[0])
        if cross != 0:
            simplified.append(cur)
    simplified.append(path_px[-1])
    return simplified


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def plan_path(
    grid_bytes: bytes,
    width: int,
    height: int,
    scale_mm_per_px: float,
    start_mm: Tuple[float, float],
    goal_mm: Tuple[float, float],
    *,
    footprint_radius_mm: int = 250,
    unknown_pixel_cost: float = 1.5,
    occupancy_threshold: int = OCCUPIED_THRESHOLD,
    snap_radius_mm: int = 1500,
    max_nodes: int = 250_000,
) -> PlanResult:
    """Plan an obstacle-aware path from `start_mm` to `goal_mm`.

    Returns a `PlanResult` whose `waypoints_mm` are world millimetres
    in the same frame as `start_mm`/`goal_mm` (origin = map centre).
    The first waypoint is the bot's current cell centre and the last
    is the (possibly snapped) goal cell centre.

    `snap_radius_mm` controls how far we'll search for a free cell
    when the operator clicks on a wall. Set to 0 to disable snapping
    (then a wall-click returns `goal_in_wall`).
    """
    if width <= 0 or height <= 0 or len(grid_bytes) != width * height:
        return PlanResult(False, reason="empty_grid")

    occ_raw = _build_occupied_mask(grid_bytes, width, height, occupancy_threshold)
    unknown = _unknown_mask(grid_bytes, width, height)

    radius_px = max(0, int(math.ceil(footprint_radius_mm / max(scale_mm_per_px, 1e-6))))
    occ = _dilate(occ_raw, width, height, radius_px)

    sx, sy = world_to_pixel(start_mm[0], start_mm[1], width, height, scale_mm_per_px)
    gx, gy = world_to_pixel(goal_mm[0], goal_mm[1], width, height, scale_mm_per_px)

    if not (0 <= sx < width and 0 <= sy < height):
        return PlanResult(False, reason="out_of_bounds")
    if not (0 <= gx < width and 0 <= gy < height):
        return PlanResult(False, reason="out_of_bounds")

    # If the bot itself is "in a wall" after dilation, the planner
    # can't find any moves. This is real on a freshly-bumped pose
    # estimate or a very early SLAM map - we relax the start by
    # searching the un-dilated occupancy mask, so the pilot can at
    # least try to inch out.
    if occ[_idx(sx, sy, width)]:
        relaxed = _nearest_free(
            occ_raw, width, height, sx, sy,
            max_radius_px=max(1, radius_px),
        )
        if relaxed is None:
            return PlanResult(False, reason="start_in_wall")
        sx, sy = relaxed

    snapped: Optional[Tuple[int, int]] = None
    if occ[_idx(gx, gy, width)]:
        if snap_radius_mm <= 0:
            return PlanResult(False, reason="goal_in_wall")
        snap_px = max(1, int(math.ceil(snap_radius_mm / max(scale_mm_per_px, 1e-6))))
        cand = _nearest_free(occ, width, height, gx, gy, max_radius_px=snap_px)
        if cand is None:
            return PlanResult(False, reason="goal_in_wall")
        gx, gy = cand
        snapped = (gx, gy)

    pixel_path, expanded = _astar(
        occ, unknown, width, height,
        start=(sx, sy), goal=(gx, gy),
        unknown_cost=unknown_pixel_cost,
        max_nodes=max_nodes,
    )
    if not pixel_path:
        return PlanResult(
            False, reason="no_path",
            snapped_goal_px=snapped, nodes_expanded=expanded,
        )

    pixel_path = _simplify(pixel_path)
    waypoints_mm = [
        pixel_to_world(px, py, width, height, scale_mm_per_px)
        for px, py in pixel_path
    ]

    # Total path length in mm (after simplification).
    length_mm = 0.0
    for (ax, ay), (bx, by) in zip(waypoints_mm[:-1], waypoints_mm[1:]):
        length_mm += math.hypot(bx - ax, by - ay)

    return PlanResult(
        ok=True,
        waypoints_mm=waypoints_mm,
        reason=f"{len(waypoints_mm)} waypoints, {length_mm/1000.0:.2f} m",
        snapped_goal_px=snapped,
        nodes_expanded=expanded,
        path_length_mm=length_mm,
    )


# ----------------------------------------------------------------------
# Test-only helpers (kept here so the unit tests don't reach into
# private names; production callers should use plan_path() above).
# ----------------------------------------------------------------------


def _debug_dilated_mask(
    grid_bytes: bytes, width: int, height: int,
    scale_mm_per_px: float, footprint_radius_mm: int,
    occupancy_threshold: int = OCCUPIED_THRESHOLD,
) -> bytes:
    occ_raw = _build_occupied_mask(grid_bytes, width, height, occupancy_threshold)
    radius_px = max(
        0, int(math.ceil(footprint_radius_mm / max(scale_mm_per_px, 1e-6)))
    )
    return bytes(_dilate(occ_raw, width, height, radius_px))


def waypoints_to_pixels(
    waypoints_mm: Iterable[Tuple[float, float]],
    width: int,
    height: int,
    scale_mm_per_px: float,
) -> List[Tuple[int, int]]:
    """Convenience used by the UI overlay - drop a list of mm
    waypoints into pixel coords for `paintEvent`.
    """
    return [
        world_to_pixel(x, y, width, height, scale_mm_per_px)
        for x, y in waypoints_mm
    ]
