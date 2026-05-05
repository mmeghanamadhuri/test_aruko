"""
Lightweight audio playback used by Nina to speak greetings while
performing actions.

The class delegates to whichever ALSA-compatible CLI player is on the
Jetson (`aplay` for WAV, `mpg123` for MP3, `ffplay` as a final
fallback). No Python audio dependencies are required, so this works on
a fresh JetPack image without extra pip installs.

Before starting a clip, ``AudioPlayer.play`` can emit a short stretch of
digital silence via ``aplay`` (``NINA_AUDIO_PREROLL_MS``, default 1000 ms)
so amps/USB speakers settle and the first syllable is not clipped.

Install hint on the Jetson:
    sudo apt install -y alsa-utils mpg123
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import List, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _preroll_ms() -> int:
    try:
        return max(0, int(os.environ.get("NINA_AUDIO_PREROLL_MS", "1000")))
    except ValueError:
        return 1000


def _aplay_device_flag() -> Optional[str]:
    d = (os.environ.get("NINA_GREET_APLAY_DEVICE") or "").strip()
    return d or None


def _ensure_preroll_wav(ms: int, sample_rate: int = 44100) -> Optional[Path]:
    if ms <= 0:
        return None
    cache = _repo_root() / "nina" / "data" / ".cache"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    path = cache / f"preroll_silence_{ms}ms_{sample_rate}.wav"
    if path.exists() and path.stat().st_size > 0:
        return path
    nframes = max(1, int(sample_rate * (ms / 1000.0)))
    try:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * nframes)
    except OSError:
        return None
    return path


def play_silence_preroll_blocking() -> None:
    """Play a short silent WAV through ``aplay`` to wake the output path."""
    ms = _preroll_ms()
    if ms <= 0:
        return
    aplay = shutil.which("aplay")
    if not aplay:
        return
    wav = _ensure_preroll_wav(ms)
    if wav is None:
        return
    cmd: List[str] = [aplay, "-q"]
    dev = _aplay_device_flag()
    if dev:
        cmd.extend(["-D", dev])
    cmd.append(str(wav))
    try:
        subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(5.0, (ms / 1000.0) * 2 + 1.0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass


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

    def play(
        self, audio_path: Path, *, skip_preroll: bool = False
    ) -> Optional[subprocess.Popen]:
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
        if self._aplay and not skip_preroll:
            play_silence_preroll_blocking()
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
