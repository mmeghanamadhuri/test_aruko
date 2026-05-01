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
* Keyboard - W/A/S/D drive forward / left / back / right while held,
  Space stops, Esc fires the EMERGENCY STOP. Auto-repeat events are
  ignored so a held key looks like one press + one release to the
  motor controller. WASD bubbles up through the focused widget on
  PyQt5, so it works regardless of whether the slider, a button, or
  the screen body has focus.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
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
from sirena_ui.widgets.dpad import DPad
from sirena_ui.workers.nina_service import NinaService


# Keyboard map for held-while-pressed driving. Arrow keys are
# intentionally NOT included because QSlider intercepts them when
# the speed slider has focus.
_KEY_TO_DIRECTION = {
    Qt.Key_W: "forward",
    Qt.Key_S: "back",
    Qt.Key_A: "left",
    Qt.Key_D: "right",
}


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

        # The camera viewport is a soft grey panel until the USB camera
        # service is wired up. Once it is, this label gets replaced with
        # a QLabel-fed QPixmap stream.
        #
        # Min height was 360 px - too tall for the 600-tall panel after
        # accounting for chrome (44 + 26), screen margins (20), HUD row
        # (~70), top pill row (~30), and card padding (~30). 200 leaves
        # the HUD and top row visible with ~520 of content height.
        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(200)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setAlignment(Qt.AlignCenter)
        glyph = QLabel("\u25C9")
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 64px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        v.addWidget(glyph)
        msg = QLabel("USB camera not connected")
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        v.addWidget(msg)
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
        # Tight stack designed for the 1024 x 600 panel: title row +
        # autonomy + D-pad + speed + brake/reverse + ESTOP all need to
        # fit in ~470 px of vertical space inside the card.
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

        # Speed row - inline label + - slider + + pill, no SECTION label
        # above it (the "-" / "+" / "%" cluster is self-explanatory and
        # the section header was eating 16 px of vertical space).
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        card.add_layout(speed_row)

        speed_lbl = QLabel("Speed")
        speed_lbl.setStyleSheet(
            "color: #6e6e73; font-size: 12px; background-color: transparent;"
        )
        speed_row.addWidget(speed_lbl)

        minus = QPushButton("\u2212")
        minus.setObjectName("secondaryButton")
        minus.setCursor(Qt.PointingHandCursor)
        minus.setFixedSize(36, 36)
        minus.setFocusPolicy(Qt.NoFocus)
        minus.clicked.connect(lambda: self._drive.set_speed(self._drive.state()["speed_pct"] - 5))
        speed_row.addWidget(minus)

        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(0, 100)
        self._speed_slider.setValue(15)
        self._speed_slider.valueChanged.connect(self._drive.set_speed)
        speed_row.addWidget(self._speed_slider, stretch=1)

        plus = QPushButton("+")
        plus.setObjectName("secondaryButton")
        plus.setCursor(Qt.PointingHandCursor)
        plus.setFixedSize(36, 36)
        plus.setFocusPolicy(Qt.NoFocus)
        plus.clicked.connect(lambda: self._drive.set_speed(self._drive.state()["speed_pct"] + 5))
        speed_row.addWidget(plus)

        self._speed_pill = Pill("15%", Pill.KIND_ERROR)
        speed_row.addWidget(self._speed_pill)

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
        # Return focus to the screen so a follow-up Esc / Space still
        # reaches our key handlers instead of the EMERGENCY STOP button.
        self.setFocus()

    def _on_autonomy_toggle(self, on: bool) -> None:
        try:
            self._autonomy.set_enabled(on)
        except Exception as exc:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Autonomous mode failed",
                f"Could not toggle autonomy: {exc}",
            )

    def _on_autonomy_enabled(self, on: bool) -> None:
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
        self._dpad.set_enabled(not on)
        self._brake_btn.setEnabled(not on)
        self._reverse_btn.setEnabled(not on)
        self._speed_slider.setEnabled(not on)
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
        # Grab focus so WASD/Space/Esc reach our key handlers without
        # the user having to click into the screen body first.
        self.setFocus()

    def keyPressEvent(self, event) -> None:
        # Filter X11 auto-repeat: a held key would otherwise look like
        # press / release / press / release at ~30 Hz and thrash the
        # worker queue with duplicate drive commands.
        if event.isAutoRepeat():
            return
        if self._autonomy.is_enabled():
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
        self._speed_pill.setText(f"{state['speed_pct']}%")
        # Autonomy lock takes priority over the brake-lock for D-pad
        # enablement: while autonomy is on, the D-pad stays disabled
        # regardless of the manual brake state.
        if not self._autonomy.is_enabled():
            self._dpad.set_enabled(not state["brake"])

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
