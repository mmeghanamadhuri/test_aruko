"""QThread that runs gTTS off the UI thread."""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from sirena_ui.workers.nina_service import NinaService


class AudioGenWorker(QThread):
    finished_ok = pyqtSignal(str, str)  # (action_name, saved_audio_path)
    failed = pyqtSignal(str, str)        # (action_name, error_message)

    def __init__(
        self,
        service: NinaService,
        action_name: str,
        text: str,
        lang: str,
        tld: str,
        offset: float,
        *,
        slow: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._action_name = action_name
        self._text = text
        self._lang = lang
        self._tld = tld
        self._offset = max(0.0, float(offset))
        self._slow = slow

    def run(self) -> None:
        try:
            path = self._service.generate_action_audio(
                self._action_name,
                self._text,
                lang=self._lang,
                tld=self._tld,
                offset=self._offset,
                slow=self._slow,
            )
            self.finished_ok.emit(self._action_name, str(path))
        except Exception as exc:  # pragma: no cover - reported back to UI
            self.failed.emit(self._action_name, str(exc))
