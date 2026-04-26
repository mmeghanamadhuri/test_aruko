"""Top-level Sirena Control Center window.

Layout:

  +-------------------------------------------------------+
  |              red header (centered title)              |
  +---------+---------------------------------------------+
  |         |                                             |
  | sidebar |              screen stack                   |
  |         |                                             |
  +---------+---------------------------------------------+
  |                  charcoal status bar                  |
  +-------------------------------------------------------+

Each screen is created lazily on first navigation. The
`MainWindow` owns the single shared `NinaService` and routes
nav clicks to the right widget.
"""

from __future__ import annotations

import getpass
import socket
from typing import Dict, Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.header_bar import HeaderBar
from sirena_ui.widgets.sidebar import NAV_ITEMS, Sidebar
from sirena_ui.widgets.status_bar import StatusBar
from sirena_ui.workers.nina_service import NinaService

APP_VERSION = "0.4"


class MainWindow(QMainWindow):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Sirena Control Center")
        self.resize(1280, 800)
        self.setMinimumSize(1100, 720)

        self._screens: Dict[str, QWidget] = {}
        self._titles: Dict[str, str] = {
            "home": "Nina \u00b7 Home",
            "drive": "Nina \u00b7 Drive",
            "vision": "Nina \u00b7 Vision",
            "map": "Nina \u00b7 Map (SLAM)",
            "actions": "Nina \u00b7 Actions",
            "settings": "Nina \u00b7 Settings",
            "health": "Nina \u00b7 Health Check",
        }

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = HeaderBar()
        outer.addWidget(self._header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, stretch=1)

        host = self._host_label()
        self._sidebar = Sidebar(version_label=f"v{APP_VERSION}", host_label=host)
        self._sidebar.nav_changed.connect(self.navigate)
        body.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        body.addWidget(self._stack, stretch=1)

        self._status_bar = StatusBar()
        outer.addWidget(self._status_bar)

        # Initial state
        self.navigate("home")
        self._sidebar.select("home")

        # Try to bring up the bus shortly after the window appears so the
        # status bar shows accurate dots without blocking the UI.
        QTimer.singleShot(150, self._initialize_bus)

    # ---------- navigation ----------

    def navigate(self, key: str) -> None:
        widget = self._screens.get(key)
        if widget is None:
            widget = self._build_screen(key)
            self._screens[key] = widget
            self._stack.addWidget(widget)
        self._stack.setCurrentWidget(widget)
        self._header.set_title(self._titles.get(key, "Nina"))
        on_enter = getattr(widget, "on_enter", None)
        if callable(on_enter):
            try:
                on_enter()
            except Exception:
                pass

    def _build_screen(self, key: str) -> QWidget:
        if key == "home":
            from sirena_ui.screens.home_screen import HomeScreen
            screen = HomeScreen(self._service)
            screen.navigate_requested.connect(self._on_nav_request)
            return screen
        if key == "drive":
            from sirena_ui.screens.drive_screen import DriveScreen
            return DriveScreen(self._service)
        if key == "vision":
            from sirena_ui.screens.vision_screen import VisionScreen
            return VisionScreen(self._service)
        if key == "map":
            from sirena_ui.screens.map_screen import MapScreen
            return MapScreen(self._service)
        if key == "actions":
            from sirena_ui.screens.actions_screen import ActionsScreen
            screen = ActionsScreen(self._service)
            screen.bus_status_changed.connect(self._status_bar.set_right_text)
            return screen
        if key == "settings":
            from sirena_ui.screens.settings_screen import SettingsScreen
            return SettingsScreen(self._service)
        if key == "health":
            from sirena_ui.screens.health_screen import HealthScreen
            return HealthScreen(self._service)
        raise ValueError(f"Unknown screen key: {key}")

    def _on_nav_request(self, key: str) -> None:
        self.navigate(key)
        self._sidebar.select(key)

    # ---------- bus / footer ----------

    def _initialize_bus(self) -> None:
        try:
            health = self._service.ensure_bus()
        except Exception:
            self._status_bar.set_dot("bus", ok=False)
            self._status_bar.set_right_text("Bus offline \u2014 check serial cable")
            return
        self._status_bar.set_dot("bus", ok=True)
        self._status_bar.set_dot("wifi", ok=True)
        self._status_bar.set_dot("battery", ok=True)
        self._status_bar.set_dot("voice", ok=False, warn=True)  # ESP voice not yet wired
        detected = health.get("detected", 0)
        expected = health.get("expected", 0)
        self._status_bar.set_right_text(
            f"Motors {detected}/{expected} \u00b7 Bus ready"
        )

    @staticmethod
    def _host_label() -> str:
        try:
            return f"{getpass.getuser()}@{socket.gethostname()}"
        except Exception:
            return ""

    def closeEvent(self, event) -> None:
        try:
            self._service.shutdown()
        except Exception:
            pass
        super().closeEvent(event)


# Keep `NAV_ITEMS` re-exported so screens can reuse the same labels.
__all__ = ["MainWindow", "NAV_ITEMS"]
