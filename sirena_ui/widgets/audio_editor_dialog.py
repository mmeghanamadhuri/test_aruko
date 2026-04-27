"""Modal dialog for generating, tuning, and removing per-action audio."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
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

from sirena_ui.workers.audio_gen_worker import AudioGenWorker
from sirena_ui.workers.nina_service import NinaService


# (display label, lang code, tld) — first entry is the default. Users
# can edit the manifest by hand for exotic combos not listed here.
VOICE_PRESETS = [
    ("US English (default)", "en", "com"),
    ("UK English", "en", "co.uk"),
    ("Australian English", "en", "com.au"),
    ("Indian English", "en", "co.in"),
    ("Hindi", "hi", "co.in"),
    ("Spanish (Spain)", "es", "es"),
    ("French (France)", "fr", "fr"),
]


class AudioEditorDialog(QDialog):
    def __init__(
        self,
        service: NinaService,
        action_name: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._action_name = action_name
        self._worker: Optional[AudioGenWorker] = None

        self.setWindowTitle(f"Audio for '{action_name}'")
        self.setModal(True)
        self.setMinimumWidth(460)

        info = service.get_action_audio_info(action_name)
        self._existing_path: Optional[Path] = info["audio_path"]
        existing_offset: float = info["audio_offset"]
        existing_rel: Optional[str] = info["audio_rel"]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        header = QLabel(
            f"<b>{action_name}</b><br>"
            "<span style='color:#6e6e73;font-size:12px;'>"
            "Generate spoken audio with Google Text-to-Speech and save it "
            "into the manifest."
            "</span>"
        )
        header.setTextFormat(Qt.RichText)
        header.setWordWrap(True)
        outer.addWidget(header)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)
        outer.addLayout(form)

        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("e.g. Namaste, welcome")
        self._text_edit.setText(action_name.replace("_", " ").title())
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
        self._offset_spin.setValue(float(existing_offset))
        self._offset_spin.setToolTip(
            "Seconds to wait after motion starts before the audio fires."
        )
        form.addRow("Audio offset:", self._offset_spin)

        self._status_label = QLabel(self._compose_status_text(existing_rel, existing_offset))
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color:#6e6e73; font-size:12px;")
        outer.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        self._preview_btn = QPushButton("Preview existing")
        self._preview_btn.setEnabled(self._existing_path is not None)
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.clicked.connect(self._on_preview)
        button_row.addWidget(self._preview_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setEnabled(self._existing_path is not None)
        self._remove_btn.setCursor(Qt.PointingHandCursor)
        self._remove_btn.clicked.connect(self._on_remove)
        button_row.addWidget(self._remove_btn)

        self._save_offset_btn = QPushButton("Save offset only")
        self._save_offset_btn.setEnabled(self._existing_path is not None)
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

        self._close_btn = QPushButton("Close")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.reject)
        button_row.addWidget(self._close_btn)

        outer.addLayout(button_row)

        # Keep the button clickable even when gTTS is missing (common on
        # a fresh Jetson Nano). The click path surfaces the install hint
        # via QMessageBox; we also show it in the status label and
        # tooltip so it's visible at a glance.
        gtts_err = self._service.audio_generator_available()
        if gtts_err:
            self._generate_btn.setToolTip(gtts_err)
            self._status_label.setText(
                "gTTS not installed - click 'Generate && Save' for install "
                "instructions, or run `pip install --user gTTS` on the "
                "Jetson now. You can still edit the offset for existing clips."
            )

    # ---------- handlers ----------

    def _on_preview(self) -> None:
        if self._existing_path is None:
            return
        try:
            self._service.preview_audio(self._existing_path)
        except Exception as exc:
            QMessageBox.warning(self, "Preview failed", str(exc))

    def _on_remove(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Remove audio?",
            f"Remove the audio clip and offset from '{self._action_name}'?\n"
            "(The MP3 file on disk will be left in place.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self._service.clear_action_audio(self._action_name)
        except Exception as exc:
            QMessageBox.warning(self, "Could not remove audio", str(exc))
            return
        self.accept()

    def _on_save_offset(self) -> None:
        offset = float(self._offset_spin.value())
        try:
            self._service.set_action_audio_offset(self._action_name, offset)
        except Exception as exc:
            QMessageBox.warning(self, "Could not save offset", str(exc))
            return
        self.accept()

    def _on_generate(self) -> None:
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
        self._status_label.setText("Generating with gTTS (needs internet) ...")
        self._worker = AudioGenWorker(
            self._service, self._action_name, text, lang, tld, offset, parent=self,
        )
        self._worker.finished_ok.connect(self._on_generate_done)
        self._worker.failed.connect(self._on_generate_failed)
        self._worker.start()

    def _on_generate_done(self, _name: str, path_str: str) -> None:
        self._set_busy(False)
        QMessageBox.information(
            self,
            "Audio saved",
            f"Saved {Path(path_str).name} and updated the manifest.",
        )
        self.accept()

    def _on_generate_failed(self, _name: str, message: str) -> None:
        self._set_busy(False)
        self._status_label.setText(f"Generation failed: {message}")
        QMessageBox.warning(self, "Audio generation failed", message)

    # ---------- helpers ----------

    def _set_busy(self, busy: bool) -> None:
        for w in (
            self._text_edit,
            self._voice_combo,
            self._offset_spin,
            self._preview_btn,
            self._remove_btn,
            self._save_offset_btn,
            self._generate_btn,
            self._close_btn,
        ):
            w.setEnabled(not busy)
        if busy:
            self.setCursor(Qt.WaitCursor)
        else:
            self.unsetCursor()

    @staticmethod
    def _compose_status_text(audio_rel: Optional[str], offset: float) -> str:
        if not audio_rel:
            return "No audio clip registered for this action."
        if offset > 0:
            return f"Current: {audio_rel}  -  offset {offset:.2f}s"
        return f"Current: {audio_rel}  -  no offset"
