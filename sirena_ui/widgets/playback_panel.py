"""Right-side panel of the Nina screen, Playback tab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.workers.nina_service import NinaService


class _ActionRow(QFrame):
    play_clicked = pyqtSignal(str)
    audio_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(
        self,
        name: str,
        meta: str,
        audio_meta: str,
        *,
        deletable: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._name = name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title = QLabel(name)
        self._title.setStyleSheet("font-weight: 700; font-size: 16px;")
        text_col.addWidget(self._title)
        self._meta = QLabel(meta)
        self._meta.setStyleSheet("color: #6e6e73; font-size: 12px;")
        text_col.addWidget(self._meta)
        self._audio_meta = QLabel(audio_meta)
        self._audio_meta.setStyleSheet("color: #6e6e73; font-size: 11px;")
        text_col.addWidget(self._audio_meta)
        layout.addLayout(text_col, stretch=1)

        self._audio = QPushButton("Audio")
        self._audio.setObjectName("secondaryButton")
        self._audio.setCursor(Qt.PointingHandCursor)
        self._audio.setToolTip("Generate, tune, or remove the audio clip for this action.")
        self._audio.clicked.connect(lambda: self.audio_clicked.emit(self._name))
        layout.addWidget(self._audio)

        self._delete = QPushButton("Delete")
        self._delete.setObjectName("dangerButton")
        self._delete.setCursor(Qt.PointingHandCursor)
        self._delete.clicked.connect(lambda: self.delete_clicked.emit(self._name))
        if not deletable:
            self._delete.setEnabled(False)
            self._delete.setToolTip(
                "Protected action - cannot be deleted from the UI."
            )
        else:
            self._delete.setToolTip(
                "Remove this action from the manifest and delete its "
                "recording file."
            )
        layout.addWidget(self._delete)

        self._play = QPushButton("\u25B6")  # right-pointing triangle
        self._play.setObjectName("playButton")
        self._play.setCursor(Qt.PointingHandCursor)
        self._play.clicked.connect(lambda: self.play_clicked.emit(self._name))
        layout.addWidget(self._play)

    def set_enabled(self, enabled: bool) -> None:
        self._play.setEnabled(enabled)
        self._audio.setEnabled(enabled)
        # Don't override the protected state set in __init__; only enable
        # if the row was deletable to begin with.
        if self._delete.toolTip().startswith("Protected"):
            self._delete.setEnabled(False)
        else:
            self._delete.setEnabled(enabled)


class PlaybackPanel(QWidget):
    play_requested = pyqtSignal(str)
    audio_edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._rows: list[_ActionRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        title = QLabel("Playback Actions")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, stretch=1)

        container = QWidget()
        scroll.setWidget(container)
        self._list_layout = QVBoxLayout(container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        refresh = QPushButton("Refresh from manifest")
        refresh.setFlat(True)
        refresh.setStyleSheet("color: #6e6e73; text-decoration: underline;")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.refresh)
        footer.addWidget(refresh)
        outer.addLayout(footer)

        self.refresh()

    def refresh(self) -> None:
        self._clear_rows()
        try:
            actions = self._service.list_actions()
        except Exception:
            actions = {}
        protected = self._protected_action_names()
        for name, rel_path in sorted(actions.items()):
            meta = self._frame_meta(self._service.settings.actions_dir / rel_path)
            audio_meta = self._audio_meta(name)
            row = _ActionRow(
                name, meta, audio_meta, deletable=name not in protected,
            )
            row.play_clicked.connect(self.play_requested.emit)
            row.audio_clicked.connect(self.audio_edit_requested.emit)
            row.delete_clicked.connect(self.delete_requested.emit)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append(row)
        if not actions:
            empty = QLabel("No actions registered yet. Record one on the Record tab.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color: #6e6e73; padding: 20px;")
            self._list_layout.insertWidget(self._list_layout.count() - 1, empty)

    def _protected_action_names(self) -> set:
        """Names that the UI should never let the user delete.

        Today that's just the configured neutral pose - removing it
        would break the startup boot sequence.
        """
        names: set = set()
        try:
            neutral = getattr(self._service.settings, "neutral_action_name", None)
            if isinstance(neutral, str) and neutral:
                names.add(neutral)
        except Exception:
            pass
        return names

    def set_buttons_enabled(self, enabled: bool) -> None:
        for row in self._rows:
            row.set_enabled(enabled)

    def _clear_rows(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for i in reversed(range(self._list_layout.count() - 1)):
            item = self._list_layout.takeAt(i)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _frame_meta(path: Path) -> str:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            frames = data.get("frames", [])
            count = len(frames)
            if not frames:
                return "0 frames"
            total = sum(float(f.get("duration", 0.0)) + float(f.get("delay", 0.0)) for f in frames)
            return f"{total:.1f}s \u2022 {count} frames"
        except Exception:
            return "unknown"

    def _audio_meta(self, name: str) -> str:
        try:
            info = self._service.get_action_audio_info(name)
        except Exception:
            return ""
        rel = info.get("audio_rel")
        path = info.get("audio_path")
        offset = float(info.get("audio_offset") or 0.0)
        if not rel:
            return "Audio: none"
        suffix = f" \u2022 +{offset:.2f}s" if offset > 0 else ""
        missing = "" if path else " (missing)"
        return f"Audio: {Path(rel).name}{missing}{suffix}"
