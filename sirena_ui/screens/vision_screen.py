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
from sirena_ui.widgets.face_enroll_dialog import FaceEnrollDialog
from sirena_ui.workers.face_follow_controller import FaceFollowController
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.object_announcer import ObjectAnnouncer
from sirena_ui.workers.vision_types import KIND_FACE, KIND_OBJECT, Detection


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
        self._viewport_label: Optional[QLabel] = None
        self._viewport_placeholder: Optional[QWidget] = None
        self._obj_conf_slider: Optional[QSlider] = None
        self._obj_conf_pill: Optional[Pill] = None
        # Recognised-face audio: wired in `NinaService.vision` so greetings
        # run from Drive / Perception too, not only this screen.
        # Speaks the current set of detected object labels when the
        # operator clicks "Play Objects". Cached MP3s + 1.5 s cooldown
        # so a double-click doesn't queue two overlapping playbacks.
        self._announcer = ObjectAnnouncer(parent=self)
        # Most recent object-only detections, refreshed every time
        # `detections_changed` fires. Used by "Play Objects" so the
        # button speaks whatever is on screen *right now*.
        self._latest_object_labels: List[str] = []
        self._play_objects_btn: Optional[QPushButton] = None
        self._follow_combo: Optional[QComboBox] = None
        self._follow_start_btn: Optional[QPushButton] = None
        self._follow_stop_btn: Optional[QPushButton] = None
        self._follow_pill: Optional[Pill] = None
        self._follow = FaceFollowController(self._service.drive, parent=self)
        # Whether on_enter is currently holding a refcount on the
        # vision worker; tracked so on_leave only ever calls one
        # release() per acquire() even if Qt fires on_leave twice.
        self._holds_camera = False

        outer = QVBoxLayout(self)
        # 10 / 8 trim from 20 / 14 to fit 1024 x 600.
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Breadcrumb("Nina", "Vision"))
        top.addStretch(1)
        self._cam_pill = Pill("USB camera not connected", Pill.KIND_NEUTRAL)
        top.addWidget(self._cam_pill)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_camera_card(), stretch=62)
        body.addWidget(self._build_recognition_card(), stretch=38)

        self._follow.status_message.connect(self._on_follow_status)
        self._wire_signals()

    # ---------- entry / exit ----------

    def on_enter(self) -> None:
        worker = self._service.vision
        # Refcount-aware start: if the Drive screen / Perception
        # screen are already keeping the camera live, this just bumps
        # the refcount; the worker keeps running. If we're the first
        # holder, the camera comes up.
        worker.acquire()
        self._holds_camera = True
        self._refresh_follow_combo()
        # Sync toggles that default ON so the worker actually starts
        # detectors without requiring an extra click.
        if self._face_toggle is not None and self._face_toggle._btn.isChecked():  # noqa: SLF001
            worker.set_face_enabled(True)
        if self._object_toggle is not None and self._object_toggle._btn.isChecked():  # noqa: SLF001
            worker.set_object_enabled(True)
        self._service.reset_face_greet_cooldown()
        # Also reflect the latest known status immediately so the pill
        # doesn't lag the first frame.
        self._apply_status(self._status_to_dict(worker.status()))

    def on_leave(self) -> None:
        self._follow.stop()
        # Drop our reference on the camera when the user navigates
        # away. Other screens (Drive, Perception) may still hold a
        # reference, in which case the worker keeps running so their
        # live previews don't go black. The detector toggles get
        # forced OFF either way - they're a Vision-screen UI
        # affordance, and leaving them on after the screen is hidden
        # would silently cost CPU even when no operator is watching
        # the detections.
        worker = self._service.vision
        if self._face_toggle is not None and self._face_toggle._btn.isChecked():  # noqa: SLF001
            self._face_toggle._btn.setChecked(False)  # noqa: SLF001 - emits toggled -> worker.set_face_enabled(False)
        if self._object_toggle is not None and self._object_toggle._btn.isChecked():  # noqa: SLF001
            self._object_toggle._btn.setChecked(False)  # noqa: SLF001
        if getattr(self, "_holds_camera", False):
            try:
                worker.release()
            finally:
                self._holds_camera = False

    # ---------- camera ----------

    def _build_camera_card(self) -> Card:
        card = Card(padding=10, spacing=6)

        header = QHBoxLayout()
        card.add_layout(header)
        header.addWidget(CardTitle("Camera"))
        header.addStretch(1)
        self._fps_pill = Pill("\u2014", Pill.KIND_NEUTRAL)
        header.addWidget(self._fps_pill)

        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        # Was 420 - too tall for the 600-px panel. 280 leaves room for
        # the title row and a stretch so the viewport still fills the
        # available vertical space when present, but doesn't dictate
        # an overflow on the 1024 x 600 native resolution.
        viewport.setMinimumHeight(280)
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
            "color: #c4c4c8; font-size: 64px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(glyph)
        msg = QLabel(
            "Plug in a USB camera to see the live feed here.",
            placeholder,
        )
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
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
        # Was padding=20. Trim to 12 to fit the 13-row rail in 530 px.
        card = Card(padding=12, spacing=6)

        card.add(SectionLabel("Recognition"))
        face = _ToggleRow("Face recognition", on=True)
        obj = _ToggleRow("Object detection", on=True)
        for w in (face, obj):
            card.add(w)
        self._face_toggle = face
        self._object_toggle = obj

        card.add(SectionLabel("Person follow"))
        follow_hint = MutedLabel(
            "Locks face size at start, then drives forward/back and turns "
            "to keep you centred. Stops when you come closer."
        )
        follow_hint.setWordWrap(True)
        card.add(follow_hint)
        self._follow_combo = QComboBox()
        self._follow_combo.setMinimumHeight(32)
        card.add(self._follow_combo)
        follow_row = QHBoxLayout()
        follow_row.setSpacing(6)
        card.add_layout(follow_row)
        self._follow_start_btn = QPushButton("Start follow")
        self._follow_start_btn.setObjectName("primaryButton")
        self._follow_start_btn.setCursor(Qt.PointingHandCursor)
        self._follow_start_btn.setMinimumHeight(34)
        self._follow_start_btn.clicked.connect(self._on_follow_start)
        follow_row.addWidget(self._follow_start_btn, stretch=1)
        self._follow_stop_btn = QPushButton("Stop follow")
        self._follow_stop_btn.setObjectName("secondaryButton")
        self._follow_stop_btn.setCursor(Qt.PointingHandCursor)
        self._follow_stop_btn.setMinimumHeight(34)
        self._follow_stop_btn.setEnabled(False)
        self._follow_stop_btn.clicked.connect(self._on_follow_stop)
        follow_row.addWidget(self._follow_stop_btn, stretch=1)
        self._follow_pill = Pill("Follow: off", Pill.KIND_NEUTRAL)
        card.add(self._follow_pill)

        # Object confidence floor (0..100%). Anything YOLO scores
        # below this threshold is dropped before it ever reaches the
        # detector list / bbox overlay. Default 80% per the operator
        # preference -- "tight" detections only.
        conf_row = QHBoxLayout()
        conf_row.setSpacing(8)
        conf_row.setContentsMargins(0, 4, 0, 4)
        card.add_layout(conf_row)
        conf_row.addWidget(MutedLabel("Object confidence"))
        initial_pct = int(round(self._service.vision.get_object_confidence() * 100))
        self._obj_conf_slider = QSlider(Qt.Horizontal)
        self._obj_conf_slider.setRange(50, 99)
        self._obj_conf_slider.setValue(max(50, min(99, initial_pct)))
        self._obj_conf_slider.valueChanged.connect(self._on_object_confidence)
        conf_row.addWidget(self._obj_conf_slider, stretch=1)
        self._obj_conf_pill = Pill(f"{initial_pct}%", Pill.KIND_NEUTRAL)
        conf_row.addWidget(self._obj_conf_pill)

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

        # All three action buttons on a single row to save vertical
        # space on the 1024 x 600 panel. Each gets equal stretch so
        # they share the rail width without one ballooning past the
        # others. Shorter labels keep the row from wrapping.
        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        card.add_layout(button_row)

        train = QPushButton("Train face")
        train.setObjectName("primaryButton")
        train.setCursor(Qt.PointingHandCursor)
        train.setMinimumHeight(34)
        train.setMaximumHeight(34)
        train.clicked.connect(self._on_train)
        button_row.addWidget(train, stretch=1)

        snap = QPushButton("Snapshot")
        snap.setObjectName("secondaryButton")
        snap.setCursor(Qt.PointingHandCursor)
        snap.setMinimumHeight(34)
        snap.setMaximumHeight(34)
        snap.clicked.connect(self._on_snapshot)
        button_row.addWidget(snap, stretch=1)

        play = QPushButton("Speak")
        play.setObjectName("primaryButton")
        play.setCursor(Qt.PointingHandCursor)
        play.setMinimumHeight(34)
        play.setMaximumHeight(34)
        play.setToolTip(
            "Speak the names of the objects currently in view. "
            "Turn on 'Object detection' first."
        )
        play.setEnabled(False)
        play.clicked.connect(self._on_play_objects)
        button_row.addWidget(play, stretch=1)
        self._play_objects_btn = play

        return card

    # ---------- worker wiring ----------

    def _wire_signals(self) -> None:
        worker = self._service.vision
        worker.frame_ready.connect(self._on_frame)
        worker.detections_changed.connect(self._on_detections_changed)
        worker.fps_changed.connect(self._on_fps)
        worker.status_changed.connect(self._apply_status)
        worker.face_enable_failed.connect(self._on_face_enable_failed)
        worker.object_enable_failed.connect(self._on_object_enable_failed)
        worker.enrollment_finished.connect(
            lambda _payload: self._refresh_follow_combo()
        )

        if self._face_toggle is not None:
            self._face_toggle.toggled.connect(worker.set_face_enabled)
        if self._object_toggle is not None:
            self._object_toggle.toggled.connect(worker.set_object_enabled)

        # Surface "Play Objects" failures (no audio player, gTTS down,
        # offline, ...) as a single warning dialog instead of a silent
        # console log.
        self._announcer.error.connect(self._on_announcer_error)

    def _on_detections_changed(self, detections: List[Detection]) -> None:
        if self._follow.is_active():
            self._follow.ingest_detections(detections)
        if not self.isVisible():
            return
        self._render_detections(detections)

    def _on_face_enable_failed(self, reason: str) -> None:
        from PyQt5.QtWidgets import QMessageBox

        if self._face_toggle is not None and self._face_toggle._btn.isChecked():  # noqa: SLF001
            # Block the toggled signal while we revert so we don't
            # round-trip another set_face_enabled(False) command.
            blocker = self._face_toggle._btn.blockSignals(True)  # noqa: SLF001
            try:
                self._face_toggle._btn.setChecked(False)  # noqa: SLF001
                self._face_toggle._btn.setText("OFF")  # noqa: SLF001
            finally:
                self._face_toggle._btn.blockSignals(blocker)  # noqa: SLF001
        QMessageBox.warning(
            self,
            "Face detection unavailable",
            "Couldn't start face detection.\n\n"
            f"{reason}\n\n"
            "Check the terminal log for the full traceback. The most "
            "common cause is OpenCV being too old (need >= 4.5.4) or "
            "the YuNet ONNX failing to download."
        )

    def _on_object_enable_failed(self, reason: str) -> None:
        from PyQt5.QtWidgets import QMessageBox

        if self._object_toggle is not None and self._object_toggle._btn.isChecked():  # noqa: SLF001
            blocker = self._object_toggle._btn.blockSignals(True)  # noqa: SLF001
            try:
                self._object_toggle._btn.setChecked(False)  # noqa: SLF001
                self._object_toggle._btn.setText("OFF")  # noqa: SLF001
            finally:
                self._object_toggle._btn.blockSignals(blocker)  # noqa: SLF001
        QMessageBox.warning(
            self,
            "Object detection unavailable",
            "Couldn't start object detection.\n\n"
            f"{reason}\n\n"
            "On Jetson Nano this is usually one of:\n"
            "  - 'ultralytics' not installed for this Python\n"
            "      python3 -m pip install --user ultralytics\n"
            "  - PyTorch CUDA wheel missing for your JetPack\n"
            "  - The first TensorRT FP16 export ran out of RAM "
            "(add 4 GB swap, see README)\n\n"
            "Tip: launch the app from a terminal to see the full "
            "Python traceback in stderr."
        )

    def _on_frame(self, image: QImage) -> None:
        if not self.isVisible():
            return
        try:
            w, h = self._service.vision.capture_dimensions()
            self._follow.set_frame_size(w, h)
        except Exception:
            pass
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
            Qt.FastTransformation,
        )
        self._viewport_label.setPixmap(pix)

    def _on_fps(self, fps: float) -> None:
        if not self.isVisible():
            return
        self._fps_pill.setText(f"{fps:0.1f} fps")

    def _apply_status(self, status: dict) -> None:
        camera_open = bool(status.get("camera_open", False))
        message = str(status.get("message", "") or "")
        self._connected = camera_open

        # Categorise the message so the pill can surface detector
        # errors / loading announces *even when* the camera itself is
        # healthy. The previous implementation always painted
        # "USB camera connected" once camera_open was True, which
        # silently swallowed messages like
        # "Object: ImportError: No module named 'ultralytics'".
        msg_lower = message.lower()
        is_detector_error = (
            message.startswith("Object:")
            or message.startswith("Face:")
            or "opencv" in msg_lower
            or "ultralytics" in msg_lower
        )
        is_loading = "loading" in msg_lower

        if camera_open and is_detector_error:
            self._cam_pill.setText("Detector unavailable")
            self._cam_pill.set_kind(Pill.KIND_WARN)
            self._cam_pill.setToolTip(message)
        elif camera_open and is_loading:
            self._cam_pill.setText(message)
            self._cam_pill.set_kind(Pill.KIND_NEUTRAL)
            self._cam_pill.setToolTip("")
        elif camera_open:
            self._cam_pill.setText("USB camera connected")
            self._cam_pill.set_kind(Pill.KIND_OK)
            self._cam_pill.setToolTip("")
        elif is_detector_error:
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
        # Stash object labels for "Play Objects" before any early
        # return -- we want the button to update even if the panel
        # isn't ready yet (e.g. during teardown).
        self._latest_object_labels = [
            det.label for det in detections if det.kind == KIND_OBJECT
        ]
        if self._play_objects_btn is not None:
            self._play_objects_btn.setEnabled(bool(self._latest_object_labels))

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
            # For recognised faces we show the match strength (cosine
            # similarity from SFace), not the YuNet detection
            # confidence -- that's what the operator actually cares
            # about when verifying recognition.
            if det.kind == KIND_FACE and det.identity and det.identity_score is not None:
                pct = int(round(det.identity_score * 100))
            else:
                pct = int(round(det.confidence * 100))
            row = _DetectionRow(det.label, det.kind, pct)
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

    def _on_object_confidence(self, pct: int) -> None:
        # Slider lives in 50..99% so the user can't accidentally drag
        # it down to "anything goes" or to 100% (which YOLO never
        # reports for any real-world detection).
        pct = max(50, min(99, int(pct)))
        if self._obj_conf_pill is not None:
            self._obj_conf_pill.setText(f"{pct}%")
        self._service.vision.set_object_confidence(pct / 100.0)

    def _on_train(self) -> None:
        from PyQt5.QtWidgets import QMessageBox

        worker = self._service.vision
        status = worker.status()
        if not status.camera_open:
            QMessageBox.warning(
                self,
                "Camera not ready",
                "Connect a USB camera before training a new face.",
            )
            return
        # Toggle face detection on if it isn't already, so the operator
        # doesn't have to remember to flip it before training. Running
        # the enrollment with face detection off would just time out.
        if self._face_toggle is not None and not self._face_toggle._btn.isChecked():  # noqa: SLF001
            self._face_toggle._btn.setChecked(True)  # noqa: SLF001 - emits toggled -> worker

        dialog = FaceEnrollDialog(worker, parent=self)
        dialog.exec_()

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

    def _on_play_objects(self) -> None:
        from PyQt5.QtWidgets import QMessageBox

        # Take a copy in case `detections_changed` fires mid-call and
        # mutates the list under our feet.
        labels = list(self._latest_object_labels)
        if not labels:
            obj_on = (
                self._object_toggle is not None
                and self._object_toggle._btn.isChecked()  # noqa: SLF001
            )
            if not obj_on:
                QMessageBox.information(
                    self,
                    "Play Objects",
                    "Turn on 'Object detection' first, then point the "
                    "camera at something to play.",
                )
                return
            # Detection is on but the frame is empty - speak it so the
            # operator gets feedback either way.
            self._announcer.announce_empty()
            return
        self._announcer.announce(labels)

    def _on_announcer_error(self, reason: str) -> None:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Play Objects", reason)

    def _refresh_follow_combo(self) -> None:
        if self._follow_combo is None:
            return
        self._follow_combo.blockSignals(True)
        self._follow_combo.clear()
        self._follow_combo.addItem("Largest face (any)", "")
        try:
            for n in sorted(self._service.vision.list_faces()):
                self._follow_combo.addItem(n, n)
        except Exception:
            pass
        self._follow_combo.blockSignals(False)

    def _on_follow_start(self) -> None:
        from PyQt5.QtWidgets import QMessageBox

        worker = self._service.vision
        st = worker.status()
        if not st.camera_open:
            QMessageBox.warning(
                self,
                "Follow",
                "Camera is not ready. Wait for the feed or plug in a USB camera.",
            )
            return
        auto = self._service.autonomy
        if auto.is_enabled():
            r = QMessageBox.question(
                self,
                "Follow",
                "Autonomy is on. Stop it and start person follow?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return
            auto.set_enabled(False)
        if self._face_toggle is not None and not self._face_toggle._btn.isChecked():  # noqa: SLF001
            self._face_toggle._btn.setChecked(True)  # noqa: SLF001
        if self._object_toggle is not None and not self._object_toggle._btn.isChecked():  # noqa: SLF001
            self._object_toggle._btn.setChecked(True)  # noqa: SLF001
        target = self._follow_combo.currentData()
        if not self._follow.start(target):
            return
        if self._follow_start_btn is not None:
            self._follow_start_btn.setEnabled(False)
        if self._follow_stop_btn is not None:
            self._follow_stop_btn.setEnabled(True)

    def _on_follow_stop(self) -> None:
        self._follow.stop()

    def _on_follow_status(self, msg: str) -> None:
        if self._follow_pill is None:
            return
        short = msg if len(msg) <= 80 else (msg[:77] + "...")
        self._follow_pill.setText(short)
        low = msg.lower()
        if "follow: off" in low:
            self._follow_pill.set_kind(Pill.KIND_NEUTRAL)
            if self._follow_start_btn is not None:
                self._follow_start_btn.setEnabled(True)
            if self._follow_stop_btn is not None:
                self._follow_stop_btn.setEnabled(False)
        elif "lost target" in low or "brake on" in low:
            self._follow_pill.set_kind(Pill.KIND_WARN)
            if self._follow_start_btn is not None:
                self._follow_start_btn.setEnabled(True)
            if self._follow_stop_btn is not None:
                self._follow_stop_btn.setEnabled(False)
        elif "lost" in low or "brake" in low:
            self._follow_pill.set_kind(Pill.KIND_WARN)
        else:
            self._follow_pill.set_kind(Pill.KIND_OK)

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
