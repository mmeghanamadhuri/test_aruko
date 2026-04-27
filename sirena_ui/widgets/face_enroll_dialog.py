"""
Modal that captures face samples and adds them to the FaceDB.

Flow:

  1. User types a name.
  2. We confirm face detection is on (or turn it on) and ask the user
     to look at the camera.
  3. We trigger `VisionWorker.enroll_face(name)` and watch its
     `enrollment_progress` / `enrollment_finished` signals.
  4. The dialog reports success / failure and closes.

The dialog is intentionally chatty: enrollment is the moment a
non-developer is most likely to make a mistake (multiple faces in
frame, too far from the camera, low light), so we surface the exact
reason whenever we bail.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from sirena_ui.workers.vision_worker import VisionWorker


class FaceEnrollDialog(QDialog):
    def __init__(
        self,
        worker: VisionWorker,
        *,
        target_samples: int = 8,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Train a new face")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._worker = worker
        self._target = int(target_samples)
        self._capturing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("Train a new face")
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 600;"
        )
        outer.addWidget(title)

        intro = QLabel(
            "Type the person's name, then click Start. Look directly "
            "at the camera and stay still. Make sure only one face is "
            "in frame -- additional faces are skipped."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #6c6c70; font-size: 13px;")
        outer.addWidget(intro)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. hari")
        self._name_edit.setMaxLength(32)
        outer.addWidget(self._name_edit)

        self._progress = QProgressBar()
        self._progress.setRange(0, self._target)
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m samples")
        outer.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #6c6c70; font-size: 12px;")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        outer.addLayout(button_row)
        button_row.addStretch(1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("secondaryButton")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(self._cancel_btn)

        self._start_btn = QPushButton("Start")
        self._start_btn.setObjectName("primaryButton")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start)
        button_row.addWidget(self._start_btn)

        worker.enrollment_progress.connect(self._on_progress)
        worker.enrollment_finished.connect(self._on_finished)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if self._capturing:
            return
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.information(
                self,
                "Name required",
                "Type a name for this face before starting.",
            )
            return
        self._capturing = True
        self._name_edit.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._set_status(
            "Look at the camera... capturing samples now."
        )
        self._progress.setValue(0)
        self._worker.enroll_face(name, target_samples=self._target)

    def _on_progress(self, captured: int, target: int) -> None:
        if not self._capturing:
            return
        if target != self._progress.maximum():
            self._progress.setRange(0, max(1, int(target)))
        self._progress.setValue(min(int(captured), self._progress.maximum()))

    def _on_finished(self, payload: dict) -> None:
        if not self._capturing:
            return
        self._capturing = False
        ok = bool(payload.get("ok"))
        message = str(payload.get("message", "")) or (
            "Face training complete." if ok else "Face training failed."
        )
        if ok:
            QMessageBox.information(self, "Face trained", message)
            self.accept()
            return
        QMessageBox.warning(self, "Training failed", message)
        # Re-enable so the operator can retry without re-typing the name
        self._name_edit.setEnabled(True)
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)
        self._progress.setValue(0)
        self._set_status(message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    def reject(self) -> None:  # noqa: D401
        # Disconnect so a late-arriving signal doesn't try to drive a
        # disposed dialog.
        try:
            self._worker.enrollment_progress.disconnect(self._on_progress)
        except (TypeError, RuntimeError):
            pass
        try:
            self._worker.enrollment_finished.disconnect(self._on_finished)
        except (TypeError, RuntimeError):
            pass
        super().reject()

    def accept(self) -> None:  # noqa: D401
        try:
            self._worker.enrollment_progress.disconnect(self._on_progress)
        except (TypeError, RuntimeError):
            pass
        try:
            self._worker.enrollment_finished.disconnect(self._on_finished)
        except (TypeError, RuntimeError):
            pass
        super().accept()
