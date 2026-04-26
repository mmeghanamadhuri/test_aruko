"""Render every Nina app screen offscreen and save PNGs for the docs.

Run with the project root as the working directory and `PYTHONPATH=.`:

    PYTHONPATH=. /tmp/sirena_venv/bin/python sirena_ui/docs/_capture_screens.py

Drops one PNG per screen into ``sirena_ui/docs/screens/`` using the
real PyQt5 widgets (no design mockups). Hardware that isn't present
on the host shows up as "sim" / "Not connected" pills, which is the
honest state when the docs are read on a non-Jetson machine.
"""

from __future__ import annotations

import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, Qt, QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

# Keep dialog popups from blocking the offscreen run.
QMessageBox.critical = staticmethod(lambda *a, **kw: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **kw: QMessageBox.Ok)
QMessageBox.information = staticmethod(lambda *a, **kw: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.Yes)


from sirena_ui.main_window import MainWindow
from sirena_ui.styles import STYLESHEET
from sirena_ui.workers.nina_service import NinaService


SCREENS = [
    ("home", "screen-home.png"),
    ("drive", "screen-drive.png"),
    ("vision", "screen-vision.png"),
    ("map", "screen-map.png"),
    ("actions", "screen-actions.png"),
    ("settings", "screen-settings.png"),
    ("health", "screen-health.png"),
]


def _pump(ms: int = 250) -> None:
    """Spin the Qt event loop for ``ms`` milliseconds."""
    end_at = QCoreApplication.instance()
    end_at.processEvents()
    deadline = QTimer()
    deadline.setSingleShot(True)
    fired = []
    deadline.timeout.connect(lambda: fired.append(True))
    deadline.start(ms)
    while not fired:
        QCoreApplication.instance().processEvents()


def main() -> int:
    out_dir = pathlib.Path(__file__).resolve().parent / "screens"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    service = NinaService()
    win = MainWindow(service)
    win.resize(1280, 800)
    win.show()
    _pump(400)  # let the bus initial-status timer fire

    for key, fname in SCREENS:
        win.navigate(key)
        # Settings sub-sidebar / Health donut / Map grid all need a
        # tick or two to lay out and paint.
        _pump(450)
        target = out_dir / fname
        ok = win.grab().save(str(target), "PNG")
        if not ok:
            print(f"FAILED to save {target}", file=sys.stderr)
            return 1
        print(f"saved {target}")

    try:
        service.shutdown()
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
