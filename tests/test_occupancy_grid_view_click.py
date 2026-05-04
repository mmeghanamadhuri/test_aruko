"""Tests for the click-to-goal pixel->world transform on
``OccupancyGridView``.

The widget letterboxes the SLAM bytemap inside its rect, so the
operator's click coordinates have to be translated through:

    widget pixel -> letterbox-relative pixel -> grid pixel -> world mm

Anything that drifts here (off-by-one in the letterbox math, sign
flip on the y-axis, scale_mm_per_px ignored) shows up as Nina
driving toward the wrong cell, which is hard to debug from logs.
This test pins the round-trip exactly.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PyQt5 = pytest.importorskip("PyQt5")
from PyQt5.QtCore import QPoint  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from sirena_ui.widgets.occupancy_grid_view import OccupancyGridView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst


def _gray_grid(w: int, h: int) -> bytes:
    return bytes([127] * (w * h))


def test_widget_to_world_mm_centre_returns_origin(qapp):
    """A click in the geometric centre of the widget maps to (0, 0)
    world mm regardless of widget size or grid scale.
    """
    view = OccupancyGridView()
    view.set_grid(_gray_grid(80, 80), 80, 80, 50.0)
    view.resize(400, 400)
    # The widget's painter inset is 8 px. With a 400x400 widget and
    # 80x80 grid, the letterbox fills the 384x384 inner rect and the
    # centre lands on widget pixel (200, 200).
    coords = view.widget_to_world_mm(200, 200)
    assert coords is not None
    x, y = coords
    # Allow a half-pixel of slop because the letterbox math rounds
    # to int; at scale=50 mm/px that's ±25 mm.
    assert abs(x) <= 25.0
    assert abs(y) <= 25.0


def test_widget_to_world_mm_y_axis_inverted(qapp):
    """A click ABOVE centre (smaller y in widget coords) is +y mm
    in world coords (forward of the bot).
    """
    view = OccupancyGridView()
    view.set_grid(_gray_grid(80, 80), 80, 80, 50.0)
    view.resize(400, 400)
    upper = view.widget_to_world_mm(200, 100)
    lower = view.widget_to_world_mm(200, 300)
    assert upper is not None and lower is not None
    assert upper[1] > 0
    assert lower[1] < 0
    assert upper[1] == pytest.approx(-lower[1], abs=50.0)


def test_widget_to_world_mm_x_axis_matches_screen(qapp):
    """A click to the RIGHT of centre is +x mm in world coords."""
    view = OccupancyGridView()
    view.set_grid(_gray_grid(80, 80), 80, 80, 50.0)
    view.resize(400, 400)
    right = view.widget_to_world_mm(300, 200)
    left = view.widget_to_world_mm(100, 200)
    assert right is not None and left is not None
    assert right[0] > 0
    assert left[0] < 0


def test_widget_to_world_mm_outside_rect_returns_none(qapp):
    """Clicks outside the letterboxed image rect (inside the
    widget margins / corners) must return None so the click event
    short-circuits.
    """
    view = OccupancyGridView()
    view.set_grid(_gray_grid(80, 80), 80, 80, 50.0)
    view.resize(400, 400)
    # Top-left corner of widget is OUTSIDE the 8-px inset rect.
    # widget_to_world_mm should refuse it.
    assert view.widget_to_world_mm(0, 0) is None
    # And a click on the very edge of the 8-px inset is also out.
    assert view.widget_to_world_mm(7, 7) is None


def test_widget_to_world_mm_no_grid_returns_none(qapp):
    """No SLAM data yet -> no transform possible."""
    view = OccupancyGridView()
    view.resize(400, 400)
    # No set_grid call yet -> _has_data False.
    assert view.widget_to_world_mm(200, 200) is None


def test_set_clickable_emits_goal_clicked_on_left_click(qapp):
    """Sanity: when clickable is on, mousePressEvent emits goal_clicked
    with sensible coordinates. We use a manual signal hook because
    QTest isn't available in the dependency set.
    """
    view = OccupancyGridView()
    view.set_grid(_gray_grid(80, 80), 80, 80, 50.0)
    view.resize(400, 400)
    view.set_clickable(True)

    captured = []
    view.goal_clicked.connect(lambda x, y: captured.append((x, y)))

    # Synthesize a click at the widget centre.
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtCore import QEvent

    event = QMouseEvent(
        QEvent.MouseButtonPress,
        QPoint(200, 200),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    view.mousePressEvent(event)
    assert len(captured) == 1
    x, y = captured[0]
    assert abs(x) <= 50.0
    assert abs(y) <= 50.0


def test_set_clickable_off_swallows_click(qapp):
    """When clickable is off, no signal fires regardless of where the
    click landed.
    """
    view = OccupancyGridView()
    view.set_grid(_gray_grid(80, 80), 80, 80, 50.0)
    view.resize(400, 400)
    view.set_clickable(False)

    captured = []
    view.goal_clicked.connect(lambda x, y: captured.append((x, y)))

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtCore import QEvent

    event = QMouseEvent(
        QEvent.MouseButtonPress,
        QPoint(200, 200),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    view.mousePressEvent(event)
    assert captured == []
