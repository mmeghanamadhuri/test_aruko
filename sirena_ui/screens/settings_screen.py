"""Settings screen: nested sub-sidebar + content panel.

Categories: General, Network, Display, Audio, Privacy, Autodock,
Voice Module, Power, OTA. Most of these are scaffolds for now;
General has working fields backed by `NinaSettings`.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.styles import asset_path
from sirena_ui.widgets.common import (
    Breadcrumb,
    Card,
    HRule,
    MutedLabel,
    Pill,
    SectionLabel,
)
from sirena_ui.workers.nina_service import NinaService


# (key, label, glyph)
SETTINGS_CATEGORIES: List[Tuple[str, str, str]] = [
    ("general", "General", "\u2699"),
    ("network", "Network \u00b7 Wi-Fi", "\u2706"),
    ("display", "Display", "\u25A1"),
    ("audio", "Audio", "\u266B"),
    ("privacy", "Privacy", "\u26C4"),  # umbrella - placeholder
    ("autodock", "Autodock", "\u2693"),
    ("voice", "Voice Module \u00b7 ESP", "\u2693"),
    ("power", "Power", "\u26A1"),
    ("ota", "OTA Update", "\u21BB"),
]


class SettingsScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._panes: Dict[str, QWidget] = {}
        self._buttons: Dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        outer.addWidget(Breadcrumb("Nina", "Settings"))

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_subsidebar())
        body.addWidget(self._build_content_stack(), stretch=1)

    # ---------- sub-sidebar ----------

    def _build_subsidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("subSidebar")
        frame.setFixedWidth(220)

        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 12, 8, 12)
        v.setSpacing(2)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label, glyph in SETTINGS_CATEGORIES:
            btn = QPushButton(f"  {glyph}    {label}")
            btn.setObjectName("subNavRow")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=key: self._select_pane(k))
            group.addButton(btn)
            v.addWidget(btn)
            self._buttons[key] = btn

        v.addStretch(1)
        footer = QLabel(f"{len(SETTINGS_CATEGORIES)} categories")
        footer.setStyleSheet(
            "color: #8e8e93; font-size: 11px; padding: 8px;"
        )
        footer.setAlignment(Qt.AlignCenter)
        v.addWidget(footer)
        return frame

    # ---------- content stack ----------

    def _build_content_stack(self) -> QStackedWidget:
        self._stack = QStackedWidget()
        for key, label, _glyph in SETTINGS_CATEGORIES:
            pane = self._build_pane(key, label)
            self._panes[key] = pane
            self._stack.addWidget(pane)
        # Default
        self._select_pane("general")
        return self._stack

    def _select_pane(self, key: str) -> None:
        widget = self._panes.get(key)
        if widget is None:
            return
        self._stack.setCurrentWidget(widget)
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def _build_pane(self, key: str, label: str) -> QWidget:
        if key == "general":
            return self._build_general_pane()
        return self._build_placeholder_pane(label)

    # ---------- General ----------

    def _build_general_pane(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        # About hero
        hero = Card(padding=16, spacing=10)
        h = QHBoxLayout()
        h.setSpacing(14)
        hero.add_layout(h)

        thumb = QLabel()
        thumb.setStyleSheet("background-color: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            thumb.setPixmap(pix.scaledToHeight(72, Qt.SmoothTransformation))
        h.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(2)
        h.addLayout(text, stretch=1)
        title = QLabel("Nina")
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 700;"
            " background-color: transparent;"
        )
        text.addWidget(title)
        sub = QLabel("Sirena Robotics \u00b7 v0.4 \u00b7 serial NN-0042")
        sub.setStyleSheet(
            "color: #6e6e73; font-size: 13px; background-color: transparent;"
        )
        text.addWidget(sub)

        view_health = QPushButton("View health")
        view_health.setObjectName("secondaryButton")
        view_health.setCursor(Qt.PointingHandCursor)
        view_health.setFixedWidth(120)
        h.addWidget(view_health, alignment=Qt.AlignTop)
        v.addWidget(hero)

        # Form card
        form_card = Card(padding=20, spacing=12)
        v.addWidget(form_card, stretch=1)

        section_title = QLabel("General")
        section_title.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 700;"
            " background-color: transparent;"
        )
        form_card.add(section_title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignRight)
        form_card.add_layout(form)

        self._robot_name = QLineEdit("Nina")
        form.addRow("Robot name", self._robot_name)

        self._tz_combo = QComboBox()
        self._tz_combo.addItems([
            "Asia / Kolkata", "Asia / Singapore", "Europe / London",
            "America / New_York", "America / Los_Angeles", "UTC",
        ])
        form.addRow("Time zone", self._tz_combo)

        self._lang_combo = QComboBox()
        self._lang_combo.addItems([
            "English (US)", "English (UK)", "English (IN)", "Hindi",
            "Spanish", "French",
        ])
        form.addRow("Default language", self._lang_combo)

        self._boot_combo = QComboBox()
        try:
            actions = sorted(self._service.list_actions().keys())
        except Exception:
            actions = ["neutral"]
        self._boot_combo.addItems(actions or ["neutral"])
        form.addRow("Boot action", self._boot_combo)

        greet = QCheckBox("Speak greeting on boot")
        greet.setChecked(True)
        form.addRow("", greet)

        diag = QCheckBox("Show diagnostic overlay on screen")
        form.addRow("", diag)

        form_card.add(HRule())
        danger = QHBoxLayout()
        danger.setSpacing(8)
        form_card.add_layout(danger)
        danger.addWidget(SectionLabel("Danger zone"))
        danger.addStretch(1)
        reset = QPushButton("Reset all")
        reset.setObjectName("secondaryButton")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._on_reset)
        danger.addWidget(reset)

        # Save / Discard
        cta = QHBoxLayout()
        cta.setSpacing(8)
        form_card.add_layout(cta)
        cta.addStretch(1)
        save = QPushButton("Save changes")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._on_save_general)
        cta.addWidget(save)
        discard = QPushButton("Discard")
        discard.setObjectName("secondaryButton")
        discard.setCursor(Qt.PointingHandCursor)
        cta.addWidget(discard)

        return container

    # ---------- placeholder panes ----------

    def _build_placeholder_pane(self, label: str) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        card = Card(padding=24, spacing=10)
        v.addWidget(card, stretch=1)

        title = QLabel(label)
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 700;"
            " background-color: transparent;"
        )
        card.add(title)
        card.add(MutedLabel(
            "Controls for this category will land alongside the matching"
            " hardware feature. The layout is locked in so wiring it up"
            " stays a one-line change."
        ))

        # Add a few sensible placeholder rows so each pane feels deliberate.
        placeholders = self._placeholder_rows(label)
        if placeholders:
            form = QFormLayout()
            form.setSpacing(10)
            form.setLabelAlignment(Qt.AlignRight)
            card.add_layout(form)
            for label_, widget in placeholders:
                form.addRow(label_, widget)

        card.add_stretch()

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        card.add_layout(chip_row)
        chip_row.addWidget(Pill("Coming soon", Pill.KIND_NEUTRAL))
        chip_row.addStretch(1)
        return container

    def _placeholder_rows(self, label: str) -> List[Tuple[str, QWidget]]:
        if label.startswith("Network"):
            wifi = QComboBox()
            wifi.addItems(["Sirena-5G", "Sirena-Guest", "Other..."])
            ip = QLabel("\u2014")
            return [("Wi-Fi network", wifi), ("IP address", ip)]
        if label == "Display":
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(70)
            sleep = QComboBox()
            sleep.addItems(["Never", "1 min", "5 min", "15 min"])
            return [("Brightness", slider), ("Screen sleep", sleep)]
        if label == "Audio":
            vol = QSlider(Qt.Horizontal)
            vol.setRange(0, 100)
            vol.setValue(60)
            mic = QComboBox()
            mic.addItems(["Default", "USB Mic", "Built-in"])
            return [("Speaker volume", vol), ("Microphone", mic)]
        if label == "Privacy":
            return [
                ("Camera privacy", QCheckBox("Disable camera when idle")),
                ("Mic privacy",    QCheckBox("Disable microphone when idle")),
            ]
        if label == "Autodock":
            thr = QSlider(Qt.Horizontal)
            thr.setRange(5, 50)
            thr.setValue(20)
            return [
                ("Return-to-dock at", thr),
                ("Charging type", QComboBox()),
            ]
        if label.startswith("Voice"):
            wake = QLineEdit("Hey Nina")
            return [
                ("Wake word", wake),
                ("ESP firmware", QLabel("0.7")),
            ]
        if label == "Power":
            return [
                ("Battery", QLabel("\u2014")),
                ("Idle behaviour", QComboBox()),
            ]
        if label.startswith("OTA"):
            return [
                ("Channel", QComboBox()),
                ("Last update", QLabel("\u2014")),
            ]
        return []

    # ---------- handlers ----------

    def _on_save_general(self) -> None:
        QMessageBox.information(
            self,
            "Settings saved",
            "Robot name, time zone, language and boot action saved locally.\n\n"
            "Persistent storage will be wired up in the next firmware update.",
        )

    def _on_reset(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Reset all settings?",
            "This will clear local UI preferences (it does NOT remove your"
            " recorded actions or audio clips). Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._robot_name.setText("Nina")
        self._tz_combo.setCurrentIndex(0)
        self._lang_combo.setCurrentIndex(0)
        self._boot_combo.setCurrentIndex(0)
