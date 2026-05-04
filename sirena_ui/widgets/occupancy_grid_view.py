"""Custom QWidget that renders a BreezySLAM occupancy grid.

The grid uses BreezySLAM's convention:
    0   = occupied (wall)
    255 = free space
    ~127 = unknown (initial fill)

We render that to a QImage at the widget's resolution and overlay
Nina's pose as a Sirena-red triangle.

The widget is also **clickable** for goto navigation:

  * Operator taps a free-ish pixel.
  * `mousePressEvent` converts the pixel back into world millimetres
    using the same letterbox math the pose triangle uses.
  * The widget emits `goal_clicked(x_mm, y_mm)` and the Map / Perception
    screen forwards that to `AutonomyController.set_goal()`.

Optional overlays for goto:

  * `set_goal(x_mm, y_mm)`     - draws a red flag at the goal point.
  * `set_path(waypoints_mm)`   - draws a thin polyline through the
                                 planner's waypoints in dashed red.
  * `clear_goal()`             - removes both overlays.

Why a custom widget rather than `QGraphicsScene`? At 800x800 pixels
and 5 Hz the simplest approach (one QImage, drawn in `paintEvent`)
is the one that gives us the cleanest path on Jetson Nano. Heavier
visualisations can swap in later without changing the screen wiring.
"""

from __future__ import annotations

from math import cos, radians, sin
from typing import List, Optional, Tuple

from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPolygon,
)
from PyQt5.QtWidgets import QSizePolicy, QWidget


class OccupancyGridView(QWidget):
    # (x_mm, y_mm) in the SLAM map frame (origin = map centre).
    goal_clicked = pyqtSignal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Was 360 x 360 - too large for the 1024 x 600 panel after the
        # sidebar (160), the side card (~330), and chrome (~110). 200
        # is the floor we need so the grid still fits the Perception
        # screen's three-column layout (each column ~280 px wide,
        # minus 8 + 8 card padding = ~264 inner). The QImage scales
        # up to fill whatever extra space the parent layout grants us.
        self.setMinimumSize(200, 200)
        # Expanding both axes so the layout actually grants us all
        # available space - default Preferred would be honoured by
        # most layouts, but in tight containers (Perception lidar
        # card) Preferred sometimes leaves the widget at sizeHint
        # while a sibling-policy widget eats the leftover. Expanding
        # is a hard "I want everything" so the lidar pane fills the
        # card the way the RGB and Depth viewports do.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #f5f5f7; border-radius: 12px;")
        self._image: Optional[QImage] = None
        self._image_width = 0
        self._image_height = 0
        self._scale_mm_per_px = 1.0
        self._pose_x_mm = 0.0
        self._pose_y_mm = 0.0
        self._pose_theta_deg = 0.0
        self._has_data = False
        self._clickable = False
        self._goal_mm: Optional[Tuple[float, float]] = None
        self._snapped_goal_mm: Optional[Tuple[float, float]] = None
        self._path_mm: List[Tuple[float, float]] = []
        self._placeholder = (
            "Lidar / IR / Ultrasonic sensors not connected.\n"
            "Toggle Autonomous mode (or Start mapping) once they're wired up."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_grid(
        self,
        grid_bytes: bytes,
        width: int,
        height: int,
        scale_mm_per_px: float,
    ) -> None:
        if width <= 0 or height <= 0 or len(grid_bytes) != width * height:
            return
        # QImage.Format_Grayscale8 wants stride=width, no padding.
        # bytes() gives us a Python buffer that QImage can keep.
        img = QImage(
            grid_bytes, width, height, width, QImage.Format_Grayscale8
        ).copy()
        self._image = img
        self._image_width = width
        self._image_height = height
        self._scale_mm_per_px = max(0.001, float(scale_mm_per_px))
        self._has_data = True
        self.update()

    def set_pose(self, x_mm: float, y_mm: float, theta_deg: float) -> None:
        self._pose_x_mm = float(x_mm)
        self._pose_y_mm = float(y_mm)
        self._pose_theta_deg = float(theta_deg)
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if not self._has_data:
            self.update()

    def clear(self) -> None:
        self._image = None
        self._has_data = False
        self.update()

    # ---- goto overlay API --------------------------------------------

    def set_clickable(self, on: bool) -> None:
        """Enable / disable click-to-set-goal. The grid is read-only
        until a screen explicitly opts in (Map screen with goto
        enabled, Perception screen lidar pane). Disabled by default
        so accidentally tapping the grid never does anything
        dangerous.
        """
        self._clickable = bool(on)
        self.setCursor(Qt.PointingHandCursor if self._clickable else Qt.ArrowCursor)

    def set_goal(
        self,
        x_mm: Optional[float],
        y_mm: Optional[float],
        snapped_x_mm: Optional[float] = None,
        snapped_y_mm: Optional[float] = None,
    ) -> None:
        """Draw a flag at the goal point.

        ``snapped_*`` is optional and represents where the planner
        actually routed to when the operator's click fell on an
        obstacle. When set, both pins render: the click location as
        a hollow ring and the snapped pin as the filled flag.
        """
        if x_mm is None or y_mm is None:
            self._goal_mm = None
            self._snapped_goal_mm = None
        else:
            self._goal_mm = (float(x_mm), float(y_mm))
            if snapped_x_mm is not None and snapped_y_mm is not None:
                self._snapped_goal_mm = (float(snapped_x_mm), float(snapped_y_mm))
            else:
                self._snapped_goal_mm = None
        self.update()

    def set_path(self, waypoints_mm: List[Tuple[float, float]]) -> None:
        self._path_mm = [(float(x), float(y)) for x, y in waypoints_mm]
        self.update()

    def clear_goal(self) -> None:
        self._goal_mm = None
        self._snapped_goal_mm = None
        self._path_mm = []
        self.update()

    # ---- Coordinate transform (used by tests + paint) ----------------

    def widget_to_world_mm(self, wx: int, wy: int) -> Optional[Tuple[float, float]]:
        """Convert widget-pixel coords to SLAM-frame world mm.

        Returns ``None`` if the widget has no grid yet OR the click
        landed outside the letterboxed grid rect (in the surrounding
        margins).
        """
        if not self._has_data or self._image is None:
            return None
        rect = self.rect().adjusted(8, 8, -8, -8)
        img_w = self._image_width
        img_h = self._image_height
        target_w, target_h = self._fit(rect.width(), rect.height(), img_w, img_h)
        ox = rect.left() + (rect.width() - target_w) // 2
        oy = rect.top() + (rect.height() - target_h) // 2
        if not (ox <= wx < ox + target_w and oy <= wy < oy + target_h):
            return None
        scale_x = target_w / float(img_w)
        scale_y = target_h / float(img_h)
        # Convert widget coords -> grid pixel coords -> world mm.
        # We invert the same maths `world_to_pixel` does in
        # SlamSnapshot so this stays consistent across the pose
        # triangle, the goal flag, and the planner.
        cx_world = img_w / 2.0
        cy_world = img_h / 2.0
        gpx = (wx - ox) / scale_x   # pixel coord in the SLAM grid
        gpy = (wy - oy) / scale_y
        x_mm = (gpx - cx_world) * self._scale_mm_per_px
        y_mm = (cy_world - gpy) * self._scale_mm_per_px
        return float(x_mm), float(y_mm)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if not self._clickable:
            super().mousePressEvent(event)
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        coords = self.widget_to_world_mm(event.x(), event.y())
        if coords is None:
            super().mousePressEvent(event)
            return
        self.goal_clicked.emit(coords[0], coords[1])
        event.accept()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(8, 8, -8, -8)

        p.fillRect(rect, QColor("#f0f0f3"))
        p.setPen(QPen(QColor("#e3e3e6"), 1))
        step = 28
        for x in range(rect.left(), rect.right(), step):
            p.drawLine(x, rect.top(), x, rect.bottom())
        for y in range(rect.top(), rect.bottom(), step):
            p.drawLine(rect.left(), y, rect.right(), y)

        if self._image is None or not self._has_data:
            p.setPen(QColor("#8e8e93"))
            p.drawText(rect, Qt.AlignCenter, self._placeholder)
            return

        # Letterbox the grid into the widget rect.
        img = self._image
        img_w = img.width()
        img_h = img.height()
        target = self._fit(rect.width(), rect.height(), img_w, img_h)
        ox = rect.left() + (rect.width() - target[0]) // 2
        oy = rect.top() + (rect.height() - target[1]) // 2

        p.drawImage(
            int(ox), int(oy), img.scaled(
                target[0], target[1],
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

        scale_x = target[0] / float(img_w)
        scale_y = target[1] / float(img_h)

        # ---- Path overlay (drawn before pose so triangle stays on top)
        if self._path_mm and len(self._path_mm) >= 2:
            pen = QPen(QColor(200, 16, 46, 200), 2, Qt.DashLine)
            p.setPen(pen)
            pts: List[QPoint] = []
            for wx_mm, wy_mm in self._path_mm:
                pts.append(self._world_mm_to_widget_qpoint(
                    wx_mm, wy_mm, ox, oy, img_w, img_h, scale_x, scale_y,
                ))
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])

        # ---- Goal flag overlay
        if self._goal_mm is not None:
            self._draw_goal_pin(
                p, self._goal_mm,
                ox, oy, img_w, img_h, scale_x, scale_y,
                filled=self._snapped_goal_mm is None,
            )
        if self._snapped_goal_mm is not None:
            self._draw_goal_pin(
                p, self._snapped_goal_mm,
                ox, oy, img_w, img_h, scale_x, scale_y,
                filled=True,
            )

        # ---- Pose triangle
        cx_world = img_w / 2.0
        cy_world = img_h / 2.0
        px = ox + (cx_world + (self._pose_x_mm / self._scale_mm_per_px)) * scale_x
        py = oy + (cy_world - (self._pose_y_mm / self._scale_mm_per_px)) * scale_y

        # Pose triangle scales with the rendered map size so it stays
        # readable across the full range of containers we host this
        # widget in: ~6 px on a tiny 200x200 cell, ~22 px on the big
        # Map screen card. Hard-coded 10 px (the previous value)
        # rendered the Map screen pose as a barely-visible dot AND
        # the Perception screen lidar pane as 'just a dot in the
        # middle of empty grey' on a fresh boot before the SLAM grid
        # had built up walls.
        size = max(6, min(target[0], target[1]) // 16)
        a = radians(self._pose_theta_deg)
        # Front of triangle = +y in robot frame -> -y on screen.
        tip = QPoint(int(px + size * sin(a)), int(py - size * cos(a)))
        left = QPoint(
            int(px + size * 0.5 * sin(a + 2.6)),
            int(py - size * 0.5 * cos(a + 2.6)),
        )
        right = QPoint(
            int(px + size * 0.5 * sin(a - 2.6)),
            int(py - size * 0.5 * cos(a - 2.6)),
        )
        # White outline so the triangle reads against the map's red
        # wall pixels and the dark grey 'unknown' fill on small panes.
        # Without this the triangle disappears on the Perception card
        # any time it sits over a wall.
        p.setBrush(QBrush(QColor("#c8102e")))
        p.setPen(QPen(QColor("white"), max(1, size // 5)))
        p.drawPolygon(QPolygon([tip, left, right]))

    def _world_mm_to_widget_qpoint(
        self, x_mm: float, y_mm: float,
        ox: int, oy: int, img_w: int, img_h: int,
        scale_x: float, scale_y: float,
    ) -> QPoint:
        cx_world = img_w / 2.0
        cy_world = img_h / 2.0
        wx = ox + (cx_world + x_mm / self._scale_mm_per_px) * scale_x
        wy = oy + (cy_world - y_mm / self._scale_mm_per_px) * scale_y
        return QPoint(int(wx), int(wy))

    def _draw_goal_pin(
        self, p: QPainter, mm: Tuple[float, float],
        ox: int, oy: int, img_w: int, img_h: int,
        scale_x: float, scale_y: float,
        *, filled: bool,
    ) -> None:
        pt = self._world_mm_to_widget_qpoint(
            mm[0], mm[1], ox, oy, img_w, img_h, scale_x, scale_y,
        )
        target_min = min(scale_x * img_w, scale_y * img_h)
        radius = max(5, int(target_min // 60))
        if filled:
            p.setBrush(QBrush(QColor("#c8102e")))
            p.setPen(QPen(QColor("white"), max(1, radius // 3)))
        else:
            # Hollow ring for the original click when we snapped
            # the goal to a free cell.
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#c8102e"), max(2, radius // 3)))
        p.drawEllipse(pt, radius, radius)
        # Little flag pole + banner so a glance distinguishes it
        # from the pose triangle on a busy map.
        if filled:
            pole_len = radius * 3
            pole_top = QPoint(pt.x(), pt.y() - pole_len)
            p.setPen(QPen(QColor("#c8102e"), max(1, radius // 4)))
            p.drawLine(pt, pole_top)
            banner = QPolygon([
                pole_top,
                QPoint(pole_top.x() + radius * 2, pole_top.y() + radius),
                QPoint(pole_top.x(), pole_top.y() + radius * 2),
            ])
            p.setBrush(QBrush(QColor("#c8102e")))
            p.setPen(QPen(QColor("white"), 1))
            p.drawPolygon(banner)

    @staticmethod
    def _fit(box_w: int, box_h: int, src_w: int, src_h: int) -> tuple:
        if src_w <= 0 or src_h <= 0:
            return box_w, box_h
        ratio = src_w / float(src_h)
        if box_w / float(box_h) > ratio:
            h = box_h
            w = int(h * ratio)
        else:
            w = box_w
            h = int(w / ratio)
        return max(1, w), max(1, h)
