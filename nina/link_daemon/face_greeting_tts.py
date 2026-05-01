"""Qt-free \"Hello, <name>\" playback after face enrollment (matches FaceGreeter).

Caches MP3 under ``nina/data/greetings/`` like ``sirena_ui.workers.face_greeter``.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer

log = logging.getLogger("nina.link_daemon.face_greeting_tts")

_GREET_DIR = Path(__file__).resolve().parent.parent / "data" / "greetings"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "anon"


def queue_hello_greeting(name: str) -> None:
    """gTTS + play ``Hello, {name}`` on a background thread (best-effort)."""
    raw = (name or "").strip()
    if not raw:
        return

    def run() -> None:
        try:
            _GREET_DIR.mkdir(parents=True, exist_ok=True)
            stem = _safe_filename(raw)
            out_path = _GREET_DIR / f"{stem}.mp3"
            text = f"Hello, {raw}"
            if not out_path.exists() or out_path.stat().st_size == 0:
                try:
                    AudioGenerator.generate(text, out_path)
                except AudioGeneratorError as exc:
                    log.warning("enrollment greeting TTS failed: %s", exc)
                    return
            player = AudioPlayer()
            if not player.is_supported:
                log.warning("enrollment greeting: no mpg123/aplay for playback")
                return
            player.play(out_path)
        except Exception:
            log.exception("enrollment greeting")

    threading.Thread(target=run, daemon=True, name="face-greeting").start()
