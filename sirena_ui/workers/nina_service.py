"""
Thin facade around the Nina hardware controllers used by the UI.

The UI creates exactly one `NinaService`, lazily initializes the
Dynamixel bus on first use, and shares it across the playback and
record workers. All bus access is serialized via `bus_lock` so a
playback worker and a record worker can never race on the serial port.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from nina.config.settings import NinaSettings, load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.services.audio_generator import AudioGenerator
from nina.services.audio_player import AudioPlayer


DEFAULT_MOTOR_IDS: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


class NinaService:
    def __init__(self, settings: Optional[NinaSettings] = None) -> None:
        if settings is None:
            repo_root = Path(__file__).resolve().parents[2]
            settings = load_settings(repo_root)
        self.settings = settings
        self.dxl = DynamixelManager(
            serial_port=settings.serial_port,
            baudrate=settings.baudrate,
            expected_motor_ids=DEFAULT_MOTOR_IDS,
        )
        self.action_runner = ActionRunner(
            manifest_path=settings.manifest_path,
            actions_dir=settings.actions_dir,
            dxl=self.dxl,
        )
        self.bus_lock = threading.RLock()
        self._bus_ready = False
        self._motor_count = len(DEFAULT_MOTOR_IDS)

    @property
    def expected_motor_count(self) -> int:
        return self._motor_count

    def ensure_bus(self) -> Dict[str, object]:
        """Initialize the bus once, run a non-fatal health check, enable torque."""
        with self.bus_lock:
            if not self._bus_ready:
                self.dxl.initialize_bus()
                self._bus_ready = True
            health = self.dxl.run_health_check()
            self.dxl.set_torque_all(True)
            return {
                "connected": health.connected,
                "detected": health.detected_motors,
                "expected": health.expected_motors,
                "detail": health.detail,
            }

    def shutdown(self) -> None:
        with self.bus_lock:
            try:
                self.dxl.close()
            finally:
                self._bus_ready = False

    def list_actions(self) -> Dict[str, str]:
        return self.action_runner.list_actions()

    def action_path(self, name: str) -> Path:
        return self.settings.actions_dir / self.list_actions()[name]

    def action_audio_path(self, name: str) -> Optional[Path]:
        """
        Resolve the audio file to play alongside an action, if any.

        Lookup order:
          1. Explicit `audio` field on the manifest entry.
          2. Convention: `nina/actions/audio/<name>.{wav,mp3}`.
        """
        rel = self.action_runner.get_action_audio(name)
        if rel:
            candidate = self.settings.actions_dir / rel
            if candidate.exists():
                return candidate
        for ext in (".wav", ".mp3"):
            candidate = self.settings.actions_dir / "audio" / f"{name}{ext}"
            if candidate.exists():
                return candidate
        return None

    def action_audio_offset(self, name: str) -> float:
        """Per-action delay (seconds) before the audio clip is fired."""
        return self.action_runner.get_action_audio_offset(name)

    # ---------- audio authoring (used by the GUI audio editor) ----------

    @staticmethod
    def audio_generator_available() -> Optional[str]:
        """Return None if gTTS is importable, else an error message."""
        return AudioGenerator.is_available()

    def get_action_audio_info(self, name: str) -> Dict[str, Any]:
        """Bundle current audio state for the editor dialog."""
        rel = self.action_runner.get_action_audio(name)
        path = self.action_audio_path(name)
        return {
            "audio_rel": rel,
            "audio_path": path,
            "audio_offset": self.action_audio_offset(name),
        }

    def generate_action_audio(
        self,
        name: str,
        text: str,
        *,
        lang: str = "en",
        tld: str = "co.in",
        offset: float = 0.0,
    ) -> Path:
        """
        Render an MP3 for `name` with gTTS, save to
        `nina/actions/audio/<name>.mp3`, and update the manifest entry
        (audio + audio_offset) in one shot.
        """
        audio_dir = self.settings.actions_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        rel = f"audio/{name}.mp3"
        out_path = self.settings.actions_dir / rel
        AudioGenerator.generate(text, out_path, lang=lang, tld=tld)
        self.action_runner.set_action_audio(name, rel, audio_offset=offset)
        return out_path

    def set_action_audio_offset(self, name: str, offset: float) -> None:
        """Update only the audio_offset for an action that already has audio."""
        rel = self.action_runner.get_action_audio(name)
        if not rel:
            raise ValueError(
                f"Action '{name}' has no audio clip; generate one first."
            )
        self.action_runner.set_action_audio(name, rel, audio_offset=offset)

    def clear_action_audio(self, name: str) -> None:
        """Remove the audio mapping (and offset) from an action."""
        self.action_runner.set_action_audio(name, None)

    def preview_audio(self, audio_path: Path) -> None:
        """Play an audio file once (used by the editor 'Preview' button)."""
        AudioPlayer().play(audio_path)
