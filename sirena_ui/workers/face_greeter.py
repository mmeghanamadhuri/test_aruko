"""
Speaks a greeting ("Hello <name>") when Nina recognises a familiar
face.

Design goals:

  * Greet at most once per person within a configurable cooldown
    window. Walking past Nina shouldn't trigger a barrage of "Hello
    hari, Hello hari, Hello hari".
  * Cache one MP3 per name on disk so the second time we see the
    same person we don't need internet (gTTS is a network call).
  * Synthesis runs on a background QThread so we never stall the
    Vision worker / GUI thread on the gTTS HTTP request.
  * Playback prefers cached MP3 via mpg123/ffplay; common Jetson images
    have only `aplay` — without mpg123, greetings fall back to `espeak`.

Public API:

    greeter = FaceGreeter()
    greeter.greet("hari")     # idempotent; no-op during cooldown

That's it. The class internalises everything else.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer


log = logging.getLogger("sirena_ui.face_greeter")


def _safe_filename(name: str) -> str:
    """Map an arbitrary display name to a safe-on-disk filename stem."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "anon"


class _GenerateClipWorker(QThread):
    """Synthesise one greeting MP3 in the background."""

    finished_with_path = pyqtSignal(str, object)  # name, Path | None

    def __init__(self, name: str, text: str, out_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._text = text
        self._out_path = out_path

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            AudioGenerator.generate(self._text, self._out_path)
            self.finished_with_path.emit(self._name, self._out_path)
        except AudioGeneratorError as exc:
            log.warning("Greeting TTS for '%s' failed: %s", self._name, exc)
            self.finished_with_path.emit(self._name, None)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Greeting TTS for '%s' crashed: %s", self._name, exc)
            self.finished_with_path.emit(self._name, None)


class FaceGreeter(QObject):
    """Per-person cooldown + cached gTTS playback."""

    spoken = pyqtSignal(str)  # name we just greeted

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        cooldown_sec: float = 30.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if cache_dir is None:
            repo_root = Path(__file__).resolve().parents[2]
            cache_dir = repo_root / "nina" / "data" / "greetings"
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cooldown = float(cooldown_sec)
        self._last_greeted: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._player = AudioPlayer()
        # Active synthesis workers, kept alive until each finishes so
        # QThread doesn't get garbage-collected mid-run.
        self._gen_workers: Dict[str, _GenerateClipWorker] = {}

    def _playback_available(self) -> bool:
        """MP3 via mpg123/ffplay, or espeak as a common Jetson fallback."""
        if self._player.can_play(Path("_greet_probe.mp3")):
            return True
        return bool(shutil.which("espeak-ng") or shutil.which("espeak"))

    @staticmethod
    def _try_espeak(text: str) -> bool:
        for exe_name in ("espeak-ng", "espeak"):
            exe = shutil.which(exe_name)
            if not exe:
                continue
            try:
                subprocess.Popen(
                    [exe, text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError as exc:
                log.debug("espeak not usable (%s): %s", exe, exc)
        return False

    def greet(self, name: str) -> None:
        """Speak "Hello <name>" if cooldown has elapsed for this name.

        Safe to call from any thread; playback is dispatched in the
        background via `subprocess.Popen`.
        """
        name = (name or "").strip()
        if not name:
            return
        if not self._playback_available():
            log.warning(
                "FaceGreeter: no MP3 player (mpg123/ffplay) or espeak; "
                "install e.g. sudo apt install -y mpg123"
            )
            return

        with self._lock:
            last = self._last_greeted.get(name, 0.0)
            now = time.time()
            if now - last < self._cooldown:
                return
            # Reserve the slot *now* so a burst of recognitions doesn't
            # queue up multiple TTS jobs while the first one is still
            # synthesising.
            self._last_greeted[name] = now

        clip = self._cached_clip_for(name)
        if clip is not None:
            self._play_clip(clip, name)
            return  # cooldown reserved above
        # First-time greeting: gTTS MP3 when possible, else espeak only.
        if AudioGenerator.is_available() is None:
            self._spawn_synthesis(name)
            return
        log.info("FaceGreeter: gTTS unavailable; using espeak for %s", name)
        text = f"Hello {name}"
        if self._try_espeak(text):
            self.spoken.emit(name)
        else:
            self.reset_cooldown(name)

    def greet_now(self, name: str) -> None:
        """Speak ``Hello <name>`` for operator-driven latches (e.g. person follow).

        Bypasses cooldown and does not reserve the slot before playback so we
        avoid the path where ``greet()`` records ``_last_greeted`` then returns
        without audio because a QThread synthesis worker is still running for
        the same name. First-time names run gTTS + play on a daemon thread so
        we never block the GUI.
        """
        name = (name or "").strip()
        if not name:
            return
        if not self._playback_available():
            log.warning(
                "FaceGreeter.greet_now: no MP3 player (mpg123/ffplay) or espeak"
            )
            return

        self.reset_cooldown(name)

        text = f"Hello {name}"
        clip = self._cached_clip_for(name)
        if clip is not None:
            if self._play_clip(clip, name):
                with self._lock:
                    self._last_greeted[name] = time.time()
            return

        if AudioGenerator.is_available() is None:
            cache_dir = self._cache_dir
            stem = _safe_filename(name)
            player = self._player
            greeter = self

            def run() -> None:
                try:
                    out_path = cache_dir / f"{stem}.mp3"
                    if not out_path.exists() or out_path.stat().st_size == 0:
                        try:
                            AudioGenerator.generate(text, out_path)
                        except AudioGeneratorError as exc:
                            log.warning(
                                "FaceGreeter.greet_now gTTS failed: %s", exc
                            )
                            if greeter._try_espeak(text):
                                greeter.spoken.emit(name)
                                with greeter._lock:
                                    greeter._last_greeted[name] = time.time()
                            return
                    if player.can_play(out_path):
                        proc = player.play(out_path)
                        if proc is not None:
                            greeter.spoken.emit(name)
                            with greeter._lock:
                                greeter._last_greeted[name] = time.time()
                            return
                    if greeter._try_espeak(text):
                        greeter.spoken.emit(name)
                        with greeter._lock:
                            greeter._last_greeted[name] = time.time()
                except Exception:  # pragma: no cover
                    log.exception("FaceGreeter.greet_now")

            threading.Thread(
                target=run, daemon=True, name="face-greet-now"
            ).start()
            return

        log.info("FaceGreeter.greet_now: gTTS unavailable; espeak for %s", name)
        if self._try_espeak(text):
            self.spoken.emit(name)
            with self._lock:
                self._last_greeted[name] = time.time()

    def reset_cooldown(self, name: Optional[str] = None) -> None:
        """Forget the last-greeted timestamp(s).

        Used by the screen on `on_enter` so re-opening the Vision tab
        always greets people freshly.
        """
        with self._lock:
            if name is None:
                self._last_greeted.clear()
            else:
                self._last_greeted.pop(name, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cached_clip_for(self, name: str) -> Optional[Path]:
        path = self._cache_dir / f"{_safe_filename(name)}.mp3"
        if path.exists() and path.stat().st_size > 0:
            return path
        return None

    def _spawn_synthesis(self, name: str) -> None:
        with self._lock:
            existing = self._gen_workers.get(name)
            if existing is not None and existing.isRunning():
                # Already on it; don't spam gTTS with parallel requests
                # for the same name.
                return

            text = f"Hello {name}"
            out_path = self._cache_dir / f"{_safe_filename(name)}.mp3"
            worker = _GenerateClipWorker(name, text, out_path, parent=self)
            worker.finished_with_path.connect(self._on_synthesised)
            worker.finished.connect(worker.deleteLater)
            self._gen_workers[name] = worker
            worker.start()

    def _on_synthesised(self, name: str, path_obj) -> None:
        with self._lock:
            self._gen_workers.pop(name, None)
        if path_obj is None:
            text = f"Hello {name}"
            if self._try_espeak(text):
                self.spoken.emit(name)
            else:
                self.reset_cooldown(name)
            return
        path = Path(path_obj)
        if path.exists():
            self._play_clip(path, name)
            return
        self.reset_cooldown(name)

    def _play_clip(self, path: Path, name: str) -> bool:
        text = f"Hello {name}"
        try:
            if self._player.can_play(path):
                proc = self._player.play(path)
                if proc is not None:
                    self.spoken.emit(name)
                    return True
            log.warning(
                "FaceGreeter: cannot play %s (need mpg123 or ffplay); "
                "trying espeak fallback",
                path.name,
            )
        except Exception as exc:  # pragma: no cover - subprocess errors
            log.warning("FaceGreeter playback failed for %s: %s", name, exc)
        if self._try_espeak(text):
            self.spoken.emit(name)
            return True
        log.warning("FaceGreeter: espeak fallback also failed for %s", name)
        self.reset_cooldown(name)
        return False


class FaceGreetReceiver(QObject):
    """Queued receiver for `VisionWorker.faces_recognized` so greetings run
    on this object's thread (typically the GUI thread) even though the
    worker emits from its capture thread."""

    def __init__(self, greeter: FaceGreeter, parent=None) -> None:
        super().__init__(parent)
        self._greeter = greeter

    @pyqtSlot(list)
    def on_faces_recognized(self, names: object) -> None:
        if not names:
            return
        for name in names:
            try:
                self._greeter.greet(str(name))
            except Exception:
                log.exception("FaceGreetReceiver: greet failed for %r", name)
