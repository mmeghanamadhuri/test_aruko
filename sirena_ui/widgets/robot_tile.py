"""Tile shown on the launcher 'Choose a robot' grid."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from sirena_ui.styles import asset_path


class RobotTile(QFrame):
    open_clicked = pyqtSignal(str)

    def __init__(
        self,
        robot_id: str,
        name: str,
        image_filename: str | None = None,
        enabled: bool = True,
        placeholder_text: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.robot_id = robot_id
        self.setObjectName("card" if enabled else "cardDisabled")
        self.setFixedWidth(280)
        self.setMinimumHeight(380)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(name)
        title.setObjectName("cardTitle")
        title.setProperty("class", "cardTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setMinimumHeight(240)
        if image_filename:
            pix = QPixmap(asset_path(image_filename))
            if not pix.isNull():
                image_label.setPixmap(
                    pix.scaledToHeight(260, Qt.SmoothTransformation)
                )
        elif placeholder_text:
            image_label.setText(placeholder_text)
            image_label.setStyleSheet("color: #8a8a8f; font-size: 16px;")
        layout.addWidget(image_label, stretch=1)

        if enabled:
            open_btn = QPushButton("OPEN")
            open_btn.setObjectName("primary")
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.clicked.connect(lambda: self.open_clicked.emit(self.robot_id))
            layout.addWidget(open_btn)
        else:
            layout.addStretch(1)
