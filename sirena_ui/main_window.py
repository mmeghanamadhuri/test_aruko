"""Top-level Sirena Control Center window."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.screens.launcher_screen import LauncherScreen
from sirena_ui.screens.nina_screen import NinaScreen
from sirena_ui.styles import asset_path
from sirena_ui.workers.nina_service import NinaService

APP_VERSION = "0.1"


class MainWindow(QMainWindow):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Sirena Control Center")
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header_title = QLabel()
        self._header_title.setObjectName("headerTitle")
        self._header_back = QPushButton("\u2190 Back")
        self._header_back.setObjectName("headerBack")
        self._header_back.setCursor(Qt.PointingHandCursor)
        self._header_back.clicked.connect(self._show_launcher)
        outer.addWidget(self._build_header())

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, stretch=1)

        outer.addWidget(self._build_footer())

        self._launcher = LauncherScreen()
        self._launcher.robot_selected.connect(self._on_robot_selected)
        self._stack.addWidget(self._launcher)

        self._nina_screen = NinaScreen(self._service)
        self._stack.addWidget(self._nina_screen)

        self._show_launcher()

    # ---- header / footer ----

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("headerBar")
        bar.setFixedHeight(72)
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(14)

        logo = QLabel()
        pix = QPixmap(asset_path("sirena_logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaledToHeight(48, Qt.SmoothTransformation))
        h.addWidget(logo)

        h.addStretch(1)
        self._header_title.setAlignment(Qt.AlignCenter)
        h.addWidget(self._header_title)
        h.addStretch(1)

        h.addWidget(self._header_back)
        return bar

    def _build_footer(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("footerBar")
        bar.setFixedHeight(28)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet("color: #6e6e73; font-size: 12px;")
        h.addWidget(version)
        h.addStretch(1)
        self._bus_status = QLabel("\u25CF Bus: not initialized")
        self._bus_status.setStyleSheet("color: #6e6e73; font-size: 12px;")
        h.addWidget(self._bus_status)
        return bar

    # ---- navigation ----

    def _show_launcher(self) -> None:
        self._stack.setCurrentWidget(self._launcher)
        self._header_title.setText("Sirena Control Center")
        self._header_back.setVisible(False)
        self._bus_status.setText("\u25CF Bus: idle")
        self._bus_status.setStyleSheet("color: #6e6e73; font-size: 12px;")

    def _on_robot_selected(self, robot_id: str) -> None:
        if robot_id == "nina":
            self._stack.setCurrentWidget(self._nina_screen)
            self._header_title.setText("Nina")
            self._header_back.setVisible(True)
            self._nina_screen.on_enter()
            self._bus_status.setText("\u25CF Bus: connected")
            self._bus_status.setStyleSheet("color: #2ecc71; font-size: 12px;")

    def closeEvent(self, event) -> None:
        try:
            self._service.shutdown()
        except Exception:
            pass
        super().closeEvent(event)
