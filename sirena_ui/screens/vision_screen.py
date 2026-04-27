"""Vision screen: USB camera + face / object recognition.

Live preview is driven by `service.vision` (a `VisionWorker`) which
runs the YuNet face detector and the YOLOv8 object detector on a
worker thread. The screen owns no detection state of its own - it
just toggles the worker, renders incoming frames, and surfaces the
worker's status as pills + lists.

Dev hosts without OpenCV / Ultralytics / a USB camera get a clear
"Vision unavailable" pill and the rest of the screen still renders
so layout work is unaffected.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
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
from sirena_ui.workers.vision_types import KIND_FACE, Detection


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

    def set_enabled_with_hint(self, enabled: bool, hint: str = "") -> None:
        self._btn.setEnabled(enabled)
        self._btn.setToolTip(hint)

    def _on_toggled(self, checked: bool) -> None:
        self._btn.setText("ON" if checked else "OFF")
        self.toggled.emit(checked)


class _DetectionRow(Card):
    def __init__(
        self,
        label: str,
        kind: str,
        confidence_pct: int,
        parent=None,
    ) -> None:
        super().__init__(padding=10, spacing=4, subtle=True, parent=parent)
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
        h.addWidget(
            Pill(
                f"{confidence_pct}%",
                Pill.KIND_OK if kind == KIND_FACE else Pill.KIND_NEUTRAL,
            )
        )


class VisionScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._connected = False
        self._detections_panel: Optional[Card] = None
        self._detections_layout: Optional[QVBoxLayout] = None
        self._face_toggle: Optional[_ToggleRow] = None
        self._object_toggle: Optional[_ToggleRow] = None
        self._track_toggle: Optional[_ToggleRow] = None
        self._viewport_label: Optional[QLabel] = None
        self._viewport_placeholder: Optional[QWidget] = None

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

        self._wire_signals()

    # ---------- entry / exit ----------

    def on_enter(self) -> None:
        worker = self._service.vision
        # Stay idempotent - VisionWorker.start() is a no-op if already
        # running, which keeps re-navigation snappy.
        worker.start()
        # Also reflect the latest known status immediately so the pill
        # doesn't lag the first frame.
        self._apply_status(self._status_to_dict(worker.status()))

    def on_leave(self) -> None:
        # Release the camera when the user navigates away. Reset the
        # toggles AND the worker flags together so re-entering doesn't
        # secretly keep detection running while the toggles read OFF.
        worker = self._service.vision
        if self._face_toggle is not None and self._face_toggle._btn.isChecked():  # noqa: SLF001
            self._face_toggle._btn.setChecked(False)  # noqa: SLF001 - emits toggled -> worker.set_face_enabled(False)
        if self._object_toggle is not None and self._object_toggle._btn.isChecked():  # noqa: SLF001
            self._object_toggle._btn.setChecked(False)  # noqa: SLF001
        try:
            worker.stop()
        except Exception:
            pass

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
        viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # IMPORTANT: do NOT call setAlignment() on this layout. With an
        # alignment set, QBoxLayout hands children their sizeHint instead
        # of stretching them, and a QLabel's sizeHint follows the pixmap.
        # The pixmap is scaled to the label size in `_on_frame`, so an
        # alignment-centered label collapses to a "dot" on first paint.
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Placeholder shown until the first frame arrives. It's a self-
        # contained widget that centers its own children, so we don't
        # need an alignment on the parent layout (see comment above).
        placeholder = QWidget(viewport)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ph_layout = QVBoxLayout(placeholder)
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(8)
        ph_layout.addStretch(1)
        glyph = QLabel("\u25CE", placeholder)
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 96px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(glyph)
        msg = QLabel(
            "Plug in a USB camera and the live feed will appear here.\n"
            "Detected faces and objects will be drawn on top.",
            placeholder,
        )
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 13px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(msg)
        ph_layout.addStretch(1)
        v.addWidget(placeholder, stretch=1)
        self._viewport_placeholder = placeholder

        # Live feed label - swapped in once the first frame arrives.
        # Ignored size policy so the (potentially huge) pixmap can't
        # feed back into the layout and balloon or collapse the card.
        feed = QLabel(viewport)
        feed.setAlignment(Qt.AlignCenter)
        feed.setStyleSheet("background-color: transparent;")
        feed.setMinimumSize(320, 240)
        feed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        feed.hide()
        v.addWidget(feed, stretch=1)
        self._viewport_label = feed

        card.add(viewport, stretch=1)
        return card

    # ---------- recognition rail ----------

    def _build_recognition_card(self) -> Card:
        card = Card(padding=20, spacing=10)

        card.add(SectionLabel("Recognition"))
        face = _ToggleRow("Face recognition")
        obj = _ToggleRow("Object detection")
        track = _ToggleRow("Person tracking")
        track.set_enabled_with_hint(
            False, "Person tracking ships in the next firmware update."
        )
        for w in (face, obj, track):
            card.add(w)
        self._face_toggle = face
        self._object_toggle = obj
        self._track_toggle = track

        card.add(SectionLabel("Detected"))
        # The list lives in its own Card so we can swap children freely
        # without disturbing the surrounding layout.
        det_card = Card(padding=8, spacing=6, subtle=True)
        det_layout = QVBoxLayout()
        det_layout.setContentsMargins(0, 0, 0, 0)
        det_layout.setSpacing(6)
        det_card.add_layout(det_layout)
        self._detections_panel = det_card
        self._detections_layout = det_layout
        card.add(det_card)
        self._render_detections([])

        card.add(SectionLabel("Camera"))
        form = QVBoxLayout()
        form.setSpacing(8)
        card.add_layout(form)

        res_row = QHBoxLayout()
        res_row.setSpacing(8)
        res_row.addWidget(MutedLabel("Resolution"))
        res = QComboBox()
        res.addItems(["1280x720", "640x480", "320x240"])
        res.setCurrentText("640x480")
        res.currentTextChanged.connect(self._on_resolution)
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

    # ---------- worker wiring ----------

    def _wire_signals(self) -> None:
        worker = self._service.vision
        worker.frame_ready.connect(self._on_frame)
        worker.detections_changed.connect(self._render_detections)
        worker.fps_changed.connect(self._on_fps)
        worker.status_changed.connect(self._apply_status)

        if self._face_toggle is not None:
            self._face_toggle.toggled.connect(worker.set_face_enabled)
        if self._object_toggle is not None:
            self._object_toggle.toggled.connect(worker.set_object_enabled)

    # ---------- handlers ----------

    def _on_frame(self, image: QImage) -> None:
        if self._viewport_label is None:
            return
        if (
            self._viewport_placeholder is not None
            and self._viewport_placeholder.isVisible()
        ):
            self._viewport_placeholder.hide()
            self._viewport_label.show()
        # Scale to the label's current size while preserving aspect
        # ratio. With QSizePolicy.Ignored the label fills whatever the
        # parent layout grants it, so this gives a frame that actually
        # uses the viewport instead of collapsing to the pixmap's
        # natural size.
        target = self._viewport_label.size()
        if target.width() <= 0 or target.height() <= 0:
            self._viewport_label.setPixmap(QPixmap.fromImage(image))
            return
        pix = QPixmap.fromImage(image).scaled(
            target,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._viewport_label.setPixmap(pix)

    def _on_fps(self, fps: float) -> None:
        self._fps_pill.setText(f"{fps:0.1f} fps")

    def _apply_status(self, status: dict) -> None:
        camera_open = bool(status.get("camera_open", False))
        message = str(status.get("message", "") or "")
        self._connected = camera_open
        if camera_open:
            self._cam_pill.setText("USB camera connected")
            self._cam_pill.set_kind(Pill.KIND_OK)
        elif message and "OpenCV" in message or "ultralytics" in message.lower():
            self._cam_pill.setText("Vision unavailable")
            self._cam_pill.set_kind(Pill.KIND_WARN)
            self._cam_pill.setToolTip(message)
        else:
            label = message or "USB camera not connected"
            self._cam_pill.setText(label)
            self._cam_pill.set_kind(Pill.KIND_NEUTRAL)
            self._cam_pill.setToolTip("")

        if not camera_open and self._viewport_label is not None:
            self._viewport_label.clear()
            self._viewport_label.hide()
            if self._viewport_placeholder is not None:
                self._viewport_placeholder.show()
            self._fps_pill.setText("\u2014")

    def _render_detections(self, detections: List[Detection]) -> None:
        layout = self._detections_layout
        if layout is None:
            return
        # Clear existing rows.
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not detections:
            empty = MutedLabel(
                "No detections yet \u2014 toggle Face or Object to start."
            )
            empty.setWordWrap(True)
            layout.addWidget(empty)
            return
        # Cap the visible list so a busy frame doesn't blow up the rail.
        for det in detections[:8]:
            row = _DetectionRow(
                det.label,
                det.kind,
                int(round(det.confidence * 100)),
            )
            layout.addWidget(row)
        if len(detections) > 8:
            more = MutedLabel(f"... and {len(detections) - 8} more")
            layout.addWidget(more)

    def _on_resolution(self, value: str) -> None:
        try:
            w, h = (int(x) for x in value.lower().split("x"))
        except ValueError:
            return
        self._service.vision.set_resolution(w, h)

    def _on_train(self) -> None:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.information(
            self,
            "Coming soon",
            "Face enrollment / recognition ships in the next firmware "
            "update. Today's pipeline detects faces but doesn't match "
            "them to identities.",
        )

    def _on_snapshot(self) -> None:
        from PyQt5.QtWidgets import QMessageBox

        worker = self._service.vision
        path = worker.snapshot()
        if path is None:
            QMessageBox.warning(
                self,
                "Snapshot",
                "No frame to capture. Connect a USB camera and try again.",
            )
            return
        QMessageBox.information(
            self,
            "Snapshot saved",
            f"Saved to:\n{path}",
        )

    # ---------- helpers ----------

    @staticmethod
    def _status_to_dict(status) -> dict:
        # Mirror dataclasses.asdict without importing dataclasses for
        # this single use; works for both VisionStatus and dict inputs.
        if isinstance(status, dict):
            return dict(status)
        return {
            "camera_open": bool(getattr(status, "camera_open", False)),
            "face_ready": bool(getattr(status, "face_ready", False)),
            "object_ready": bool(getattr(status, "object_ready", False)),
            "message": str(getattr(status, "message", "") or ""),
        }
