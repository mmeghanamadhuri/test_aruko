"""Left-side panel of the Nina screen: Nina image + status row."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from sirena_ui.styles import asset_path


class NinaImagePanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self._image = QLabel()
        self._image.setAlignment(Qt.AlignCenter)
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            self._image.setPixmap(pix.scaledToHeight(520, Qt.SmoothTransformation))
        layout.addWidget(self._image, stretch=1)

        self._status = QLabel("Status: Idle")
        self._status.setProperty("class", "cardMuted")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("color: #6e6e73; font-size: 13px;")
        layout.addWidget(self._status)

    def set_status(self, text: str) -> None:
        self._status.setText(text)
