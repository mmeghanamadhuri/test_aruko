"""Charcoal footer that shows live subsystem status dots."""

from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
)

DOT = "\u25CF"


class StatusBar(QFrame):
    """Bottom strip with `Bus / Wi-Fi / Battery / Voice` indicators."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("footerBar")
        # 26 px (was 32) - tight, but the row contains only 12 px text
        # so 7 px of vertical padding on each side is plenty. Frees
        # 6 px of vertical space for the screen content above.
        self.setFixedHeight(26)

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(16)

        self._dots: Dict[str, QLabel] = {}
        for key, label in (
            ("bus", "Bus"),
            ("wifi", "Wi-Fi"),
            ("battery", "Battery"),
            ("voice", "Voice"),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            dot = QLabel(DOT)
            dot.setStyleSheet("color: #6e6e73; font-size: 12px; background-color: transparent;")
            text = QLabel(label)
            text.setStyleSheet("color: #ffffff; font-size: 12px; background-color: transparent;")
            row.addWidget(dot)
            row.addWidget(text)
            wrap = QFrame()
            wrap.setStyleSheet("background-color: transparent;")
            wrap.setLayout(row)
            h.addWidget(wrap)
            self._dots[key] = dot

        h.addStretch(1)

        self._right = QLabel("")
        self._right.setProperty("class", "footerMuted")
        self._right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._right.setStyleSheet(
            "color: #c7c7cc; font-size: 12px; background-color: transparent;"
        )
        h.addWidget(self._right)

    def set_dot(self, key: str, ok: bool, *, warn: bool = False) -> None:
        dot = self._dots.get(key)
        if dot is None:
            return
        if warn:
            color = "#f5a623"
        elif ok:
            color = "#2ecc71"
        else:
            color = "#e74c3c"
        dot.setStyleSheet(f"color: {color}; font-size: 12px; background-color: transparent;")

    def set_right_text(self, text: str) -> None:
        self._right.setText(text)
