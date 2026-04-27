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
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

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
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_camera_card(), stretch=58)
        body.addWidget(self._build_control_card(), stretch=42)

        # Push initial state into the HUD / pills.
        self._render_state(self._drive.state())

    # ---------- camera card ----------

    def _build_camera_card(self) -> Card:
        card = Card(padding=16, spacing=10)

        header = QHBoxLayout()
        header.setSpacing(8)
        card.add_layout(header)
        header.addWidget(CardTitle("Front camera"))
        header.addStretch(1)
        self._cam_pill = Pill("Preview \u2014 camera not connected", Pill.KIND_NEUTRAL)
        header.addWidget(self._cam_pill)

        # The camera viewport is a soft grey panel until the USB camera
        # service is wired up. Once it is, this label gets replaced with
        # a QLabel-fed QPixmap stream.
        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(360)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setAlignment(Qt.AlignCenter)
        glyph = QLabel("\u25C9")
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 88px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        v.addWidget(glyph)
        msg = QLabel("Front-camera feed will appear here once the USB camera is connected.")
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 13px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        v.addWidget(msg)
        card.add(viewport, stretch=1)

        # HUD row beneath the viewport with the live drive state.
        hud = QHBoxLayout()
        hud.setSpacing(12)
        card.add_layout(hud)
        self._hud_speed = self._make_hud("Speed", "0%")
        self._hud_heading = self._make_hud("Heading", "0\u00b0")
        self._hud_distance = self._make_hud("Distance", "0.0 m")
        self._hud_battery = self._make_hud("Battery", "n/a")
        for w in (self._hud_speed, self._hud_heading, self._hud_distance, self._hud_battery):
            hud.addWidget(w, stretch=1)

        return card

    def _make_hud(self, label: str, value: str) -> Card:
        box = Card(padding=12, spacing=4, subtle=True)
        box.add(SectionLabel(label))
        v = QLabel(value)
        v.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 700;"
            " background-color: transparent;"
        )
        box.add(v)
        # Stash the value label on the card so we can update it later.
        box._value_label = v  # type: ignore[attr-defined]
        return box

    # ---------- control card ----------

    def _build_control_card(self) -> Card:
        card = Card(padding=20, spacing=14)
        card.add(CardTitle("Manual Control"))
        card.add(MutedLabel("Hold a direction to drive \u00b7 release to stop"))

        # Autonomous-mode toggle. Mirrors the same control on the Map
        # screen - both feed into `service.autonomy.set_enabled()`.
        self._autonomy_btn = QPushButton("Autonomous mode: OFF")
        self._autonomy_btn.setObjectName("primaryButton")
        self._autonomy_btn.setCursor(Qt.PointingHandCursor)
        self._autonomy_btn.setCheckable(True)
        self._autonomy_btn.setFocusPolicy(Qt.NoFocus)
        self._autonomy_btn.toggled.connect(self._on_autonomy_toggle)
        card.add(self._autonomy_btn)

        self._auto_banner = MutedLabel(
            "Manual D-pad below is active. Toggle Autonomous mode to "
            "let Nina drive herself using lidar + ultrasonic + IR + "
            "depth-camera obstacle avoidance."
        )
        self._auto_banner.setWordWrap(True)
        card.add(self._auto_banner)

        self._dpad = DPad()
        self._dpad.direction_pressed.connect(self._drive.drive)
        self._dpad.direction_released.connect(lambda _d: self._drive.stop())
        self._dpad.stop_clicked.connect(self._drive.stop)
        card.add(self._dpad)

        card.add(SectionLabel("Speed"))
        speed_row = QHBoxLayout()
        speed_row.setSpacing(8)
        card.add_layout(speed_row)

        minus = QPushButton("\u2212")
        minus.setObjectName("secondaryButton")
        minus.setCursor(Qt.PointingHandCursor)
        minus.setFixedWidth(40)
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
        plus.setFixedWidth(40)
        plus.setFocusPolicy(Qt.NoFocus)
        plus.clicked.connect(lambda: self._drive.set_speed(self._drive.state()["speed_pct"] + 5))
        speed_row.addWidget(plus)

        self._speed_pill = Pill("15%", Pill.KIND_ERROR)
        speed_row.addWidget(self._speed_pill)

        # Brake / Reverse toggle pills
        toggles = QHBoxLayout()
        toggles.setSpacing(10)
        card.add_layout(toggles)
        self._brake_btn = QPushButton("Brake: ON")
        self._brake_btn.setObjectName("togglePill")
        self._brake_btn.setCheckable(True)
        self._brake_btn.setChecked(True)
        self._brake_btn.setFocusPolicy(Qt.NoFocus)
        self._brake_btn.clicked.connect(self._on_brake_toggle)
        toggles.addWidget(self._brake_btn)

        self._reverse_btn = QPushButton("Reverse: OFF")
        self._reverse_btn.setObjectName("togglePill")
        self._reverse_btn.setCheckable(True)
        self._reverse_btn.setFocusPolicy(Qt.NoFocus)
        self._reverse_btn.clicked.connect(self._on_reverse_toggle)
        toggles.addWidget(self._reverse_btn)
        toggles.addStretch(1)

        # Big red panic button. Bypasses the brake toggle so the operator
        # can fire it without first releasing whatever direction is held.
        self._estop_btn = QPushButton("\u26A0  EMERGENCY STOP")
        self._estop_btn.setObjectName("stopButton")
        self._estop_btn.setCursor(Qt.PointingHandCursor)
        self._estop_btn.setFocusPolicy(Qt.NoFocus)
        self._estop_btn.clicked.connect(self._on_emergency_stop)
        card.add(self._estop_btn)

        # Keyboard hint - critical for non-touch displays where a single
        # mouse click is too short for the BLDC to spin up.
        kb_hint = MutedLabel(
            "Keyboard: W A S D = drive (held) \u00b7 "
            "Space = stop \u00b7 Esc = EMERGENCY STOP. "
            "On-screen D-pad: press-and-HOLD the mouse button."
        )
        kb_hint.setWordWrap(True)
        card.add(kb_hint)

        card.add_stretch()
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
        self._autonomy_btn.setText(
            f"Autonomous mode: {'ON' if on else 'OFF'}"
        )
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

        if on:
            self._auto_banner.setText(
                "Autonomous mode active \u2014 Nina is driving herself. "
                "Manual controls are disabled. Toggle off to take back "
                "control."
            )
        else:
            self._auto_banner.setText(
                "Manual D-pad below is active. Toggle Autonomous mode "
                "to let Nina drive herself using lidar + ultrasonic + "
                "IR + depth-camera obstacle avoidance."
            )

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
