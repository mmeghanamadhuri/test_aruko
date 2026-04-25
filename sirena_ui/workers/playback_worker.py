"""Background QThread that plays a named action via the smooth playback path."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from nina.services.audio_player import AudioPlayer
from sirena_ui.workers.nina_service import NinaService


class PlaybackWorker(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        service: NinaService,
        action_name: str,
        smooth: bool = True,
        sub_hz: float = 50.0,
        max_speed: int = 1023,
        speed: float = 0.5,
        audio_path: Optional[Path] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._action_name = action_name
        self._smooth = smooth
        self._sub_hz = sub_hz
        self._max_speed = max_speed
        self._speed = speed
        self._audio_path = audio_path
        self._audio_player = AudioPlayer()

    def run(self) -> None:
        try:
            with self._service.bus_lock:
                if self._audio_path is not None:
                    self._audio_player.play(self._audio_path)
                self._service.action_runner.run_named_action(
                    self._action_name,
                    smooth=self._smooth,
                    sub_hz=self._sub_hz,
                    max_speed=self._max_speed,
                    speed=self._speed,
                )
            self.finished_ok.emit(self._action_name)
        except Exception as exc:  # pragma: no cover - reported back to UI
            self.failed.emit(str(exc))
