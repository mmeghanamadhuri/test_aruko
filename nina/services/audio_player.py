"""
Lightweight audio playback used by Nina to speak greetings while
performing actions.

The class delegates to whichever ALSA-compatible CLI player is on the
Jetson (`aplay` for WAV, `mpg123` for MP3, `ffplay` as a final
fallback). No Python audio dependencies are required, so this works on
a fresh JetPack image without extra pip installs.

Install hint on the Jetson:
    sudo apt install -y alsa-utils mpg123
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import List, Optional


class AudioPlayer:
    def __init__(self) -> None:
        self._aplay = shutil.which("aplay")
        self._mpg123 = shutil.which("mpg123")
        self._ffplay = shutil.which("ffplay")
        self._procs: List[subprocess.Popen] = []
        self._lock = threading.Lock()

    @property
    def is_supported(self) -> bool:
        return any((self._aplay, self._mpg123, self._ffplay))

    def can_play(self, audio_path: Path) -> bool:
        """True if this path's format can be played (file may not exist yet)."""
        return self._command_for(Path(audio_path)) is not None

    def play(self, audio_path: Path) -> Optional[subprocess.Popen]:
        """Start playback in the background. Returns the spawned process or None."""
        if audio_path is None:
            return None
        path = Path(audio_path)
        if not path.exists():
            print(f"[audio] file not found: {path}")
            return None
        cmd = self._command_for(path)
        if cmd is None:
            print(
                "[audio] no player available; "
                "install one with: sudo apt install -y alsa-utils mpg123"
            )
            return None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"[audio] failed to play {path}: {exc}")
            return None
        with self._lock:
            self._procs = [p for p in self._procs if p.poll() is None]
            self._procs.append(proc)
        return proc

    def stop_all(self) -> None:
        with self._lock:
            for proc in self._procs:
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            self._procs.clear()

    def _command_for(self, path: Path) -> Optional[List[str]]:
        ext = path.suffix.lower()
        if ext == ".wav" and self._aplay:
            return [self._aplay, "-q", str(path)]
        if ext in (".mp3",) and self._mpg123:
            return [self._mpg123, "-q", str(path)]
        if self._ffplay:
            return [
                self._ffplay,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                str(path),
            ]
        if ext == ".wav" and self._ffplay:
            return [self._ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        return None
