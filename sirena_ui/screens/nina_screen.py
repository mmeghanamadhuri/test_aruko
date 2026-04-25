"""Nina control screen with Playback / Record tabs."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.nina_image_panel import NinaImagePanel
from sirena_ui.widgets.playback_panel import PlaybackPanel
from sirena_ui.widgets.record_panel import RecordPanel
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.playback_worker import PlaybackWorker
from sirena_ui.workers.record_worker import RecordWorker


class NinaScreen(QWidget):
    back_requested = pyqtSignal()

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._playback_worker: PlaybackWorker | None = None
        self._record_worker: RecordWorker | None = None
        self._health_text = "Status: Idle"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addLayout(self._build_tab_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._image_panel = NinaImagePanel()
        body.addWidget(self._image_panel, stretch=45)

        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setStyleSheet("color: #e3e3e6;")
        body.addWidget(divider)

        self._stack = QStackedWidget()
        self._playback_panel = PlaybackPanel(service)
        self._playback_panel.play_requested.connect(self._on_play)
        self._record_panel = RecordPanel()
        self._record_panel.start_requested.connect(self._on_start_record)
        self._record_panel.stop_requested.connect(self._on_stop_record)
        self._stack.addWidget(self._playback_panel)
        self._stack.addWidget(self._record_panel)
        body.addWidget(self._stack, stretch=55)

        outer.addLayout(body, stretch=1)

    # ---------- header tabs ----------

    def _build_tab_bar(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        self._tab_play = QPushButton("Playback")
        self._tab_play.setObjectName("tabButton")
        self._tab_play.setCheckable(True)
        self._tab_play.setChecked(True)
        self._tab_play.setCursor(Qt.PointingHandCursor)
        self._tab_play.clicked.connect(lambda: self._switch_tab(0))
        self._tab_group.addButton(self._tab_play)
        row.addWidget(self._tab_play, stretch=1)

        self._tab_rec = QPushButton("Record")
        self._tab_rec.setObjectName("tabButton")
        self._tab_rec.setCheckable(True)
        self._tab_rec.setCursor(Qt.PointingHandCursor)
        self._tab_rec.clicked.connect(lambda: self._switch_tab(1))
        self._tab_group.addButton(self._tab_rec)
        row.addWidget(self._tab_rec, stretch=1)

        return row

    def _switch_tab(self, idx: int) -> None:
        if self._record_worker is not None and self._record_worker.isRunning() and idx == 0:
            QMessageBox.information(
                self, "Recording in progress",
                "Stop the current recording before switching to Playback.",
            )
            self._tab_rec.setChecked(True)
            return
        self._stack.setCurrentIndex(idx)

    # ---------- lifecycle ----------

    def on_enter(self) -> None:
        """Called whenever this screen becomes visible."""
        try:
            health = self._service.ensure_bus()
        except Exception as exc:
            QMessageBox.critical(self, "Bus error", f"Could not initialize Dynamixel bus:\n{exc}")
            self._set_status("Status: bus error")
            return
        if health["connected"]:
            self._health_text = (
                f"Status: Idle | Motors {health['detected']}/{health['expected']} healthy | Torque ON"
            )
        else:
            self._health_text = (
                f"Status: Idle | Motors {health['detected']}/{health['expected']} responded | "
                "missing motors will not move"
            )
        self._set_status(self._health_text)
        self._playback_panel.refresh()

    # ---------- playback ----------

    def _on_play(self, name: str) -> None:
        if self._record_worker is not None and self._record_worker.isRunning():
            return
        if self._playback_worker is not None and self._playback_worker.isRunning():
            return
        self._playback_panel.set_buttons_enabled(False)
        self._set_status(f"Status: Playing '{name}' \u2026")
        self._playback_worker = PlaybackWorker(self._service, name)
        self._playback_worker.finished_ok.connect(self._on_play_done)
        self._playback_worker.failed.connect(self._on_play_failed)
        self._playback_worker.start()

    def _on_play_done(self, name: str) -> None:
        self._playback_panel.set_buttons_enabled(True)
        self._set_status(self._health_text)
        self._playback_worker = None

    def _on_play_failed(self, message: str) -> None:
        self._playback_panel.set_buttons_enabled(True)
        self._set_status(f"Status: playback failed - {message}")
        self._playback_worker = None
        QMessageBox.warning(self, "Playback failed", message)

    # ---------- recording ----------

    def _on_start_record(self, params: dict) -> None:
        if self._playback_worker is not None and self._playback_worker.isRunning():
            QMessageBox.information(
                self, "Playback in progress",
                "Wait for the current playback to finish before recording.",
            )
            return
        self._record_panel.set_recording(True)
        self._record_panel.set_progress(0, max(1, int(params["seconds"] * params["hz"])))
        self._set_status("Status: preparing to record \u2026")
        worker = RecordWorker(
            self._service,
            name=params["name"],
            seconds=params["seconds"],
            hz=params["hz"],
            countdown_sec=params["countdown"],
            register=params["register"],
            hold_after=params["hold_after"],
        )
        worker.countdown.connect(self._on_record_countdown)
        worker.progress.connect(self._on_record_progress)
        worker.finished_ok.connect(self._on_record_done)
        worker.failed.connect(self._on_record_failed)
        self._record_worker = worker
        worker.start()

    def _on_stop_record(self) -> None:
        if self._record_worker is not None:
            self._record_worker.request_stop()
            self._set_status("Status: stopping recording \u2026")

    def _on_record_countdown(self, remaining: int) -> None:
        self._set_status(
            f"Status: Torque RELEASED | starting in {remaining} \u2026"
        )

    def _on_record_progress(self, captured: int, target: int, elapsed: float) -> None:
        self._record_panel.set_progress(captured, target)
        self._set_status(
            f"Status: \u25CF RECORDING | Torque RELEASED | "
            f"Frames {captured}/{target} \u2022 {elapsed:.1f}s"
        )

    def _on_record_done(self, name: str, frame_count: int) -> None:
        self._record_panel.set_recording(False)
        self._record_worker = None
        self._set_status(f"Status: saved '{name}' ({frame_count} frames)")
        self._playback_panel.refresh()

    def _on_record_failed(self, message: str) -> None:
        self._record_panel.set_recording(False)
        self._record_worker = None
        self._set_status(f"Status: recording failed - {message}")
        QMessageBox.warning(self, "Recording failed", message)

    # ---------- helpers ----------

    def _set_status(self, text: str) -> None:
        self._image_panel.set_status(text)
