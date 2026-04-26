"""Touch-friendly virtual D-pad used on the Drive screen."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QGridLayout,
    QPushButton,
    QWidget,
)


class DPad(QWidget):
    """Five-button D-pad: forward, back, left, right, stop."""

    direction_pressed = pyqtSignal(str)   # 'forward' | 'back' | 'left' | 'right'
    direction_released = pyqtSignal(str)
    stop_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        self._fwd = self._make_btn("\u25B2  Forward", "forward")
        self._back = self._make_btn("\u25BC  Back", "back")
        self._left = self._make_btn("\u25C0  Left", "left")
        self._right = self._make_btn("\u25B6  Right", "right")

        self._stop = QPushButton("STOP")
        self._stop.setObjectName("dpadStop")
        self._stop.setCursor(Qt.PointingHandCursor)
        self._stop.clicked.connect(self.stop_clicked.emit)

        grid.addWidget(self._fwd,   0, 1)
        grid.addWidget(self._left,  1, 0)
        grid.addWidget(self._stop,  1, 1)
        grid.addWidget(self._right, 1, 2)
        grid.addWidget(self._back,  2, 1)

    def _make_btn(self, label: str, direction: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("dpadButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.pressed.connect(lambda d=direction: self.direction_pressed.emit(d))
        btn.released.connect(lambda d=direction: self.direction_released.emit(d))
        return btn

    def set_enabled(self, enabled: bool) -> None:
        for btn in (self._fwd, self._back, self._left, self._right, self._stop):
            btn.setEnabled(enabled)
