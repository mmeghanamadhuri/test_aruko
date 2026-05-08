"""
Speaks the labels currently visible to Nina's object detector.

Bound to the "Play Objects" button on the Vision screen. The button
hands us a list of detected labels (e.g. ``["person", "chair",
"chair", "bottle"]``) and we:

  * Deduplicate and pluralise: ``["person", "chair", "chair",
    "bottle"]`` -> ``"I see a person, two chairs and a bottle."``
  * Synthesise once per unique sentence on a background QThread so the
    GUI never blocks on the gTTS HTTP call.
  * Cache the resulting MP3 on disk under ``nina/data/announcements/``
    so identical scenes replay instantly without internet.
  * Honour a short cooldown so a double-click doesn't queue up two
    overlapping playbacks.

Public API:

    announcer = ObjectAnnouncer()
    announcer.announce(["person", "chair"])
    announcer.announce_empty()      # speaks "I don't see anything yet."

Failure paths surface via :pyattr:`error` so the screen can show a
toast on first-run problems (no gTTS, no audio player, network down).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer


log = logging.getLogger("sirena_ui.object_announcer")


# A hand-rolled list is shorter and faster than dragging in `inflect`
# for the few dozen COCO labels we actually announce. Anything not in
# the table falls back to a naive "+s" rule.
_IRREGULAR_PLURALS: Dict[str, str] = {
    "person": "people",
    "mouse": "mice",
    "knife": "knives",
    "child": "children",
    "tooth": "teeth",
    "foot": "feet",
    "sheep": "sheep",
    "fish": "fish",
    "deer": "deer",
}

# COCO-style labels that read as compound words ("traffic light",
# "sports ball"). Our heuristic pluralises the last word so we get
# "traffic lights" / "sports balls" not "traffic light s".
_NUMBER_WORDS: Dict[int, str] = {
    1: "a",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def _pluralise(label: str, count: int) -> str:
    """Return ``label`` if ``count == 1``, else its plural form."""
    if count <= 1:
        return label
    parts = label.split()
    head = parts[-1]
    pl = _IRREGULAR_PLURALS.get(head)
    if pl is None:
        if head.endswith(("s", "x", "z", "ch", "sh")):
            pl = head + "es"
        elif head.endswith("y") and len(head) > 1 and head[-2] not in "aeiou":
            pl = head[:-1] + "ies"
        else:
            pl = head + "s"
    parts[-1] = pl
    return " ".join(parts)


def _article(label: str) -> str:
    """Return 'an' if the label starts with a vowel sound, else 'a'."""
    return "an" if label[:1].lower() in "aeiou" else "a"


def _format_phrase(label: str, count: int) -> str:
    if count == 1:
        return f"{_article(label)} {label}"
    word = _NUMBER_WORDS.get(count, str(count))
    return f"{word} {_pluralise(label, count)}"


def _join_phrases(phrases: Sequence[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"


def build_sentence(labels: Iterable[str]) -> str:
    """Turn raw detection labels into a speakable sentence.

    Order is by descending count, then alphabetical, so the operator
    hears the most common things first.
    """
    counts = Counter(s for s in (str(x).strip() for x in labels) if s)
    if not counts:
        return "I don't see anything yet."
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    phrases = [_format_phrase(label, n) for label, n in ordered]
    return "I see " + _join_phrases(phrases) + "."


def _cache_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _safe_stem(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", text.strip())[:48].strip("_")
    return cleaned or "scene"


class _GenerateClipWorker(QThread):
    """Synthesise one announcement MP3 in the background."""

    finished_with_path = pyqtSignal(str, object)  # cache_key, Path | None

    def __init__(self, key: str, text: str, out_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._text = text
        self._out_path = out_path

    def run(self) -> None:  # noqa: D401 - QThread entry
        try:
            AudioGenerator.generate(self._text, self._out_path)
            self.finished_with_path.emit(self._key, self._out_path)
        except AudioGeneratorError as exc:
            log.warning("Object announcer TTS failed: %s", exc)
            self.finished_with_path.emit(self._key, None)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Object announcer TTS crashed: %s", exc)
            self.finished_with_path.emit(self._key, None)


class ObjectAnnouncer(QObject):
    """Speak the current set of detected object labels."""

    spoken = pyqtSignal(str)   # the sentence we just played
    error = pyqtSignal(str)    # human-readable failure reason

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        cooldown_sec: float = 1.5,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if cache_dir is None:
            repo_root = Path(__file__).resolve().parents[2]
            cache_dir = repo_root / "nina" / "data" / "announcements"
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cooldown = float(cooldown_sec)
        self._last_spoken_at = 0.0
        self._last_sentence: Optional[str] = None
        self._lock = threading.RLock()
        self._player = AudioPlayer()
        self._gen_workers: Dict[str, _GenerateClipWorker] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def announce(self, labels: Iterable[str]) -> None:
        """Build a sentence from the labels and speak it."""
        sentence = build_sentence(labels)
        self._speak(sentence)

    def announce_empty(self) -> None:
        """Convenience for "I don't see anything yet." with no labels."""
        self._speak("I don't see anything yet.")

    def speak_sentence(self, sentence: str) -> None:
        """Speak arbitrary text (cached like other announcements)."""
        s = (sentence or "").strip()
        if not s:
            return
        self._speak(s)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _speak(self, sentence: str) -> None:
        if not sentence:
            return
        if not self._player.is_supported:
            msg = (
                "No audio player available. Install one via:\n"
                "  sudo apt install mpg123    # or alsa-utils / ffmpeg"
            )
            log.warning(msg)
            self.error.emit(msg)
            return

        with self._lock:
            now = time.time()
            if (
                self._last_sentence == sentence
                and now - self._last_spoken_at < self._cooldown
            ):
                # Operator double-clicked the button; don't overlap.
                return
            self._last_sentence = sentence
            self._last_spoken_at = now

        clip = self._cached_clip_for(sentence)
        if clip is not None:
            self._play_clip(clip, sentence)
            return
        self._spawn_synthesis(sentence)

    def _cached_clip_for(self, sentence: str) -> Optional[Path]:
        key = _cache_key(sentence)
        path = self._cache_dir / f"{_safe_stem(sentence)}_{key}.mp3"
        if path.exists() and path.stat().st_size > 0:
            return path
        return None

    def _spawn_synthesis(self, sentence: str) -> None:
        key = _cache_key(sentence)
        with self._lock:
            existing = self._gen_workers.get(key)
            if existing is not None and existing.isRunning():
                return
            out_path = self._cache_dir / f"{_safe_stem(sentence)}_{key}.mp3"
            worker = _GenerateClipWorker(key, sentence, out_path, parent=self)
            worker.finished_with_path.connect(self._on_synthesised)
            worker.finished.connect(worker.deleteLater)
            self._gen_workers[key] = worker
            worker.start()

    def _on_synthesised(self, key: str, path_obj) -> None:
        with self._lock:
            self._gen_workers.pop(key, None)
        if path_obj is None:
            self.error.emit(
                "Couldn't synthesise audio. Check internet connectivity "
                "and that gTTS is installed (pip install --user gTTS)."
            )
            return
        path = Path(path_obj)
        if path.exists():
            # The sentence we asked for is whichever is currently in
            # _last_sentence (set in _speak before the synth started).
            sentence = self._last_sentence or path.stem
            self._play_clip(path, sentence)

    def _play_clip(self, path: Path, sentence: str) -> None:
        try:
            self._player.play(path)
        except Exception as exc:  # pragma: no cover
            msg = f"Couldn't play audio: {exc}"
            log.warning(msg)
            self.error.emit(msg)
            return
        self.spoken.emit(sentence)

    def list_known_labels(self) -> List[str]:
        """Used in tests to verify the irregular-plural table."""
        return list(_IRREGULAR_PLURALS.keys())
