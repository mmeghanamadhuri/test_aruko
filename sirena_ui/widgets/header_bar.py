"""Top red Sirena header. Shows the current screen title in the centre."""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
)


class HeaderBar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(56)

        h = QHBoxLayout(self)
        h.setContentsMargins(20, 6, 16, 6)
        h.setSpacing(12)

        # Brand / logo area lives in the sidebar; keep header light and
        # leave room for the centered title.
        self._left_spacer = QLabel("")
        self._left_spacer.setMinimumWidth(60)
        h.addWidget(self._left_spacer)

        h.addStretch(1)
        self._title = QLabel("")
        self._title.setObjectName("headerTitle")
        self._title.setAlignment(Qt.AlignCenter)
        h.addWidget(self._title)
        h.addStretch(1)

        # Right "system tray": time, simple WiFi/battery glyphs.
        self._wifi = QLabel("\u2706")          # wifi/phone glyph
        self._wifi.setObjectName("headerTray")
        self._wifi.setStyleSheet(
            "color: white; font-size: 16px; padding: 0 6px;"
            " background-color: transparent;"
        )
        h.addWidget(self._wifi)

        self._battery = QLabel("\u25AE")       # vertical bar (battery placeholder)
        self._battery.setStyleSheet(
            "color: white; font-size: 16px; padding: 0 6px;"
            " background-color: transparent;"
        )
        h.addWidget(self._battery)

        self._clock = QLabel("00:00")
        self._clock.setStyleSheet(
            "color: white; font-size: 14px; padding: 0 8px;"
            " background-color: transparent;"
        )
        h.addWidget(self._clock)

        self._menu = QPushButton("\u22EE")
        self._menu.setObjectName("headerTray")
        self._menu.setCursor(Qt.PointingHandCursor)
        self._menu.setFixedSize(28, 28)
        h.addWidget(self._menu)

        # Refresh the clock every 30 s. Cheap; QTimer parented to self
        # so it stops when the header is destroyed.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_clock)
        self._timer.start(30_000)
        self._refresh_clock()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def _refresh_clock(self) -> None:
        from datetime import datetime
        self._clock.setText(datetime.now().strftime("%H:%M"))
