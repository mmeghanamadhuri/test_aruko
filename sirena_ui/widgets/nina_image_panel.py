"""Left-side panel of the Nina screen: Nina image + status row."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from sirena_ui.styles import asset_path


class NinaImagePanel(QWidget):
    """Photo of Nina in a white card + a small status line below.

    Used as the left rail of the Actions screen.
    """

    def __init__(self, image_height: int = 280, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self._image = QLabel()
        self._image.setAlignment(Qt.AlignCenter)
        self._image.setStyleSheet("background-color: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            self._image.setPixmap(
                pix.scaledToHeight(image_height, Qt.SmoothTransformation)
            )
        layout.addWidget(self._image, stretch=1)

        self._status = QLabel("Status: Idle")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            "color: #6e6e73; font-size: 12px; background-color: transparent;"
        )
        layout.addWidget(self._status)

        outer.addWidget(card, stretch=1)

    def set_status(self, text: str) -> None:
        self._status.setText(text)
