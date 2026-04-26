"""Map / SLAM screen: occupancy grid + sensor health + auto-dock."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import (
    Breadcrumb,
    Card,
    CardTitle,
    MutedLabel,
    Pill,
    SectionLabel,
)
from sirena_ui.workers.nina_service import NinaService


class _MapCanvas(QWidget):
    """Simple painted occupancy-grid placeholder.

    Renders a faint grid + a Sirena-red triangle for Nina's pose. When
    real lidar data lands, this widget can be replaced with a
    `QGraphicsScene` driven view; the public API stays the same.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(420)
        self.setStyleSheet("background-color: #f5f5f7; border-radius: 12px;")
        self._has_map = False

    def set_has_map(self, has: bool) -> None:
        self._has_map = has
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(8, 8, -8, -8)

        # Light grid background
        p.fillRect(rect, QColor("#f0f0f3"))
        p.setPen(QPen(QColor("#e3e3e6"), 1))
        step = 28
        for x in range(rect.left(), rect.right(), step):
            p.drawLine(x, rect.top(), x, rect.bottom())
        for y in range(rect.top(), rect.bottom(), step):
            p.drawLine(rect.left(), y, rect.right(), y)

        if not self._has_map:
            p.setPen(QColor("#8e8e93"))
            p.drawText(
                rect, Qt.AlignCenter,
                "Lidar / IR / Ultrasonic sensors not connected.\n"
                "Start mapping once the sensors are wired up.",
            )
            return

        # Demo walls
        p.setPen(QPen(QColor("#2c2c2e"), 4))
        cx, cy = rect.center().x(), rect.center().y()
        p.drawRect(int(cx - 220), int(cy - 140), 440, 280)
        p.drawLine(int(cx - 60), int(cy - 140), int(cx - 60), int(cy + 30))
        p.drawLine(int(cx + 80), int(cy - 30), int(cx + 80), int(cy + 140))

        # Lidar fan
        p.setBrush(QBrush(QColor(200, 16, 46, 30)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(cx - 110), int(cy - 110), 220, 220)

        # Nina pose triangle
        p.setBrush(QBrush(QColor("#c8102e")))
        p.setPen(Qt.NoPen)
        from PyQt5.QtGui import QPolygon
        from PyQt5.QtCore import QPoint
        tri = QPolygon([
            QPoint(cx, cy - 12),
            QPoint(cx - 9, cy + 8),
            QPoint(cx + 9, cy + 8),
        ])
        p.drawPolygon(tri)


class MapScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Breadcrumb("Nina", "Map (SLAM)"))
        top.addStretch(1)
        self._sensor_pill = Pill("Sensors not connected", Pill.KIND_NEUTRAL)
        top.addWidget(self._sensor_pill)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_map_card(), stretch=62)
        body.addWidget(self._build_side_card(), stretch=38)

    # ---------- map ----------

    def _build_map_card(self) -> Card:
        card = Card(padding=16, spacing=10)
        header = QHBoxLayout()
        card.add_layout(header)
        header.addWidget(CardTitle("Occupancy map"))
        header.addStretch(1)
        header.addWidget(Pill("Preview \u2014 demo data", Pill.KIND_NEUTRAL))

        self._canvas = _MapCanvas()
        card.add(self._canvas, stretch=1)

        legend = QHBoxLayout()
        legend.setSpacing(12)
        card.add_layout(legend)
        for color, text in [
            ("#c8102e", "Nina"),
            ("#2c2c2e", "Wall"),
            ("#2ecc71", "Path"),
            ("#3498db", "Dock"),
        ]:
            chip = QFrame()
            chip.setStyleSheet("background-color: transparent;")
            row = QHBoxLayout(chip)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            dot = QLabel("\u25CF")
            dot.setStyleSheet(
                f"color: {color}; font-size: 12px; background-color: transparent;"
            )
            row.addWidget(dot)
            label = QLabel(text)
            label.setStyleSheet(
                "color: #6e6e73; font-size: 12px; background-color: transparent;"
            )
            row.addWidget(label)
            legend.addWidget(chip)
        legend.addStretch(1)
        return card

    # ---------- side ----------

    def _build_side_card(self) -> Card:
        card = Card(padding=20, spacing=10)

        card.add(SectionLabel("Mapping"))
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        card.add_layout(row1)
        start = QPushButton("Start mapping")
        start.setObjectName("primaryButton")
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self._on_start_mapping)
        pause = QPushButton("Pause")
        pause.setObjectName("secondaryButton")
        pause.setCursor(Qt.PointingHandCursor)
        row1.addWidget(start)
        row1.addWidget(pause)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        card.add_layout(row2)
        for label in ("Save map", "Load map", "Clear"):
            btn = QPushButton(label)
            btn.setObjectName("secondaryButton")
            btn.setCursor(Qt.PointingHandCursor)
            row2.addWidget(btn)

        card.add(SectionLabel("Sensor health"))
        chips = QHBoxLayout()
        chips.setSpacing(6)
        card.add_layout(chips)
        for label in ("Lidar 360\u00b0", "IR 4/4", "Ultra 2/2"):
            chips.addWidget(Pill(f"{label} \u00d7", Pill.KIND_NEUTRAL))
        chips.addStretch(1)

        card.add(SectionLabel("Pose"))
        pose = QLabel("x: \u2014\ny: \u2014\n\u03b8: \u2014")
        pose.setStyleSheet(
            "background-color: #f5f5f7; border-radius: 8px; padding: 10px;"
            " font-family: Menlo, monospace; color: #1c1c1e;"
        )
        card.add(pose)

        card.add(SectionLabel("Auto-dock"))
        dock = QPushButton("Return to dock")
        dock.setObjectName("primaryButton")
        dock.setCursor(Qt.PointingHandCursor)
        dock.clicked.connect(self._on_return_to_dock)
        card.add(dock)
        card.add(MutedLabel("Battery > 20% required \u00b7 sensors must be online."))

        card.add_stretch()
        return card

    # ---------- handlers ----------

    def _on_start_mapping(self) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Sensors not connected",
            "Connect the lidar / IR / ultrasonic sensors to the Jetson before"
            " starting a mapping run.",
        )

    def _on_return_to_dock(self) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Auto-dock unavailable",
            "Auto-dock requires an active map and connected sensors. Build a"
            " map first via 'Start mapping'.",
        )
