"""Persistent dark-charcoal sidebar with Sirena nav rows."""

from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from sirena_ui.styles import asset_path


# (key, label, icon-glyph) — the glyphs are simple Unicode symbols so we
# don't need an icon-font. They render fine on the Jetson's default
# Noto/DejaVu fonts.
NAV_ITEMS: List[Tuple[str, str, str]] = [
    ("home", "Home", "\u2302"),                # house
    ("drive", "Drive", "\u2B95"),              # right arrow (substitute for car)
    ("vision", "Vision", "\u25CE"),            # bullseye
    ("perception", "Perception", "\u2299"),    # circled dot - sensor fusion view
    ("map", "Map", "\u25A6"),                  # square with grid
    ("actions", "Actions", "\u2630"),          # trigram (lines)
    ("settings", "Settings", "\u2699"),        # gear
    ("health", "Health", "\u2665"),            # heart
]


class Sidebar(QFrame):
    nav_changed = pyqtSignal(str)

    def __init__(self, version_label: str = "v0.4", host_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        # 160 px (was 200) - the nav rows ("  ⌂   Home") fit cleanly at
        # 14 px font with the 17 px left padding, and we get 40 px back
        # for screen content. Critical at 1024 wide.
        self.setFixedWidth(160)
        self._buttons: Dict[str, QPushButton] = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(2)

        v.addWidget(self._build_brand())

        v.addSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, label, glyph in NAV_ITEMS:
            btn = QPushButton(f"  {glyph}   {label}")
            btn.setObjectName("navRow")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=key: self._on_clicked(k))
            self._group.addButton(btn)
            v.addWidget(btn)
            self._buttons[key] = btn

        v.addStretch(1)

        footer_text = version_label
        if host_label:
            footer_text = f"{version_label} \u00b7 {host_label}"
        footer = QLabel(footer_text)
        footer.setObjectName("sidebarFooter")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #9a9a9f; font-size: 11px; padding: 8px;")
        v.addWidget(footer)

    def _build_brand(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet("background-color: transparent;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 12, 2)
        h.setSpacing(8)

        logo = QLabel()
        pix = QPixmap(asset_path("sirena_logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaledToHeight(22, Qt.SmoothTransformation))
        h.addWidget(logo)

        word = QLabel("Sirena")
        word.setStyleSheet(
            "color: white; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        h.addWidget(word)
        h.addStretch(1)
        return bar

    def _on_clicked(self, key: str) -> None:
        self.nav_changed.emit(key)

    def select(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)
