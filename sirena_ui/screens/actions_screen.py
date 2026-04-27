"""
Actions screen with Playback / Record / Audio sub-tabs.

This is the new home for everything that used to live on the
old `NinaScreen`. It keeps the same behaviour but fits inside the
v2 app shell (persistent sidebar + header + footer).
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.audio_panel import AudioPanel
from sirena_ui.widgets.common import Breadcrumb, Pill
from sirena_ui.widgets.nina_image_panel import NinaImagePanel
from sirena_ui.widgets.playback_panel import PlaybackPanel
from sirena_ui.widgets.record_panel import RecordPanel
from sirena_ui.workers.error_hints import explain_error
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.playback_worker import PlaybackWorker
from sirena_ui.workers.record_worker import RecordWorker


class ActionsScreen(QWidget):
    bus_status_changed = pyqtSignal(str)

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._playback_worker: PlaybackWorker | None = None
        self._record_worker: RecordWorker | None = None
        self._health_text = "Status: Idle"
        self._bus_initialized = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        # Top breadcrumb + status pill row
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self._breadcrumb = Breadcrumb("Nina", "Actions")
        top.addWidget(self._breadcrumb)
        top.addStretch(1)
        self._status_pill = Pill("Bus: idle", Pill.KIND_NEUTRAL)
        top.addWidget(self._status_pill)
        outer.addLayout(top)

        # Sub-tab bar
        outer.addLayout(self._build_subtabs())

        # Body: Nina image left, content stack right
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self._image_panel = NinaImagePanel()
        body.addWidget(self._image_panel, stretch=42)

        self._stack = QStackedWidget()
        self._playback_panel = PlaybackPanel(service)
        self._playback_panel.play_requested.connect(self._on_play)
        self._playback_panel.audio_edit_requested.connect(self._on_edit_audio_inline)
        self._playback_panel.delete_requested.connect(self._on_delete_action)
        self._record_panel = RecordPanel()
        self._record_panel.start_requested.connect(self._on_start_record)
        self._record_panel.stop_requested.connect(self._on_stop_record)
        self._audio_panel = AudioPanel(service)
        self._audio_panel.audio_changed.connect(self._on_audio_changed)
        self._stack.addWidget(self._playback_panel)
        self._stack.addWidget(self._record_panel)
        self._stack.addWidget(self._audio_panel)
        body.addWidget(self._stack, stretch=58)

        outer.addLayout(body, stretch=1)

    # ---------- sub-tabs ----------

    def _build_subtabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        labels = [("Playback", 0), ("Record", 1), ("Audio", 2)]
        for label, idx in labels:
            btn = QPushButton(label)
            btn.setObjectName("subTabButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, i=idx: self._switch_tab(i))
            self._tab_group.addButton(btn)
            row.addWidget(btn)

        # Default selected tab
        first = self._tab_group.buttons()[0]
        first.setChecked(True)

        # Subtle bottom rule under the tab strip
        row.addStretch(1)
        return row

    def _switch_tab(self, idx: int) -> None:
        if (
            self._record_worker is not None
            and self._record_worker.isRunning()
            and idx != 1
        ):
            QMessageBox.information(
                self, "Recording in progress",
                "Stop the current recording before switching tabs.",
            )
            self._tab_group.buttons()[1].setChecked(True)
            return
        self._stack.setCurrentIndex(idx)
        if idx == 2:
            self._audio_panel.refresh()

    # ---------- lifecycle ----------

    def on_enter(self) -> None:
        if not self._bus_initialized:
            try:
                health = self._service.ensure_bus()
                self._bus_initialized = True
            except Exception as exc:
                hint = explain_error(exc, self._service.settings)
                QMessageBox.critical(
                    self, "Bus error",
                    f"Could not initialize Dynamixel bus:\n\n{hint}",
                )
                self._set_status("Status: bus error")
                self._status_pill.setText("Bus: error")
                self._status_pill.set_kind(Pill.KIND_ERROR)
                self.bus_status_changed.emit("Bus offline")
                return
            if health["connected"]:
                self._health_text = (
                    f"Status: Idle | Motors {health['detected']}/{health['expected']}"
                    f" healthy | Torque ON"
                )
                self._status_pill.setText(
                    f"Bus connected \u00b7 {health['detected']}/{health['expected']} motors"
                )
                self._status_pill.set_kind(Pill.KIND_OK)
                self.bus_status_changed.emit(
                    f"Motors {health['detected']}/{health['expected']} \u00b7 Bus ready"
                )
            else:
                self._health_text = (
                    f"Status: Idle | Motors {health['detected']}/{health['expected']}"
                    " responded | missing motors will not move"
                )
                self._status_pill.setText(
                    f"Bus partial \u00b7 {health['detected']}/{health['expected']} motors"
                )
                self._status_pill.set_kind(Pill.KIND_WARN)
                self.bus_status_changed.emit("Bus partial")
            self._set_status(self._health_text)
        self._playback_panel.refresh()
        self._audio_panel.refresh()

    # ---------- playback ----------

    def _on_play(self, name: str) -> None:
        if self._record_worker is not None and self._record_worker.isRunning():
            return
        if self._playback_worker is not None and self._playback_worker.isRunning():
            return
        self._playback_panel.set_buttons_enabled(False)
        audio_path = self._service.action_audio_path(name)
        audio_offset = self._service.action_audio_offset(name) if audio_path else 0.0
        if audio_path and audio_offset > 0:
            suffix = f" (audio +{audio_offset:.1f}s)"
        elif audio_path:
            suffix = " (with audio)"
        else:
            suffix = ""
        self._set_status(f"Status: Playing '{name}'{suffix} \u2026")
        self._playback_worker = PlaybackWorker(
            self._service,
            name,
            audio_path=audio_path,
            audio_offset_sec=audio_offset,
        )
        self._playback_worker.finished_ok.connect(self._on_play_done)
        self._playback_worker.failed.connect(self._on_play_failed)
        self._playback_worker.start()

    def _on_play_done(self, _name: str) -> None:
        self._playback_panel.set_buttons_enabled(True)
        self._set_status(self._health_text)
        self._playback_worker = None

    def _on_play_failed(self, message: str) -> None:
        self._playback_panel.set_buttons_enabled(True)
        self._set_status(f"Status: playback failed - {message}")
        self._playback_worker = None
        QMessageBox.warning(self, "Playback failed", message)

    # ---------- audio editor ----------

    def _on_edit_audio_inline(self, name: str) -> None:
        """Switch to the Audio sub-tab with the given action pre-selected."""
        if self._playback_worker is not None and self._playback_worker.isRunning():
            QMessageBox.information(
                self, "Playback in progress",
                "Wait for the current playback to finish before editing audio.",
            )
            return
        if self._record_worker is not None and self._record_worker.isRunning():
            QMessageBox.information(
                self, "Recording in progress",
                "Stop recording before editing audio.",
            )
            return
        self._tab_group.buttons()[2].setChecked(True)
        self._switch_tab(2)
        self._audio_panel.select_action(name)

    def _on_audio_changed(self, _name: str) -> None:
        self._playback_panel.refresh()

    # ---------- delete ----------

    def _on_delete_action(self, name: str) -> None:
        if self._playback_worker is not None and self._playback_worker.isRunning():
            QMessageBox.information(
                self, "Playback in progress",
                "Wait for the current playback to finish before deleting.",
            )
            return
        if self._record_worker is not None and self._record_worker.isRunning():
            QMessageBox.information(
                self, "Recording in progress",
                "Stop recording before deleting an action.",
            )
            return

        neutral = getattr(self._service.settings, "neutral_action_name", None)
        if isinstance(neutral, str) and neutral and name == neutral:
            QMessageBox.warning(
                self, "Cannot delete",
                f"'{name}' is the configured neutral pose used during "
                "startup. Change NINA_NEUTRAL_ACTION before removing it.",
            )
            return

        info = {}
        try:
            info = self._service.get_action_audio_info(name)
        except Exception:
            info = {}
        has_audio = bool(info.get("audio_path") or info.get("audio_rel"))

        msg = (
            f"Delete '{name}'?\n\n"
            "This removes the action from the manifest and deletes its "
            "recording file from disk."
        )
        if has_audio:
            msg += (
                "\n\nThe associated audio clip on disk will be left in "
                "place (other actions may use it)."
            )

        confirm = QMessageBox.question(
            self,
            "Delete action",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            result = self._service.delete_action(name)
        except Exception as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return

        deleted_file = result.get("deleted_recording")
        if deleted_file:
            self._set_status(
                f"Status: deleted '{name}' (manifest + recording file)"
            )
        else:
            self._set_status(
                f"Status: removed '{name}' from manifest "
                "(recording file already missing)"
            )
        self._playback_panel.refresh()
        self._audio_panel.refresh()

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
        self._audio_panel.refresh()

    def _on_record_failed(self, message: str) -> None:
        self._record_panel.set_recording(False)
        self._record_worker = None
        self._set_status(f"Status: recording failed - {message}")
        QMessageBox.warning(self, "Recording failed", message)

    # ---------- helpers ----------

    def _set_status(self, text: str) -> None:
        self._image_panel.set_status(text)
