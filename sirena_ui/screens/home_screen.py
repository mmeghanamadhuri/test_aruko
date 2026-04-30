"""Home / Dashboard screen.

Shows a hero card with Nina's photo, a quick-action tile grid that
deep-links into the major sub-screens, and a small "live status"
card. Designed to be the first thing a user sees when the app
launches on the 10.1" Jetson display.
"""

from __future__ import annotations

from typing import List, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.styles import asset_path
from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel, Pill, SectionLabel
from sirena_ui.workers.nina_service import NinaService


# (key, label, glyph, blurb)
# Keys that contain a ":" are deep links of the form "screen:subtab".
# They are routed by `MainWindow.navigate` to the right screen and
# then to the right inner tab via that screen's `set_subtab(name)`.
QUICK_ACTIONS: List[Tuple[str, str, str, str]] = [
    ("actions:playback", "Play action", "\u25B6", "Run a saved motion"),
    ("actions:record", "Record", "\u25CF", "Capture a new pose"),
    ("actions:audio", "Audio", "\u266B", "Voice clips"),
    ("drive", "Drive", "\u2B95", "Manual control"),
    ("vision", "Vision", "\u25CE", "Camera & faces"),
    ("map", "Map", "\u25A6", "SLAM & dock"),
    ("health", "Health", "\u2665", "System checks"),
    ("settings", "Settings", "\u2699", "Configure"),
]


class _QuickTile(QPushButton):
    def __init__(self, glyph: str, label: str, blurb: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 80 (was 110) so the 2-row tile grid fits beside the hero on
        # a 600-tall panel after chrome (~70) and outer margins (~20).
        # Still well above the 44 px touch-target minimum.
        self.setMinimumHeight(80)
        self.setStyleSheet(
            """
            QPushButton#card {
                background-color: white;
                border: 1px solid #e3e3e6;
                border-radius: 12px;
                text-align: left;
            }
            QPushButton#card:hover, QPushButton#card:pressed {
                border-color: #c8102e;
                background-color: #fbe7eb;
            }
            """
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        glyph_label = QLabel(glyph)
        glyph_label.setStyleSheet(
            "color: #c8102e; font-size: 18px; background-color: transparent;"
        )
        v.addWidget(glyph_label)
        title = QLabel(label)
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 14px; font-weight: 700;"
            " background-color: transparent;"
        )
        v.addWidget(title)
        sub = QLabel(blurb)
        sub.setStyleSheet(
            "color: #6e6e73; font-size: 11px; background-color: transparent;"
        )
        v.addWidget(sub)


class HomeScreen(QWidget):
    navigate_requested = pyqtSignal(str)

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(Breadcrumb("Nina", "Home"))

        outer.addWidget(self._build_hero(), stretch=0)

        section = SectionLabel("Quick actions")
        outer.addWidget(section)
        outer.addLayout(self._build_tiles(), stretch=1)

        outer.addWidget(self._build_status_strip(), stretch=0)

    # ---------- hero ----------

    def _build_hero(self) -> Card:
        # Was padding=24 spacing=16 with a 180-tall image. On a 600 px
        # panel that hero alone ate ~220 px of the ~510 px content area
        # and pushed the tile grid + status strip off-screen. Trimmed
        # everything by ~40%.
        card = Card(padding=12, spacing=8, hero=True)
        h = QHBoxLayout()
        h.setSpacing(12)
        card.add_layout(h)

        # Nina image, scaled to a comfortable hero size
        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setStyleSheet("background-color: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            image.setPixmap(pix.scaledToHeight(110, Qt.SmoothTransformation))
        image.setFixedWidth(140)
        h.addWidget(image)

        text = QVBoxLayout()
        text.setSpacing(4)
        h.addLayout(text, stretch=1)

        hello = QLabel("Hi, I'm Nina.")
        hello.setStyleSheet(
            "color: #1c1c1e; font-size: 20px; font-weight: 700;"
            " background-color: transparent;"
        )
        text.addWidget(hello)

        sub = QLabel("Sirena Robotics \u00b7 ready when you are.")
        sub.setStyleSheet(
            "color: #6e6e73; font-size: 12px; background-color: transparent;"
        )
        text.addWidget(sub)

        text.addSpacing(4)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        chip_row.setAlignment(Qt.AlignLeft)
        text.addLayout(chip_row)
        for label, kind in [
            ("Idle", Pill.KIND_NEUTRAL),
            ("Torque ON", Pill.KIND_OK),
            ("Voice ready", Pill.KIND_NEUTRAL),
        ]:
            chip_row.addWidget(Pill(label, kind))
        text.addStretch(1)

        # Right-side primary CTA
        cta_col = QVBoxLayout()
        cta_col.setSpacing(8)
        cta_col.setAlignment(Qt.AlignCenter)
        h.addLayout(cta_col)

        play_btn = QPushButton("Play actions")
        play_btn.setObjectName("primaryButton")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setMinimumWidth(140)
        play_btn.clicked.connect(
            lambda: self.navigate_requested.emit("actions:playback")
        )
        cta_col.addWidget(play_btn)

        record_btn = QPushButton("Record new")
        record_btn.setObjectName("secondaryButton")
        record_btn.setCursor(Qt.PointingHandCursor)
        record_btn.setMinimumWidth(140)
        record_btn.clicked.connect(
            lambda: self.navigate_requested.emit("actions:record")
        )
        cta_col.addWidget(record_btn)

        return card

    # ---------- tiles ----------

    def _build_tiles(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)
        cols = 4
        for i, (key, label, glyph, blurb) in enumerate(QUICK_ACTIONS):
            tile = _QuickTile(glyph, label, blurb)
            tile.clicked.connect(lambda _checked=False, k=key: self.navigate_requested.emit(k))
            grid.addWidget(tile, i // cols, i % cols)
        return grid

    # ---------- status strip ----------

    def _build_status_strip(self) -> Card:
        card = Card(padding=10, spacing=6)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        card.add_layout(title_row)
        title_row.addWidget(CardTitle("System overview"))
        title_row.addStretch(1)
        title_row.addWidget(MutedLabel("Tap Health for details"))

        row = QHBoxLayout()
        row.setSpacing(8)
        card.add_layout(row)
        items = [
            ("Bus", "Connecting...", Pill.KIND_NEUTRAL),
            ("Camera", "Not connected", Pill.KIND_NEUTRAL),
            ("Lidar", "Not connected", Pill.KIND_NEUTRAL),
            ("Battery", "n/a", Pill.KIND_NEUTRAL),
            ("Wi-Fi", "Online", Pill.KIND_OK),
        ]
        for label, value, kind in items:
            box = Card(padding=8, spacing=2, subtle=True)
            box.add(SectionLabel(label))
            box.add(Pill(value, kind))
            row.addWidget(box, stretch=1)

        return card
