"""Entry point: `python3 -m sirena_ui` launches the Sirena Control Center."""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from sirena_ui.main_window import MainWindow
from sirena_ui.styles import STYLESHEET, asset_path
from sirena_ui.workers.nina_service import NinaService


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Sirena")
    app.setOrganizationName("Sirena Technologies")
    app.setWindowIcon(QIcon(asset_path("sirena_app_icon.png")))
    app.setStyleSheet(STYLESHEET)

    service = NinaService()
    window = MainWindow(service)
    window.setWindowIcon(QIcon(asset_path("sirena_app_icon.png")))
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
