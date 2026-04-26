"""Custom QWidget that renders a BreezySLAM occupancy grid.

The grid uses BreezySLAM's convention:
    0   = occupied (wall)
    255 = free space
    ~127 = unknown (initial fill)

We render that to a QImage at the widget's resolution and overlay
Nina's pose as a Sirena-red triangle.

Why a custom widget rather than `QGraphicsScene`? At 800x800 pixels
and 5 Hz the simplest approach (one QImage, drawn in `paintEvent`)
is the one that gives us the cleanest path on Jetson Nano. Heavier
visualisations can swap in later without changing the screen wiring.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPolygon,
)
from PyQt5.QtWidgets import QWidget


class OccupancyGridView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self.setStyleSheet("background-color: #f5f5f7; border-radius: 12px;")
        self._image: Optional[QImage] = None
        self._image_width = 0
        self._image_height = 0
        self._scale_mm_per_px = 1.0
        self._pose_x_mm = 0.0
        self._pose_y_mm = 0.0
        self._pose_theta_deg = 0.0
        self._has_data = False
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

        # Overlay pose triangle.
        scale_x = target[0] / float(img_w)
        scale_y = target[1] / float(img_h)
        cx_world = img_w / 2.0
        cy_world = img_h / 2.0
        px = ox + (cx_world + (self._pose_x_mm / self._scale_mm_per_px)) * scale_x
        py = oy + (cy_world - (self._pose_y_mm / self._scale_mm_per_px)) * scale_y

        size = 10
        from math import cos, sin, radians
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
        p.setBrush(QBrush(QColor("#c8102e")))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygon([tip, left, right]))

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
