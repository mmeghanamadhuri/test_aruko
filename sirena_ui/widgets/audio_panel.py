"""
In-app audio authoring panel (the non-modal version of `AudioEditorDialog`).

Used inside the Actions screen as the "Audio" sub-tab. The same
`NinaService` API is used so behaviour exactly matches the dialog
flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.audio_editor_dialog import VOICE_PRESETS
from sirena_ui.widgets.common import Card, CardTitle, MutedLabel
from sirena_ui.workers.audio_gen_worker import AudioGenWorker
from sirena_ui.workers.nina_service import NinaService


class AudioPanel(QWidget):
    """Action picker + audio editor form."""

    audio_changed = pyqtSignal(str)  # emitted with the action name when audio is saved/removed

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._worker: Optional[AudioGenWorker] = None
        self._existing_path: Optional[Path] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = CardTitle("Action Audio")
        outer.addWidget(title)

        intro = MutedLabel(
            "Generate spoken audio for an action with Google Text-to-Speech."
            " The clip is saved into nina/actions/audio/ and the offset"
            " controls how long the robot moves before audio fires."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        card = Card(spacing=12)
        outer.addWidget(card, stretch=1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)
        card.add_layout(form)

        self._action_combo = QComboBox()
        self._action_combo.currentIndexChanged.connect(self._on_action_changed)
        form.addRow("Action:", self._action_combo)

        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("e.g. Namaste, welcome")
        form.addRow("Text to speak:", self._text_edit)

        self._voice_combo = QComboBox()
        for label, lang, tld in VOICE_PRESETS:
            self._voice_combo.addItem(label, (lang, tld))
        form.addRow("Voice:", self._voice_combo)

        self._offset_spin = QDoubleSpinBox()
        self._offset_spin.setRange(0.0, 60.0)
        self._offset_spin.setSingleStep(0.1)
        self._offset_spin.setDecimals(2)
        self._offset_spin.setSuffix(" s")
        self._offset_spin.setToolTip(
            "Seconds to wait after motion starts before the audio fires."
        )
        form.addRow("Audio offset:", self._offset_spin)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color:#6e6e73; font-size:12px;")
        card.add(self._status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        card.add_layout(button_row)

        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setObjectName("secondaryButton")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.clicked.connect(self._on_preview)
        button_row.addWidget(self._preview_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setObjectName("secondaryButton")
        self._remove_btn.setCursor(Qt.PointingHandCursor)
        self._remove_btn.clicked.connect(self._on_remove)
        button_row.addWidget(self._remove_btn)

        self._save_offset_btn = QPushButton("Save offset")
        self._save_offset_btn.setObjectName("secondaryButton")
        self._save_offset_btn.setCursor(Qt.PointingHandCursor)
        self._save_offset_btn.clicked.connect(self._on_save_offset)
        button_row.addWidget(self._save_offset_btn)

        button_row.addStretch(1)

        self._generate_btn = QPushButton("Generate && Save")
        self._generate_btn.setObjectName("primaryButton")
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.setDefault(True)
        self._generate_btn.clicked.connect(self._on_generate)
        button_row.addWidget(self._generate_btn)

        card.add_stretch()

        # If gTTS isn't importable yet (typical on a fresh Jetson Nano)
        # we keep the button clickable so the operator gets an actionable
        # error dialog with install instructions on click, instead of a
        # silent grey button. The same hint also lands in the tooltip and
        # status label so it's visible without clicking.
        self._gtts_err: Optional[str] = self._service.audio_generator_available()
        if self._gtts_err:
            self._generate_btn.setToolTip(self._gtts_err)

        self.refresh()

    # ---------- public API ----------

    def refresh(self) -> None:
        """Reload the action list from the manifest, preserving selection."""
        prev = self._action_combo.currentData()
        self._action_combo.blockSignals(True)
        self._action_combo.clear()
        try:
            actions = sorted(self._service.list_actions().keys())
        except Exception:
            actions = []
        for name in actions:
            self._action_combo.addItem(name, name)
        self._action_combo.blockSignals(False)
        if prev:
            idx = self._action_combo.findData(prev)
            if idx >= 0:
                self._action_combo.setCurrentIndex(idx)
        self._on_action_changed()

    def select_action(self, name: str) -> None:
        idx = self._action_combo.findData(name)
        if idx >= 0:
            self._action_combo.setCurrentIndex(idx)

    # ---------- handlers ----------

    def _current_name(self) -> str:
        data = self._action_combo.currentData()
        return str(data) if data else ""

    def _on_action_changed(self, *_args) -> None:
        name = self._current_name()
        if not name:
            self._existing_path = None
            self._set_status("Select an action to edit its audio.")
            self._update_buttons_for_state()
            return
        try:
            info = self._service.get_action_audio_info(name)
        except Exception as exc:
            self._existing_path = None
            self._set_status(f"Could not read audio info: {exc}")
            self._update_buttons_for_state()
            return
        self._existing_path = info.get("audio_path")
        rel: Optional[str] = info.get("audio_rel")
        offset = float(info.get("audio_offset") or 0.0)
        # Don't overwrite text the user is already editing for the same action
        if not self._text_edit.text():
            self._text_edit.setText(name.replace("_", " ").title())
        self._offset_spin.setValue(offset)
        if not rel:
            base = f"No audio clip for '{name}' yet. Generate one above."
        elif offset > 0:
            base = f"Current: {rel}  -  offset {offset:.2f}s"
        else:
            base = f"Current: {rel}  -  no offset"
        if self._gtts_err:
            self._set_status(
                f"{base}\nNote: gTTS not installed - run "
                "`pip install --user gTTS` on the Jetson to enable audio "
                "generation."
            )
        else:
            self._set_status(base)
        self._update_buttons_for_state()

    def _update_buttons_for_state(self) -> None:
        has_audio = self._existing_path is not None
        self._preview_btn.setEnabled(has_audio)
        self._remove_btn.setEnabled(has_audio)
        self._save_offset_btn.setEnabled(has_audio)

    def _on_preview(self) -> None:
        if not self._existing_path:
            return
        try:
            self._service.preview_audio(self._existing_path)
        except Exception as exc:
            QMessageBox.warning(self, "Preview failed", str(exc))

    def _on_remove(self) -> None:
        name = self._current_name()
        if not name:
            return
        confirm = QMessageBox.question(
            self,
            "Remove audio?",
            f"Remove the audio clip and offset from '{name}'?\n"
            "(The MP3 file on disk will be left in place.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self._service.clear_action_audio(name)
        except Exception as exc:
            QMessageBox.warning(self, "Could not remove audio", str(exc))
            return
        self.audio_changed.emit(name)
        self._on_action_changed()

    def _on_save_offset(self) -> None:
        name = self._current_name()
        if not name:
            return
        try:
            self._service.set_action_audio_offset(name, float(self._offset_spin.value()))
        except Exception as exc:
            QMessageBox.warning(self, "Could not save offset", str(exc))
            return
        self.audio_changed.emit(name)
        self._on_action_changed()

    def _on_generate(self) -> None:
        name = self._current_name()
        if not name:
            QMessageBox.information(
                self, "Pick an action",
                "Choose an action above before generating audio.",
            )
            return
        text = self._text_edit.text().strip()
        if not text:
            QMessageBox.information(
                self, "Text required",
                "Enter the words you want spoken before generating.",
            )
            return
        if self._existing_path is not None:
            confirm = QMessageBox.question(
                self,
                "Replace existing audio?",
                f"This will regenerate '{self._existing_path.name}' and "
                "overwrite the current clip. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm != QMessageBox.Yes:
                return
        lang, tld = self._voice_combo.currentData()
        offset = float(self._offset_spin.value())

        self._set_busy(True)
        self._set_status("Generating with gTTS (needs internet) ...")
        self._worker = AudioGenWorker(
            self._service, name, text, lang, tld, offset, parent=self,
        )
        self._worker.finished_ok.connect(self._on_generate_done)
        self._worker.failed.connect(self._on_generate_failed)
        self._worker.start()

    def _on_generate_done(self, name: str, _path_str: str) -> None:
        self._set_busy(False)
        self.audio_changed.emit(name)
        self._on_action_changed()

    def _on_generate_failed(self, _name: str, message: str) -> None:
        self._set_busy(False)
        self._set_status(f"Generation failed: {message}")
        QMessageBox.warning(self, "Audio generation failed", message)

    # ---------- helpers ----------

    def _set_busy(self, busy: bool) -> None:
        for w in (
            self._action_combo,
            self._text_edit,
            self._voice_combo,
            self._offset_spin,
            self._preview_btn,
            self._remove_btn,
            self._save_offset_btn,
            self._generate_btn,
        ):
            w.setEnabled(not busy)
        if not busy:
            self._update_buttons_for_state()
        if busy:
            self.setCursor(Qt.WaitCursor)
        else:
            self.unsetCursor()

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)


__all__ = ["AudioPanel"]
