"""Drive screen: front camera placeholder + manual control cockpit.

The BLDC firmware is being integrated separately; this screen ships
with a working UI and a `DriveStub` so the user can see exactly how
the experience will feel once the driver lands.
"""

from __future__ import annotations

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
from sirena_ui.workers.drive_stub import DriveStub
from sirena_ui.workers.nina_service import NinaService


class DriveScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._drive = DriveStub(self)
        self._drive.state_changed.connect(self._render_state)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Breadcrumb("Nina", "Drive"))
        top.addStretch(1)
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
        self._brake_btn.clicked.connect(self._on_brake_toggle)
        toggles.addWidget(self._brake_btn)

        self._reverse_btn = QPushButton("Reverse: OFF")
        self._reverse_btn.setObjectName("togglePill")
        self._reverse_btn.setCheckable(True)
        self._reverse_btn.clicked.connect(self._on_reverse_toggle)
        toggles.addWidget(self._reverse_btn)
        toggles.addStretch(1)

        card.add_stretch()
        return card

    # ---------- handlers ----------

    def _on_brake_toggle(self, checked: bool) -> None:
        self._brake_btn.setText(f"Brake: {'ON' if checked else 'OFF'}")
        self._drive.set_brake(checked)

    def _on_reverse_toggle(self, checked: bool) -> None:
        self._reverse_btn.setText(f"Reverse: {'ON' if checked else 'OFF'}")
        self._drive.set_reverse(checked)

    def _render_state(self, state: dict) -> None:
        self._hud_speed._value_label.setText(f"{state['speed_pct']}%")
        self._hud_heading._value_label.setText(f"{state['heading_deg']}\u00b0")
        self._hud_distance._value_label.setText(f"{state['distance_m']:.1f} m")
        self._speed_pill.setText(f"{state['speed_pct']}%")
        self._dpad.set_enabled(not state["brake"])
        if state["connected"]:
            self._conn_pill.setText("BLDC L+R connected")
            self._conn_pill.set_kind(Pill.KIND_OK)
        else:
            self._conn_pill.setText("BLDC not connected (preview)")
            self._conn_pill.set_kind(Pill.KIND_NEUTRAL)
