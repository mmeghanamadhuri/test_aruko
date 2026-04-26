"""Vision screen: USB camera + face / object recognition controls.

Like the Drive screen, this is the polished UI scaffold; the real
camera + ML pipeline will hook into the same layout once it
lands. The right-hand recognition toggles already drive a small
in-process state object so the look-and-feel is final.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import (
    Breadcrumb,
    Card,
    CardTitle,
    MutedLabel,
    Pill,
    SectionLabel,
)
from sirena_ui.workers.nina_service import NinaService


class _ToggleRow(QFrame):
    """Inline label + iOS-style toggle pill."""

    toggled = pyqtSignal(bool)

    def __init__(self, label: str, on: bool = False, parent=None) -> None:
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(8)
        title = QLabel(label)
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 14px; background-color: transparent;"
        )
        h.addWidget(title, stretch=1)

        self._btn = QPushButton("ON" if on else "OFF")
        self._btn.setObjectName("togglePill")
        self._btn.setCheckable(True)
        self._btn.setChecked(on)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setFixedWidth(74)
        self._btn.toggled.connect(self._on_toggled)
        h.addWidget(self._btn)

    def _on_toggled(self, checked: bool) -> None:
        self._btn.setText("ON" if checked else "OFF")
        self.toggled.emit(checked)


class _DetectionRow(Card):
    def __init__(self, label: str, kind: str, confidence: int, parent=None) -> None:
        super().__init__(padding=12, spacing=4, subtle=True, parent=parent)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.add_layout(h)
        title = QLabel(label)
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 14px; font-weight: 600;"
            " background-color: transparent;"
        )
        h.addWidget(title)
        sub = QLabel(f"\u00b7 {kind}")
        sub.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
        )
        h.addWidget(sub)
        h.addStretch(1)
        h.addWidget(Pill(f"{confidence}%", Pill.KIND_OK if kind == "face" else Pill.KIND_NEUTRAL))


class VisionScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Breadcrumb("Nina", "Vision"))
        top.addStretch(1)
        self._cam_pill = Pill("USB camera not connected", Pill.KIND_NEUTRAL)
        top.addWidget(self._cam_pill)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_camera_card(), stretch=64)
        body.addWidget(self._build_recognition_card(), stretch=36)

    # ---------- camera ----------

    def _build_camera_card(self) -> Card:
        card = Card(padding=16, spacing=10)

        header = QHBoxLayout()
        card.add_layout(header)
        header.addWidget(CardTitle("Camera"))
        header.addStretch(1)
        self._fps_pill = Pill("\u2014", Pill.KIND_NEUTRAL)
        header.addWidget(self._fps_pill)

        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(420)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setAlignment(Qt.AlignCenter)
        glyph = QLabel("\u25CE")
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 96px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        v.addWidget(glyph)
        msg = QLabel(
            "Plug in a USB camera and the live feed will appear here.\n"
            "Detected faces and objects will be drawn on top."
        )
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 13px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        v.addWidget(msg)
        card.add(viewport, stretch=1)

        return card

    # ---------- recognition rail ----------

    def _build_recognition_card(self) -> Card:
        card = Card(padding=20, spacing=10)

        card.add(SectionLabel("Recognition"))
        face = _ToggleRow("Face recognition")
        obj = _ToggleRow("Object detection")
        track = _ToggleRow("Person tracking")
        for w in (face, obj, track):
            card.add(w)

        card.add(SectionLabel("Detected"))
        empty = MutedLabel("No detections yet \u2014 connect a camera and toggle a feature on.")
        empty.setWordWrap(True)
        card.add(empty)

        card.add(SectionLabel("Camera"))
        form = QVBoxLayout()
        form.setSpacing(8)
        card.add_layout(form)

        res_row = QHBoxLayout()
        res_row.setSpacing(8)
        res_row.addWidget(MutedLabel("Resolution"))
        res = QComboBox()
        res.addItems(["1280x720", "640x480", "320x240"])
        res_row.addWidget(res, stretch=1)
        form.addLayout(res_row)

        bright_row = QHBoxLayout()
        bright_row.setSpacing(8)
        bright_row.addWidget(MutedLabel("Brightness"))
        bright = QSlider(Qt.Horizontal)
        bright.setRange(0, 100)
        bright.setValue(55)
        bright_row.addWidget(bright, stretch=1)
        self._bright_pill = Pill("55%", Pill.KIND_NEUTRAL)
        bright.valueChanged.connect(lambda v: self._bright_pill.setText(f"{v}%"))
        bright_row.addWidget(self._bright_pill)
        form.addLayout(bright_row)

        exp_row = QHBoxLayout()
        exp_row.setSpacing(8)
        exp_row.addWidget(MutedLabel("Exposure"))
        exp = QComboBox()
        exp.addItems(["Auto", "Manual: 1/30", "Manual: 1/60", "Manual: 1/120"])
        exp_row.addWidget(exp, stretch=1)
        form.addLayout(exp_row)

        card.add_stretch()

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        card.add_layout(button_row)
        train = QPushButton("Train a new face")
        train.setObjectName("primaryButton")
        train.setCursor(Qt.PointingHandCursor)
        train.clicked.connect(self._on_train)
        button_row.addWidget(train)
        snap = QPushButton("Snapshot")
        snap.setObjectName("secondaryButton")
        snap.setCursor(Qt.PointingHandCursor)
        snap.clicked.connect(self._on_snapshot)
        button_row.addWidget(snap)

        return card

    # ---------- handlers ----------

    def _on_train(self) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Camera not connected",
            "Plug in a USB camera and try again. The vision pipeline will be"
            " enabled in the next firmware update.",
        )

    def _on_snapshot(self) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Camera not connected",
            "Snapshots will be available once the USB camera is connected.",
        )
