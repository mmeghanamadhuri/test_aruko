"""
Lightweight audio playback used by Nina to speak greetings while
performing actions.

The class delegates to whichever ALSA-compatible CLI player is on the
Jetson (`aplay` for WAV, `mpg123` for MP3, `ffplay` as a final
fallback). No Python audio dependencies are required, so this works on
a fresh JetPack image without extra pip installs.

Before starting a clip, ``AudioPlayer.play`` can optionally run a **volume
preroll**: save sink/Master level, set **0%**, dwell (``NINA_AUDIO_MUTE_PREROLL_SEC``),
then restore (ALSA ``amixer`` first, else PulseAudio). **Default dwell is 0**
(disabled): the old default (**4 s**) muted the amp before every clip and
caused long silence / stutter — especially with USB DACs, dmix, or when
the mixer control does not match the PCM device mpg123 uses. Set
``NINA_AUDIO_MUTE_PREROLL_SEC=0.15`` (or similar) only on hardware that
needs a brief mute to avoid power-on thump. If volume preroll cannot run,
``NINA_AUDIO_PREROLL_MS`` can inject digital silence via ``aplay`` instead.

When mute preroll is **off** (the default), ``play_silence_preroll_blocking``
still **clears Pulse/ALSA mute** and plays a very short digital silence
(``NINA_AUDIO_OUTPUT_WARMUP_MS``, default **100** ms) on the same device as
greetings — many dmix / USB DAC paths fail or pop if mpg123 is the first
client with no prior open. Set ``NINA_AUDIO_OUTPUT_WARMUP_MS=0`` to skip.
If Master was left at **0%%** from an interrupted old preroll, set
``NINA_AUDIO_RECOVER_ZERO_MASTER=1`` so we restore ``NINA_AUDIO_RESTORE_VOLUME_PCT``.

For MP3, set ``NINA_AUDIO_MPG123_DEVICE`` or ``NINA_GREET_APLAY_DEVICE`` so
``mpg123`` opens the **same** ALSA device the preroll mutes; otherwise the
decoder may use a different PCM path and volume changes won't prevent
startup glitches / underrun noise.

Output sample rate: ``NINA_AUDIO_OUTPUT_RATE`` (Hz) defaults to **48000**,
matching typical Jetson HDMI/USB DACs. Silence warmup WAVs and ``mpg123 -r``
use this rate so the stream matches the device (MP3s are most often 44100 Hz;
mpg123 resamples cleanly when the rate is forced). Set **44100** for 44.1 kHz
hardware, or **auto** to restore legacy behaviour (no ``-r`` on mpg123,
44100 Hz preroll — can mis-match 48 kHz sinks and cause artifacts).

Install hint on the Jetson:
    sudo apt install -y alsa-utils mpg123
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mute_preroll_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("NINA_AUDIO_MUTE_PREROLL_SEC", "0")))
    except ValueError:
        return 0.0


def _preroll_ms() -> int:
    try:
        return max(0, int(os.environ.get("NINA_AUDIO_PREROLL_MS", "0")))
    except ValueError:
        return 0


def _output_warmup_ms() -> int:
    """Short silence before real audio so dmix/DAC sees an open PCM stream first."""
    raw = (os.environ.get("NINA_AUDIO_OUTPUT_WARMUP_MS") or "").strip()
    if raw:
        try:
            return max(0, min(2000, int(raw)))
        except ValueError:
            return 100
    return 100


def _recover_zero_master_enabled() -> bool:
    v = (os.environ.get("NINA_AUDIO_RECOVER_ZERO_MASTER") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _restore_volume_default_pct() -> int:
    try:
        return max(1, min(100, int(os.environ.get("NINA_AUDIO_RESTORE_VOLUME_PCT", "75"))))
    except ValueError:
        return 75


def _aplay_device_flag() -> Optional[str]:
    d = (os.environ.get("NINA_GREET_APLAY_DEVICE") or "").strip()
    return d or None


def _pcm_output_rate_hz() -> Optional[int]:
    """Sample rate (Hz) for preroll WAV and ``mpg123 -r``.

    * **Default** 48000 — common for Jetson HDMI/USB Class 1 audio.
    * ``NINA_AUDIO_OUTPUT_RATE=44100`` (etc.) — explicit hardware rate.
    * ``auto`` / ``native`` — do not pass ``-r`` to mpg123; preroll uses 44100 Hz
      (legacy; can mismatch 48 kHz devices).
    """
    raw = (os.environ.get("NINA_AUDIO_OUTPUT_RATE") or "48000").strip().lower()
    if raw in ("auto", "native"):
        return None
    try:
        hz = int(raw)
    except ValueError:
        return 48000
    if hz <= 0:
        return None
    return max(8000, min(192000, hz))


def _preroll_wav_sample_rate_hz() -> int:
    """Warmup / silence WAV rate (must match ``mpg123 -r`` when rate is forced)."""
    forced = _pcm_output_rate_hz()
    return 44100 if forced is None else forced


def _parse_volume_pct_from_text(text: str) -> Optional[int]:
    """First ``NN%`` in pactl/amixer output (e.g. 50% / ... dB)."""
    if not text:
        return None
    m = re.search(r"(\d{1,3})%", text)
    if not m:
        return None
    v = int(m.group(1))
    return v if 0 <= v <= 150 else None


def _ensure_preroll_wav(ms: int, sample_rate: int) -> Optional[Path]:
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


def _alsa_amixer_base() -> Optional[List[str]]:
    exe = shutil.which("amixer")
    if not exe:
        return None
    cmd: List[str] = [exe]
    card = (os.environ.get("NINA_AUDIO_MIXER_CARD") or "").strip()
    if card:
        cmd.extend(["-c", card])
    return cmd


def _alsa_mixer_control() -> str:
    return (os.environ.get("NINA_AUDIO_MIXER_CONTROL") or "Master").strip() or "Master"


def _alsa_get_volume_pct() -> Optional[int]:
    base = _alsa_amixer_base()
    if not base:
        return None
    try:
        r = subprocess.run(
            base + ["sget", _alsa_mixer_control()],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return _parse_volume_pct_from_text(r.stdout or "")


def _alsa_set_volume_pct(pct: int) -> bool:
    base = _alsa_amixer_base()
    if not base:
        return False
    pct = max(0, min(100, int(pct)))
    try:
        r = subprocess.run(
            base + ["-q", "sset", _alsa_mixer_control(), f"{pct}%"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _pulse_get_volume_pct() -> Optional[int]:
    pactl = shutil.which("pactl")
    if not pactl:
        return None
    try:
        r = subprocess.run(
            [pactl, "get-sink-volume", "@DEFAULT_SINK@"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return _parse_volume_pct_from_text(r.stdout or "")


def mpg123_command_for(path: Path) -> Optional[List[str]]:
    """Build an ``mpg123`` argv list, optionally binding ALSA output so the
    same device sees volume preroll (``amixer``/``pactl``) and decoded PCM.

    ``NINA_AUDIO_MPG123_DEVICE`` wins; else ``NINA_GREET_APLAY_DEVICE`` (the
    aplay ``-D`` value works as ``mpg123 -a``).
    """
    mpg = shutil.which("mpg123")
    if not mpg:
        return None
    cmd: List[str] = [mpg, "-q"]
    dev = (
        os.environ.get("NINA_AUDIO_MPG123_DEVICE")
        or os.environ.get("NINA_GREET_APLAY_DEVICE")
        or ""
    ).strip()
    if dev:
        cmd.extend(["-o", "alsa", "-a", dev])
    rate = _pcm_output_rate_hz()
    if rate is not None:
        cmd.extend(["-r", str(rate)])
    cmd.append(str(path))
    return cmd


def _pulse_set_volume_pct(pct: int) -> bool:
    pactl = shutil.which("pactl")
    if not pactl:
        return False
    pct = max(0, min(150, int(pct)))
    try:
        r = subprocess.run(
            [pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ensure_sinks_unmuted() -> None:
    """Clear soft-mute on common paths (Pulse default sink, ALSA Master)."""
    pactl = shutil.which("pactl")
    if pactl:
        try:
            subprocess.run(
                [pactl, "set-sink-mute", "@DEFAULT_SINK@", "0"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=3.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    base = _alsa_amixer_base()
    if not base:
        return
    ctrl = _alsa_mixer_control()
    try:
        subprocess.run(
            base + ["-q", "sset", ctrl, "unmute"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _maybe_recover_alsa_master_from_zero() -> None:
    if not _recover_zero_master_enabled():
        return
    pct = _alsa_get_volume_pct()
    if pct is None or pct > 0:
        return
    _alsa_set_volume_pct(_restore_volume_default_pct())


def _play_aplay_silence_ms(ms: int) -> None:
    """Play silent WAV through ``aplay`` for *ms* milliseconds (warmup / fallback)."""
    if ms <= 0:
        return
    aplay = shutil.which("aplay")
    if not aplay:
        return
    wav = _ensure_preroll_wav(ms, _preroll_wav_sample_rate_hz())
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


def _silence_wav_preroll_blocking() -> None:
    """Play silent WAV through ``aplay`` when volume preroll is unavailable."""
    _play_aplay_silence_ms(_preroll_ms())


def play_silence_preroll_blocking() -> None:
    """Before playback: optional volume preroll, else unmute + short PCM warmup.

    If ``NINA_AUDIO_MUTE_PREROLL_SEC`` > 0: mute dwell then restore (ALSA, else Pulse).

    If **off** (default **0**): clear Pulse/ALSA mute, optionally bump Master if
    ``NINA_AUDIO_RECOVER_ZERO_MASTER`` and level reads 0%%, then play
    ``NINA_AUDIO_OUTPUT_WARMUP_MS`` + ``NINA_AUDIO_PREROLL_MS`` of silence via
    ``aplay`` (same ``-D`` as ``NINA_GREET_APLAY_DEVICE`` when set).
    """
    sec = _mute_preroll_sec()
    if sec <= 0:
        _ensure_sinks_unmuted()
        _maybe_recover_alsa_master_from_zero()
        _play_aplay_silence_ms(_output_warmup_ms() + _preroll_ms())
        return

    restore_default = _restore_volume_default_pct()

    # Prefer ALSA Master first: mpg123/aplay often hit the card directly;
    # Pulse default sink may not affect that path on Jetson kiosks.
    alsa_saved = _alsa_get_volume_pct()
    if _alsa_set_volume_pct(0):
        try:
            time.sleep(sec)
        finally:
            r = alsa_saved if alsa_saved is not None else restore_default
            _alsa_set_volume_pct(r)
        return

    pulse_saved = _pulse_get_volume_pct()
    if _pulse_set_volume_pct(0):
        try:
            time.sleep(sec)
        finally:
            r = pulse_saved if pulse_saved is not None else restore_default
            _pulse_set_volume_pct(r)
        return

    _silence_wav_preroll_blocking()


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
        if not skip_preroll:
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
        if ext in (".mp3",):
            cmd = mpg123_command_for(Path(path))
            if cmd:
                return cmd
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
