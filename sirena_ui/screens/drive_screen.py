"""Drive screen: front camera placeholder + manual control cockpit.

The screen talks to the real BLDC drivers through
`NinaService.drive`, a Qt facade over `NavigationManager`. Hardware
init happens lazily on first navigation to this screen, and
gracefully falls back to "simulation" mode on dev hosts where
`Jetson.GPIO` is unavailable - the UI still reacts to button presses,
just without any PWM going out.

Two input modes are supported:

* On-screen D-pad - press-and-HOLD the mouse button on a direction
  (don't single-click; the BLDC needs a couple of seconds for the
  rotor to actually catch after the kick-start pulse).
* **90° left / 90° right** — single-click timed in-place pivots (~90°,
  tunable via ``NINA_DRIVE_TURN_90_SEC`` / ``NINA_DRIVE_TURN_90_PCT``).
* Keyboard - W/A/S/D drive forward / left / back / right while held,
  Space stops, Esc fires the EMERGENCY STOP. Auto-repeat events are
  ignored so a held key looks like one press + one release to the
  motor controller. WASD bubbles up through the focused widget on
  PyQt5, so it works when the screen body or a button has focus.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
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
from sirena_ui.widgets.dpad import DPad
from sirena_ui.workers.drive_controller import MAX_SPEED_PCT, MIN_SPEED_PCT
from sirena_ui.workers.nina_service import NinaService


# Keyboard map for held-while-pressed driving.
_KEY_TO_DIRECTION = {
    Qt.Key_W: "forward",
    Qt.Key_S: "back",
    Qt.Key_A: "left",
    Qt.Key_D: "right",
}

# Bench / field check: drive straight for NINA_STRAIGHT_TEST_MS (default 10 s), then stop.
# PWM: NINA_STRAIGHT_TEST_SPEED_PCT (8–100; use only where mechanically safe).
STRAIGHT_READY_POLL_MS = 50
STRAIGHT_READY_MAX_POLLS = 100


def _straight_test_speed_pct() -> int:
    try:
        raw = int(os.environ.get("NINA_STRAIGHT_TEST_SPEED_PCT", str(MAX_SPEED_PCT)))
    except ValueError:
        raw = MAX_SPEED_PCT
    return max(MIN_SPEED_PCT, min(100, raw))


def _straight_sequence_spec() -> List[Tuple[str, int]]:
    """Single forward segment: (\"fwd\", duration_ms)."""
    raw = (os.environ.get("NINA_STRAIGHT_TEST_MS") or "").strip()
    if not raw:
        raw = (os.environ.get("NINA_STRAIGHT_SEQ_FWD1_MS") or "").strip()
    if raw:
        try:
            ms = int(raw)
        except ValueError:
            ms = 10_000
    else:
        ms = 10_000
    ms = max(100, min(120_000, ms))
    return [("fwd", ms)]


class DriveScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._drive = service.drive
        self._autonomy = service.autonomy
        self._drive.state_changed.connect(self._render_state)
        self._autonomy.enabled_changed.connect(self._on_autonomy_enabled)

        # Accept keyboard focus so WASD/Space/Esc reach keyPressEvent
        # even when the user hasn't clicked into a child widget.
        self.setFocusPolicy(Qt.StrongFocus)
        self._kb_active_key: Optional[int] = None

        self._straight_test_timer = QTimer(self)
        self._straight_test_timer.setSingleShot(True)
        self._straight_test_timer.timeout.connect(self._on_straight_sequence_timer)
        self._straight_pending = False
        self._straight_sequence_spec: List[Tuple[str, int]] = []
        self._straight_seq_index: int = -1
        self._straight_seq_fwd_dir: str = "forward"

        # Live RGB feed wiring. The "Front camera" card on the left of
        # the Drive screen used to be a static placeholder; we now
        # subscribe to VisionWorker.frame_ready and stream pixmaps in.
        # The camera is acquired ONCE on first on_enter (via the
        # VisionWorker's refcount API) so navigating Drive -> Vision
        # -> Drive doesn't tear down the feed when the Vision screen's
        # on_leave releases its own reference.
        self._vision_acquired = False
        self._cam_feed_label: Optional[QLabel] = None
        self._cam_placeholder: Optional[QWidget] = None
        try:
            # frame_ready is connected only in on_enter so hidden screens
            # do not each duplicate 30 Hz QImage deliveries on the GUI thread.
            self._service.vision.status_changed.connect(self._on_camera_status)
        except Exception:
            # In headless / vision-disabled builds the worker may
            # still construct but never emit; not fatal for the Drive
            # screen, which can still drive without a camera feed.
            pass

        outer = QVBoxLayout(self)
        # Trim from 20 -> 10 on each side. At 1024 x 600 we cannot
        # afford 40 wasted px in either direction.
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Breadcrumb("Nina", "Drive"))
        top.addStretch(1)
        self._auto_pill = Pill("Autonomous: OFF", Pill.KIND_NEUTRAL)
        top.addWidget(self._auto_pill)
        self._conn_pill = Pill("BLDC not connected", Pill.KIND_NEUTRAL)
        top.addWidget(self._conn_pill)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)
        outer.addLayout(body, stretch=1)

        # Slightly more weight to the control card now (was 58/42) so
        # the D-pad isn't squeezed out at 1024 wide.
        body.addWidget(self._build_camera_card(), stretch=55)
        body.addWidget(self._build_control_card(), stretch=45)

        # Push initial state into the HUD / pills.
        self._render_state(self._drive.state())

    def _connect_vision_frame_preview(self) -> None:
        try:
            sig = self._service.vision.frame_ready
            try:
                sig.disconnect(self._on_camera_frame)
            except TypeError:
                pass
            sig.connect(self._on_camera_frame)
        except Exception:
            pass

    def _disconnect_vision_frame_preview(self) -> None:
        try:
            self._service.vision.frame_ready.disconnect(self._on_camera_frame)
        except TypeError:
            pass

    # ---------- camera card ----------

    def _build_camera_card(self) -> Card:
        card = Card(padding=10, spacing=6)

        header = QHBoxLayout()
        header.setSpacing(6)
        card.add_layout(header)
        header.addWidget(CardTitle("Front camera"))
        header.addStretch(1)
        self._cam_pill = Pill("Preview \u2014 camera not connected", Pill.KIND_NEUTRAL)
        header.addWidget(self._cam_pill)

        # The camera viewport now hosts the LIVE VisionWorker feed.
        # Layout pattern mirrors VisionScreen._build_camera_card so
        # the visual shape matches across screens: a placeholder
        # QWidget centered in the viewport (shown while no frame has
        # arrived) plus a hidden QLabel that gets pixmaps once
        # `frame_ready` fires.
        #
        # IMPORTANT: do NOT call setAlignment() on the viewport's
        # layout. With an alignment set, QBoxLayout hands children
        # their sizeHint instead of stretching them, and a QLabel's
        # sizeHint follows the pixmap. The pixmap is scaled to the
        # label size in _on_camera_frame, so an alignment-centered
        # label collapses to a "dot" on first paint.
        #
        # Min height: 200 px - we need to fit the HUD row underneath
        # in the 600-tall panel.
        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(200)
        viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        placeholder = QWidget(viewport)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ph_layout = QVBoxLayout(placeholder)
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(8)
        ph_layout.addStretch(1)
        glyph = QLabel("\u25C9", placeholder)
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 64px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(glyph)
        msg = QLabel("USB camera not connected", placeholder)
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(msg)
        ph_layout.addStretch(1)
        v.addWidget(placeholder, stretch=1)
        self._cam_placeholder = placeholder

        feed = QLabel(viewport)
        feed.setAlignment(Qt.AlignCenter)
        feed.setStyleSheet("background-color: transparent;")
        feed.setMinimumSize(320, 180)
        # Ignored size policy so the (potentially huge) pixmap can't
        # feed back into the layout and balloon or collapse the card.
        feed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        feed.hide()
        v.addWidget(feed, stretch=1)
        self._cam_feed_label = feed

        card.add(viewport, stretch=1)

        # HUD row beneath the viewport with the live drive state.
        hud = QHBoxLayout()
        hud.setSpacing(8)
        card.add_layout(hud)
        self._hud_speed = self._make_hud("Speed", "0%")
        self._hud_heading = self._make_hud("Heading", "0\u00b0")
        self._hud_distance = self._make_hud("Distance", "0.0 m")
        self._hud_battery = self._make_hud("Battery", "n/a")
        for w in (self._hud_speed, self._hud_heading, self._hud_distance, self._hud_battery):
            hud.addWidget(w, stretch=1)

        return card

    def _make_hud(self, label: str, value: str) -> Card:
        # Tight HUD tile - was padding=12, spacing=4. At 1024 x 600 we
        # need every px the camera viewport can borrow.
        box = Card(padding=8, spacing=2, subtle=True)
        box.add(SectionLabel(label))
        v = QLabel(value)
        v.setStyleSheet(
            "color: #1c1c1e; font-size: 16px; font-weight: 700;"
            " background-color: transparent;"
        )
        box.add(v)
        # Stash the value label on the card so we can update it later.
        box._value_label = v  # type: ignore[attr-defined]
        return box

    # ---------- control card ----------

    def _build_control_card(self) -> Card:
        # Tight stack for the 1024 x 600 panel: title row + autonomy +
        # D-pad + straight test + wheel polarity + brake/reverse + ESTOP.
        card = Card(padding=10, spacing=6)

        # Title + autonomy toggle on the same row so the toggle isn't
        # full-width (it stretched and looked oversized on the panel).
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        card.add_layout(title_row)
        title_row.addWidget(CardTitle("Manual"))
        title_row.addStretch(1)
        self._autonomy_btn = QPushButton("Auto: OFF")
        self._autonomy_btn.setObjectName("primaryButton")
        self._autonomy_btn.setCursor(Qt.PointingHandCursor)
        self._autonomy_btn.setCheckable(True)
        self._autonomy_btn.setFocusPolicy(Qt.NoFocus)
        self._autonomy_btn.setMinimumHeight(34)
        self._autonomy_btn.setMaximumHeight(34)
        self._autonomy_btn.toggled.connect(self._on_autonomy_toggle)
        title_row.addWidget(self._autonomy_btn)

        # Banner removed - the "Auto: OFF/ON" pill above is enough
        # surface area to communicate the mode at this resolution. We
        # keep the attribute set to None so handlers that reach for it
        # don't need to be conditional everywhere.
        self._auto_banner = None  # type: ignore[assignment]

        # D-pad sits in a horizontal row centered with stretches so it
        # doesn't pin to the left edge when the card is wider.
        dpad_row = QHBoxLayout()
        dpad_row.setContentsMargins(0, 0, 0, 0)
        dpad_row.addStretch(1)
        self._dpad = DPad()
        self._dpad.direction_pressed.connect(self._drive.drive)
        self._dpad.direction_released.connect(lambda _d: self._drive.stop())
        self._dpad.stop_clicked.connect(self._drive.stop)
        dpad_row.addWidget(self._dpad)
        dpad_row.addStretch(1)
        card.add_layout(dpad_row)

        straight_row = QHBoxLayout()
        straight_row.setContentsMargins(0, 0, 0, 0)
        straight_row.addStretch(1)
        self._straight_test_btn = QPushButton("Straight test (10 s)")
        self._straight_test_btn.setObjectName("secondaryButton")
        self._straight_test_btn.setCursor(Qt.PointingHandCursor)
        self._straight_test_btn.setFocusPolicy(Qt.NoFocus)
        self._straight_test_btn.setMinimumHeight(32)
        self._straight_test_btn.setToolTip(
            "Drives straight for NINA_STRAIGHT_TEST_MS (default 10 s; legacy: NINA_STRAIGHT_SEQ_FWD1_MS) "
            "at NINA_STRAIGHT_TEST_SPEED_PCT, then stops. Respects Reverse. "
            "Space cancels; brake, E-STOP, autonomy, or leaving Drive stops the run. "
            "Turn off autonomous mode and release the brake first."
        )
        self._straight_test_btn.clicked.connect(self._on_straight_test_clicked)
        straight_row.addWidget(self._straight_test_btn)
        straight_row.addStretch(1)
        card.add_layout(straight_row)

        turn_row = QHBoxLayout()
        turn_row.setContentsMargins(0, 0, 0, 0)
        turn_row.setSpacing(8)
        turn_row.addStretch(1)
        self._turn_90_left_btn = QPushButton("90° left")
        self._turn_90_left_btn.setObjectName("secondaryButton")
        self._turn_90_left_btn.setCursor(Qt.PointingHandCursor)
        self._turn_90_left_btn.setFocusPolicy(Qt.NoFocus)
        self._turn_90_left_btn.setMinimumHeight(32)
        self._turn_90_left_btn.setToolTip(
            "Timed in-place pivot ~90° counter-clockwise (robot frame). "
            "Duration: NINA_DRIVE_TURN_90_SEC or NINA_NAV_TURN_SEC; "
            "speed: NINA_DRIVE_TURN_90_PCT or manual cruise default."
        )
        self._turn_90_left_btn.clicked.connect(lambda: self._on_turn_90_clicked("left"))
        turn_row.addWidget(self._turn_90_left_btn)
        self._turn_90_right_btn = QPushButton("90° right")
        self._turn_90_right_btn.setObjectName("secondaryButton")
        self._turn_90_right_btn.setCursor(Qt.PointingHandCursor)
        self._turn_90_right_btn.setFocusPolicy(Qt.NoFocus)
        self._turn_90_right_btn.setMinimumHeight(32)
        self._turn_90_right_btn.setToolTip(
            "Timed in-place pivot ~90° clockwise (robot frame). "
            "Duration: NINA_DRIVE_TURN_90_SEC or NINA_NAV_TURN_SEC; "
            "speed: NINA_DRIVE_TURN_90_PCT or manual cruise default."
        )
        self._turn_90_right_btn.clicked.connect(lambda: self._on_turn_90_clicked("right"))
        turn_row.addWidget(self._turn_90_right_btn)
        turn_row.addStretch(1)
        card.add_layout(turn_row)

        # Wheel polarity calibration. The first time a Nina is built the
        # JYQDs are commonly soldered to the hub motors with one wheel
        # phase-wired backwards, so a "forward" command spins one wheel
        # forward and one backward. Toggling these flips that wheel's
        # polarity at the nav layer, and the choice is persisted to
        # ~/.config/sirena/drive_polarity.json so the next boot picks
        # it up. Replaces the older NINA_NAV_INVERT_LEFT / RIGHT env
        # vars (those still work as a boot-time fallback if no
        # persisted value exists yet).
        polarity_row = QHBoxLayout()
        polarity_row.setSpacing(6)
        card.add_layout(polarity_row)
        polarity_lbl = QLabel("Wheels")
        polarity_lbl.setStyleSheet(
            "color: #6e6e73; font-size: 12px; background-color: transparent;"
        )
        polarity_lbl.setToolTip(
            "Per-wheel polarity flip. Use these when 'Forward' makes "
            "the wheels spin in opposite directions."
        )
        polarity_row.addWidget(polarity_lbl)

        self._invert_left_btn = QPushButton("Flip L: OFF")
        self._invert_left_btn.setObjectName("togglePill")
        self._invert_left_btn.setCheckable(True)
        self._invert_left_btn.setFocusPolicy(Qt.NoFocus)
        self._invert_left_btn.setMinimumHeight(28)
        self._invert_left_btn.setMaximumHeight(28)
        self._invert_left_btn.setToolTip(
            "Flip the LEFT wheel's forward/backward polarity. Survives "
            "reboot. Saved to ~/.config/sirena/drive_polarity.json."
        )
        self._invert_left_btn.clicked.connect(self._on_invert_left_toggle)
        polarity_row.addWidget(self._invert_left_btn)

        self._invert_right_btn = QPushButton("Flip R: OFF")
        self._invert_right_btn.setObjectName("togglePill")
        self._invert_right_btn.setCheckable(True)
        self._invert_right_btn.setFocusPolicy(Qt.NoFocus)
        self._invert_right_btn.setMinimumHeight(28)
        self._invert_right_btn.setMaximumHeight(28)
        self._invert_right_btn.setToolTip(
            "Flip the RIGHT wheel's forward/backward polarity. Survives "
            "reboot. Saved to ~/.config/sirena/drive_polarity.json."
        )
        self._invert_right_btn.clicked.connect(self._on_invert_right_toggle)
        polarity_row.addWidget(self._invert_right_btn)
        polarity_row.addStretch(1)

        # Brake / Reverse on the SAME row as ESTOP so the bottom of the
        # card collapses from three rows into one.
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        card.add_layout(bottom_row)
        self._brake_btn = QPushButton("Brake: ON")
        self._brake_btn.setObjectName("togglePill")
        self._brake_btn.setCheckable(True)
        self._brake_btn.setChecked(True)
        self._brake_btn.setFocusPolicy(Qt.NoFocus)
        self._brake_btn.setMinimumHeight(34)
        self._brake_btn.setMaximumHeight(34)
        self._brake_btn.clicked.connect(self._on_brake_toggle)
        bottom_row.addWidget(self._brake_btn)

        self._reverse_btn = QPushButton("Reverse: OFF")
        self._reverse_btn.setObjectName("togglePill")
        self._reverse_btn.setCheckable(True)
        self._reverse_btn.setFocusPolicy(Qt.NoFocus)
        self._reverse_btn.setMinimumHeight(34)
        self._reverse_btn.setMaximumHeight(34)
        self._reverse_btn.clicked.connect(self._on_reverse_toggle)
        bottom_row.addWidget(self._reverse_btn)
        bottom_row.addStretch(1)

        # Big red panic button - shrunk from the default `stopButton`
        # styling (16/24 padding, 18 px font) which was ~50 px tall and
        # pushed the kb hint off-screen on the 10.1" panel.
        self._estop_btn = QPushButton("\u26A0  E-STOP")
        self._estop_btn.setObjectName("stopButton")
        self._estop_btn.setCursor(Qt.PointingHandCursor)
        self._estop_btn.setFocusPolicy(Qt.NoFocus)
        self._estop_btn.setMinimumHeight(34)
        self._estop_btn.setMaximumHeight(34)
        self._estop_btn.setStyleSheet(
            "QPushButton#stopButton {"
            "  padding: 4px 12px; font-size: 14px; border-radius: 17px;"
            "}"
        )
        self._estop_btn.clicked.connect(self._on_emergency_stop)
        bottom_row.addWidget(self._estop_btn)

        # Keyboard hint as a single small line right above the bottom.
        kb_hint = QLabel(
            "WASD drive \u00b7 Space stop \u00b7 Esc E-STOP"
        )
        kb_hint.setStyleSheet(
            "color: #8e8e93; font-size: 11px; background-color: transparent;"
        )
        kb_hint.setAlignment(Qt.AlignCenter)
        card.add(kb_hint)

        return card

    # ---------- handlers ----------

    def _on_brake_toggle(self, checked: bool) -> None:
        if checked and self._straight_test_timer.isActive():
            self._finish_straight_test()
        self._brake_btn.setText(f"Brake: {'ON' if checked else 'OFF'}")
        self._drive.set_brake(checked)
        self.setFocus()

    def _on_reverse_toggle(self, checked: bool) -> None:
        self._reverse_btn.setText(f"Reverse: {'ON' if checked else 'OFF'}")
        self._drive.set_reverse(checked)
        self.setFocus()

    def _on_invert_left_toggle(self, checked: bool) -> None:
        self._invert_left_btn.setText(f"Flip L: {'ON' if checked else 'OFF'}")
        self._drive.set_invert_left(checked)
        # Return focus to the screen so WASD keeps reaching us instead
        # of the button.
        self.setFocus()

    def _on_invert_right_toggle(self, checked: bool) -> None:
        self._invert_right_btn.setText(f"Flip R: {'ON' if checked else 'OFF'}")
        self._drive.set_invert_right(checked)
        self.setFocus()

    def _on_emergency_stop(self) -> None:
        self._drive.emergency_stop()
        # Sync the Brake toggle so the screen reflects the new state
        # immediately (the controller already engaged the brake on the
        # hardware; this just makes the UI agree).
        self._brake_btn.blockSignals(True)
        self._brake_btn.setChecked(True)
        self._brake_btn.setText("Brake: ON")
        self._brake_btn.blockSignals(False)
        self._restore_after_straight_test()
        # Return focus to the screen so a follow-up Esc / Space still
        # reaches our key handlers instead of the EMERGENCY STOP button.
        self.setFocus()

    def _on_straight_test_clicked(self) -> None:
        if self._straight_test_timer.isActive() or self._straight_pending:
            return
        if self._autonomy.is_enabled():
            QMessageBox.warning(
                self,
                "Autonomous mode",
                "Turn off autonomous mode before running a manual straight test.",
            )
            return
        if self._brake_btn.isChecked():
            QMessageBox.information(
                self,
                "Brake engaged",
                "Release the brake (Brake: OFF) before running a straight test.",
            )
            return
        self._drive.ensure_hardware()
        self._straight_test_btn.setEnabled(False)
        self._dpad.set_enabled(False)
        self._turn_90_left_btn.setEnabled(False)
        self._turn_90_right_btn.setEnabled(False)
        self._straight_pending = True
        self._straight_ready_polls = 0
        QTimer.singleShot(STRAIGHT_READY_POLL_MS, self._try_straight_when_ready)
        self.setFocus()

    def _try_straight_when_ready(self) -> None:
        """Start straight test only after BLDC init finishes (async worker)."""
        if not self._straight_pending:
            return
        self._straight_ready_polls += 1
        self._drive.ensure_hardware()
        if not self._drive.state().get("connected"):
            if self._straight_ready_polls >= STRAIGHT_READY_MAX_POLLS:
                self._straight_pending = False
                QMessageBox.warning(
                    self,
                    "Drive not ready",
                    "BLDC did not connect in time. Check the link / Pi bridge, "
                    "wait for the green connected pill, then try Straight again.",
                )
                self._restore_after_straight_test()
                return
            QTimer.singleShot(STRAIGHT_READY_POLL_MS, self._try_straight_when_ready)
            return
        self._straight_pending = False
        self._straight_seq_fwd_dir = (
            "back" if self._drive.state().get("reverse") else "forward"
        )
        self._straight_sequence_spec = _straight_sequence_spec()
        self._straight_seq_index = -1
        self._apply_straight_sequence_segment(0)
        self.setFocus()

    def _apply_straight_sequence_segment(self, index: int) -> None:
        spec = self._straight_sequence_spec
        n = len(spec)
        while index < n and spec[index][1] <= 0:
            index += 1
        if index >= n:
            self._finish_straight_test()
            return
        _, ms = spec[index]
        pct_fwd = _straight_test_speed_pct()
        d = self._straight_seq_fwd_dir
        self._drive.drive_wheels(d, pct_fwd, d, pct_fwd)
        self._straight_seq_index = index
        self._straight_test_timer.start(ms)

    def _on_straight_sequence_timer(self) -> None:
        self._straight_test_timer.stop()
        try:
            self._drive.stop(drain=True)
        except Exception:
            try:
                self._drive.stop()
            except Exception:
                pass
        next_i = self._straight_seq_index + 1
        if next_i >= len(self._straight_sequence_spec):
            self._restore_after_straight_test()
            self.setFocus()
            return
        self._apply_straight_sequence_segment(next_i)

    def _restore_after_straight_test(self) -> None:
        self._straight_test_timer.stop()
        self._straight_sequence_spec = []
        self._straight_seq_index = -1
        self._straight_pending = False
        if not self._autonomy.is_enabled():
            self._straight_test_btn.setEnabled(True)
            st = self._drive.state()
            self._dpad.set_enabled(not st["brake"])
            can_turn = not st["brake"]
            self._turn_90_left_btn.setEnabled(can_turn)
            self._turn_90_right_btn.setEnabled(can_turn)
        else:
            self._straight_test_btn.setEnabled(False)
            self._turn_90_left_btn.setEnabled(False)
            self._turn_90_right_btn.setEnabled(False)

    def _finish_straight_test(self) -> None:
        try:
            self._drive.stop(drain=True)
        except Exception:
            try:
                self._drive.stop()
            except Exception:
                pass
        self._restore_after_straight_test()
        self.setFocus()

    def _on_turn_90_clicked(self, which: str) -> None:
        if self._autonomy.is_enabled():
            QMessageBox.warning(
                self,
                "Autonomous mode",
                "Turn off autonomous mode before using manual 90° turns.",
            )
            return
        if self._brake_btn.isChecked():
            QMessageBox.information(
                self,
                "Brake engaged",
                "Release the brake (Brake: OFF) before running a 90° turn.",
            )
            return
        self._drive.ensure_hardware()
        self._drive.turn_90(which)
        self.setFocus()

    def _on_autonomy_toggle(self, on: bool) -> None:
        try:
            self._autonomy.set_enabled(on)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Autonomous mode failed",
                f"Could not toggle autonomy: {exc}",
            )

    def _on_autonomy_enabled(self, on: bool) -> None:
        if on and self._straight_test_timer.isActive():
            try:
                self._drive.stop(drain=True)
            except Exception:
                try:
                    self._drive.stop()
                except Exception:
                    pass
            self._restore_after_straight_test()
        self._autonomy_btn.blockSignals(True)
        self._autonomy_btn.setChecked(on)
        # Short label - we removed the explanatory banner below the
        # button when refitting for the 1024 x 600 panel, so the pill
        # in the title row carries the "ON / OFF" affordance alone.
        self._autonomy_btn.setText(f"Auto: {'ON' if on else 'OFF'}")
        self._autonomy_btn.blockSignals(False)

        self._auto_pill.setText(
            f"Autonomous: {'ON' if on else 'OFF'}"
        )
        self._auto_pill.set_kind(Pill.KIND_OK if on else Pill.KIND_NEUTRAL)

        # Disable the manual D-pad / brake / reverse while autonomy is
        # in charge so the operator can't fight it on the wheels.
        if on:
            self._dpad.set_enabled(False)
            self._brake_btn.setEnabled(False)
            self._reverse_btn.setEnabled(False)
            self._straight_test_btn.setEnabled(False)
            self._turn_90_left_btn.setEnabled(False)
            self._turn_90_right_btn.setEnabled(False)
        else:
            st = self._drive.state()
            manual = not st["brake"]
            self._dpad.set_enabled(manual)
            self._brake_btn.setEnabled(True)
            self._reverse_btn.setEnabled(True)
            self._straight_test_btn.setEnabled(True)
            self._turn_90_left_btn.setEnabled(manual)
            self._turn_90_right_btn.setEnabled(manual)
        # _auto_banner was removed in the 1024 x 600 refit; nothing to
        # update here. The title-row pill conveys the same state.

    def on_enter(self) -> None:
        """Lazily initialise the BLDC drivers the first time the user
        opens the Drive screen. Re-entry is cheap; the controller
        dedupes inside its worker."""
        self._drive.ensure_hardware()
        # Reflect the current autonomy state in case the user toggled
        # it from the Map screen.
        self._on_autonomy_enabled(self._autonomy.is_enabled())
        # Bring the live RGB feed up. We acquire ONCE for the lifetime
        # of the screen - the operator picked "always live" so the
        # cleanest contract is: as soon as Drive has been opened, the
        # camera is on; it goes off only on app shutdown
        # (NinaService.shutdown -> VisionWorker.shutdown). The
        # refcount means a Vision-tab on_leave doesn't tear down our
        # feed.
        if not self._vision_acquired:
            try:
                self._service.vision.acquire()
                self._vision_acquired = True
            except Exception:
                pass
        # Recognise enrolled faces on the front camera (greeting is wired
        # in NinaService). Vision on_leave disables detectors; turn face
        # back on here so Drive still says "Hello <name>".
        try:
            self._service.vision.set_face_enabled(True)
        except Exception:
            pass
        # Same policy as Vision: fresh face-recognition greetings when
        # this screen takes the live feed (Drive shares VisionWorker).
        self._service.reset_face_greet_cooldown()
        self._connect_vision_frame_preview()
        # Grab focus so WASD/Space/Esc reach our key handlers without
        # the user having to click into the screen body first.
        self.setFocus()

    def on_leave(self) -> None:
        self._disconnect_vision_frame_preview()
        if self._straight_test_timer.isActive():
            self._finish_straight_test()
        elif self._straight_pending:
            self._restore_after_straight_test()

    def _on_camera_frame(self, image: QImage) -> None:
        """Render an incoming RGB frame into the Front-camera card."""
        if not self.isVisible():
            return
        feed = self._cam_feed_label
        if feed is None:
            return
        if self._cam_placeholder is not None and self._cam_placeholder.isVisible():
            self._cam_placeholder.hide()
            feed.show()
        target = feed.size()
        if target.width() <= 0 or target.height() <= 0:
            feed.setPixmap(QPixmap.fromImage(image))
            return
        pix = QPixmap.fromImage(image).scaled(
            target,
            Qt.KeepAspectRatio,
            Qt.FastTransformation,
        )
        feed.setPixmap(pix)

    def _on_camera_status(self, status: dict) -> None:
        """Update the camera pill in the Front-camera card header.

        Mirrors VisionScreen's pill-state policy so the operator sees
        the same "USB camera connected / not connected / Detector
        unavailable" labels regardless of which screen they're on.
        """
        camera_open = bool(status.get("camera_open", False))
        message = str(status.get("message", "") or "")
        if camera_open:
            self._cam_pill.setText("Live")
            self._cam_pill.set_kind(Pill.KIND_OK)
            self._cam_pill.setToolTip("")
        else:
            label = message or "USB camera not connected"
            self._cam_pill.setText(label)
            self._cam_pill.set_kind(Pill.KIND_NEUTRAL)
            self._cam_pill.setToolTip("")
            # Drop back to the placeholder if the camera went away.
            if self._cam_feed_label is not None:
                self._cam_feed_label.clear()
                self._cam_feed_label.hide()
            if self._cam_placeholder is not None:
                self._cam_placeholder.show()

    def keyPressEvent(self, event) -> None:
        # Filter X11 auto-repeat: a held key would otherwise look like
        # press / release / press / release at ~30 Hz and thrash the
        # worker queue with duplicate drive commands.
        if event.isAutoRepeat():
            return
        if self._autonomy.is_enabled():
            super().keyPressEvent(event)
            return

        if self._straight_test_timer.isActive():
            key = event.key()
            if key == Qt.Key_Space:
                self._finish_straight_test()
                event.accept()
                return
            if key == Qt.Key_Escape:
                self._on_emergency_stop()
                event.accept()
                return
            if key in _KEY_TO_DIRECTION:
                event.accept()
                return
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in _KEY_TO_DIRECTION:
            # Only allow one direction key at a time. Pressing a second
            # while one is already held is ignored - swapping mid-drive
            # is rough on a BLDC and rough on the operator's nerves.
            if self._kb_active_key is not None and self._kb_active_key != key:
                event.accept()
                return
            self._kb_active_key = key
            self._drive.drive(_KEY_TO_DIRECTION[key])
            event.accept()
            return
        if key == Qt.Key_Space:
            self._drive.stop()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self._on_emergency_stop()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in _KEY_TO_DIRECTION and key == self._kb_active_key:
            self._kb_active_key = None
            self._drive.stop()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _render_state(self, state: dict) -> None:
        self._hud_speed._value_label.setText(f"{state['speed_pct']}%")
        self._hud_heading._value_label.setText(f"{state['heading_deg']}\u00b0")
        self._hud_distance._value_label.setText(f"{state['distance_m']:.1f} m")
        # Autonomy lock takes priority over the brake-lock for D-pad
        # enablement: while autonomy is on, the D-pad stays disabled
        # regardless of the manual brake state.
        if not self._autonomy.is_enabled():
            if self._straight_test_timer.isActive() or self._straight_seq_index >= 0:
                self._dpad.set_enabled(False)
                self._turn_90_left_btn.setEnabled(False)
                self._turn_90_right_btn.setEnabled(False)
            else:
                st = self._drive.state()
                can_manual = not st["brake"]
                self._dpad.set_enabled(can_manual)
                self._turn_90_left_btn.setEnabled(can_manual)
                self._turn_90_right_btn.setEnabled(can_manual)

        # Reflect persisted/runtime polarity in the toggle pills WITHOUT
        # firing their `clicked` signal back into the controller (which
        # would loop). blockSignals() is the cleanest way to do that
        # since we don't have access to the underlying button's setter
        # discrimination otherwise.
        for btn, key, label_prefix in (
            (self._invert_left_btn, "invert_left", "Flip L"),
            (self._invert_right_btn, "invert_right", "Flip R"),
        ):
            on = bool(state.get(key, False))
            if btn.isChecked() != on:
                btn.blockSignals(True)
                btn.setChecked(on)
                btn.blockSignals(False)
            btn.setText(f"{label_prefix}: {'ON' if on else 'OFF'}")

        message = state.get("driver_message", "")
        if state["connected"]:
            self._conn_pill.setText(message or "BLDC L+R connected")
            self._conn_pill.set_kind(Pill.KIND_OK)
        elif message and message.startswith("Simulation"):
            # GPIO backend missing - dev mode; show a warn pill so the
            # operator understands button presses don't move wheels.
            self._conn_pill.setText(message)
            self._conn_pill.set_kind(Pill.KIND_WARN)
        else:
            self._conn_pill.setText("BLDC not connected")
            self._conn_pill.set_kind(Pill.KIND_NEUTRAL)
