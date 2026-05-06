"""Entry point: `python3 -m sirena_ui` launches the Sirena Control Center."""

from __future__ import annotations

import atexit
import os
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from sirena_ui.main_window import MainWindow
from sirena_ui.styles import STYLESHEET, asset_path
from sirena_ui.workers.companion_delegate_server import start_companion_delegate_server
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.osk import OnScreenKeyboardManager


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Sirena")
    app.setOrganizationName("Sirena Technologies")
    app.setWindowIcon(QIcon(asset_path("sirena_app_icon.png")))
    app.setStyleSheet(STYLESHEET)

    if _env_truthy("NINA_UI_CPROFILE"):
        import cProfile

        _pr = cProfile.Profile()
        _pr.enable()

        def _dump_cprofile() -> None:
            _pr.disable()
            out = os.environ.get(
                "NINA_UI_CPROFILE_OUT",
                "sirena_ui_cprofile.stats",
            )
            _pr.dump_stats(out)
            print(
                f"NINA_UI_CPROFILE: wrote {out} "
                f"(python -m pstats {out} / snakeviz)"
            )

        atexit.register(_dump_cprofile)

    # Touchscreen on-screen keyboard. Pops up `onboard` (or whatever
    # NINA_UI_OSK_BIN is set to) the first time a text-input widget
    # gets focus. Silently disabled on dev hosts that don't have a
    # touchscreen OSK installed - see workers/osk.py for the env-var
    # surface (NINA_UI_OSK=auto|always|off, NINA_UI_OSK_BIN,
    # NINA_UI_OSK_ARGS). Kept on `app` so it isn't garbage-collected
    # when main() returns.
    app._osk = OnScreenKeyboardManager(app)  # type: ignore[attr-defined]

    service = NinaService()
    start_companion_delegate_server(service)
    window = MainWindow(service)
    window.setWindowIcon(QIcon(asset_path("sirena_app_icon.png")))
    window.show()

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
