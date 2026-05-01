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
import os
import socket
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QGuiApplication
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


def _env_truthy(name: str) -> bool:
    """Permissive bool parse so the operator can use 1/true/yes/on."""
    raw = os.environ.get(name, "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


class MainWindow(QMainWindow):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Sirena Control Center")

        # NINA_UI_FULLSCREEN=1 starts the GUI in frameless kiosk mode at
        # the panel's native resolution - what we want on the bot. Devs
        # leave the env var unset to keep their normal resizable window.
        self._kiosk = _env_truthy("NINA_UI_FULLSCREEN")
        if self._kiosk:
            self.setWindowFlag(Qt.FramelessWindowHint, True)

        # Default geometry targets a 1024 x 600 panel (Nina's stock 10.1"
        # touchscreen). The minimum is set to that exact size so the GUI
        # doesn't crop off below it on the bot. In kiosk mode we size
        # the window to the primary screen IMMEDIATELY (rather than
        # the dev-friendly 1280x800 default) so the WM never sees a
        # window bigger than the panel - on a 1024x600 panel a 1280x800
        # initial size makes the WM smart-place the oversize window at
        # a non-zero offset and our subsequent showEvent setGeometry
        # then races against that placement, leaving the kiosk shifted
        # right of the panel with the desktop visible underneath.
        if self._kiosk:
            primary = QGuiApplication.primaryScreen()
            if primary is not None:
                pg = primary.geometry()
                self.resize(pg.width(), pg.height())
            else:
                self.resize(1024, 600)
            self.setMinimumSize(640, 360)
        else:
            self.resize(1280, 800)
            self.setMinimumSize(1024, 600)

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

    def showEvent(self, event) -> None:
        """Honour kiosk mode after the window's first show.

        Default kiosk mode is a frameless window explicitly sized to
        the primary QScreen geometry - NOT showFullScreen() and NOT
        showMaximized():

        - showFullScreen() claims X11 `_NET_WM_STATE_FULLSCREEN`
          which most WMs stack above every other window class
          including ABOVE-state windows (i.e. the OSK).
        - showMaximized() + FramelessWindowHint is unreliable: many
          WMs don't know how to maximize a frameless window and just
          leave it at the prior resize, producing a stretched/cropped
          UI on a 1024x600 panel.

        Manual setGeometry(primary_screen) sidesteps both. The work
        gets DEFERRED into a QTimer.singleShot(0, ...) call so the
        WM has finished its initial placement of our frameless
        window before we override - without that deferral the WM's
        smart-place pass races our setGeometry and the kiosk often
        ends up shifted right of the panel with the desktop visible
        on the left. We also re-assert geometry once more 250 ms
        later because some WMs do a second placement pass after the
        first override.

        NINA_UI_FULLSCREEN_STRICT=1 opts back into showFullScreen()
        for deployments that don't use an on-screen keyboard and
        want the WM to absolutely guarantee no other window stacks
        above the kiosk.
        """
        super().showEvent(event)
        if not self._kiosk:
            return

        if _env_truthy("NINA_UI_FULLSCREEN_STRICT"):
            if not self.isFullScreen():
                self.showFullScreen()
            QTimer.singleShot(50, self._log_kiosk_state)
            return

        # Defer to next event-loop tick so WM initial placement
        # completes first, then re-assert after the WM's secondary
        # placement pass would have run.
        QTimer.singleShot(0, self._apply_kiosk_geometry)
        QTimer.singleShot(250, self._apply_kiosk_geometry)

    def _apply_kiosk_geometry(self) -> None:
        """Force the kiosk window to fill the primary screen exactly.

        Idempotent: calling this multiple times is safe and is in
        fact how we counter WMs that perform a second placement
        pass after our first override (we schedule a 250ms re-run
        from showEvent for exactly that reason).
        """
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            # No screens reported - last-resort fullscreen so the
            # operator at least sees the GUI even if stacking is
            # broken.
            if not self.isFullScreen():
                self.showFullScreen()
            return

        # Drop any prior fullscreen/maximized state so setGeometry
        # actually takes - Qt ignores geometry changes on a window
        # in those states.
        if self.isFullScreen() or self.isMaximized():
            self.showNormal()

        target = primary.geometry()
        if (self.x(), self.y(), self.width(), self.height()) != (
            target.x(), target.y(), target.width(), target.height()
        ):
            self.setGeometry(target)
        self._log_kiosk_state()

    def _log_kiosk_state(self) -> None:
        """One log line per geometry application showing screen +
        window rects, so launch.log makes it obvious if we're
        misaligned. Operator pastes the latest [kiosk] line into a
        bug report and we know exactly what setGeometry produced."""
        try:
            primary = QGuiApplication.primaryScreen()
            if primary is None:
                print("[kiosk] no primary screen reported", flush=True)
                return
            g = primary.geometry()
            mode = "fullscreen" if _env_truthy("NINA_UI_FULLSCREEN_STRICT") else "sized"
            print(
                f"[kiosk] mode={mode} "
                f"screen={primary.name()!r} "
                f"screen_rect=({g.x()},{g.y()},{g.width()}x{g.height()}) "
                f"window_rect=({self.x()},{self.y()},{self.width()}x{self.height()}) "
                f"devicePixelRatio={primary.devicePixelRatio()}",
                flush=True,
            )
        except Exception:
            pass

    def keyPressEvent(self, event) -> None:
        """Kiosk-mode escape hatch: F11 toggles fullscreen, F10 quits.

        Both ignored unless we're in kiosk mode so dev workflows
        (where F10/F11 may be claimed by something else) aren't
        affected.
        """
        if self._kiosk:
            if event.key() == Qt.Key_F11:
                # F11 toggles between expanded kiosk view and a
                # normal-sized window so a dev with a USB keyboard
                # can pop the kiosk down for inspection. We don't
                # try to be clever about restoring the exact
                # manual-sized geometry on toggle-back-up - just
                # use showFullScreen as a "make it big again"
                # shortcut. NINA_UI_FULLSCREEN_STRICT users get
                # their normal fullscreen behaviour.
                if self.isFullScreen():
                    self.showNormal()
                else:
                    self.showFullScreen()
                event.accept()
                return
            if event.key() == Qt.Key_F10:
                self.close()
                event.accept()
                return
        super().keyPressEvent(event)

    # ---------- navigation ----------

    def navigate(self, key: str) -> None:
        # Accept "screen:subtab" deep links (e.g. "actions:record")
        # so tiles on the Home screen and similar shortcuts can land
        # the user directly on the right inner tab.
        screen_key, _, subtab = key.partition(":")
        if not screen_key:
            return

        widget = self._screens.get(screen_key)
        if widget is None:
            widget = self._build_screen(screen_key)
            self._screens[screen_key] = widget
            self._stack.addWidget(widget)

        # Notify the outgoing screen so screens that own background
        # workers (e.g. Vision releases the camera) can stand down.
        previous = self._stack.currentWidget()
        if previous is not None and previous is not widget:
            on_leave = getattr(previous, "on_leave", None)
            if callable(on_leave):
                try:
                    on_leave()
                except Exception:
                    pass

        self._stack.setCurrentWidget(widget)
        self._header.set_title(self._titles.get(screen_key, "Nina"))
        on_enter = getattr(widget, "on_enter", None)
        if callable(on_enter):
            try:
                on_enter()
            except Exception:
                pass

        if subtab:
            set_subtab = getattr(widget, "set_subtab", None)
            if callable(set_subtab):
                try:
                    set_subtab(subtab)
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
        # Sidebar items are addressed by the screen key alone
        # ("actions"), not the deep-link form ("actions:record").
        screen_key = key.partition(":")[0]
        self._sidebar.select(screen_key)

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
