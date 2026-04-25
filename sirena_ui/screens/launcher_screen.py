"""'Choose a robot' launcher screen."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.robot_tile import RobotTile


class LauncherScreen(QWidget):
    robot_selected = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 24, 40, 24)
        outer.setSpacing(20)

        title = QLabel("Choose a robot")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(title)

        tile_row = QHBoxLayout()
        tile_row.setSpacing(20)
        tile_row.setAlignment(Qt.AlignLeft)

        nina_tile = RobotTile(
            robot_id="nina",
            name="Nina",
            image_filename="nina.png",
            enabled=True,
        )
        nina_tile.open_clicked.connect(self.robot_selected.emit)
        tile_row.addWidget(nina_tile)

        carbot_tile = RobotTile(
            robot_id="carbot",
            name="Carbot",
            enabled=False,
            placeholder_text="coming soon",
        )
        tile_row.addWidget(carbot_tile)

        add_tile = RobotTile(
            robot_id="add",
            name="+ Add robot",
            enabled=False,
            placeholder_text="future",
        )
        tile_row.addWidget(add_tile)

        tile_row.addStretch(1)
        outer.addLayout(tile_row)
        outer.addStretch(1)
