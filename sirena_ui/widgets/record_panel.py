"""Right-side panel of the Nina screen, Record tab."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class RecordPanel(QWidget):
    start_requested = pyqtSignal(dict)
    stop_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel("Record Action")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        outer.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(form.labelAlignment())

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. namaste_v2")
        form.addRow("Action name", self._name)

        self._duration = QDoubleSpinBox()
        self._duration.setRange(1.0, 120.0)
        self._duration.setSingleStep(0.5)
        self._duration.setValue(8.0)
        self._duration.setSuffix(" s")
        form.addRow("Duration", self._duration)

        self._hz = QSpinBox()
        self._hz.setRange(5, 100)
        self._hz.setValue(20)
        self._hz.setSuffix(" Hz")
        form.addRow("Sample rate", self._hz)

        self._countdown = QSpinBox()
        self._countdown.setRange(0, 10)
        self._countdown.setValue(3)
        self._countdown.setSuffix(" s")
        form.addRow("Countdown", self._countdown)

        self._register = QCheckBox("Register in manifest")
        self._register.setChecked(True)
        form.addRow("", self._register)

        self._hold_after = QCheckBox("Re-engage torque after recording (hold final pose)")
        self._hold_after.setChecked(False)
        form.addRow("", self._hold_after)

        outer.addLayout(form)

        outer.addStretch(1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        outer.addWidget(self._progress)

        button_row = QHBoxLayout()
        self._start = QPushButton("START RECORDING")
        self._start.setObjectName("startButton")
        self._start.clicked.connect(self._on_start)
        self._stop = QPushButton("STOP RECORDING \u25A0")
        self._stop.setObjectName("stopButton")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(self.stop_requested.emit)
        button_row.addWidget(self._start, stretch=1)
        button_row.addWidget(self._stop, stretch=1)
        outer.addLayout(button_row)

    # ---- public API ---------------------------------------------------

    def set_recording(self, recording: bool) -> None:
        self._start.setEnabled(not recording)
        self._stop.setEnabled(recording)
        self._name.setEnabled(not recording)
        self._duration.setEnabled(not recording)
        self._hz.setEnabled(not recording)
        self._countdown.setEnabled(not recording)
        self._register.setEnabled(not recording)
        self._hold_after.setEnabled(not recording)
        if not recording:
            self._progress.setValue(0)

    def set_progress(self, captured: int, total: int) -> None:
        if total <= 0:
            self._progress.setValue(0)
            return
        self._progress.setValue(int(round(100.0 * captured / total)))

    # ---- internals ----------------------------------------------------

    def _on_start(self) -> None:
        name = self._name.text().strip() or "recording"
        params = {
            "name": name,
            "seconds": float(self._duration.value()),
            "hz": float(self._hz.value()),
            "countdown": float(self._countdown.value()),
            "register": bool(self._register.isChecked()),
            "hold_after": bool(self._hold_after.isChecked()),
        }
        self.start_requested.emit(params)
