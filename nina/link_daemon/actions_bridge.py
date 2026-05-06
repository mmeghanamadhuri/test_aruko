"""Optional Dynamixel playback via nina-link (same stack as ``python -m nina.app run-action``).

Enable with ``NINA_LINK_ENABLE_ACTION_BRIDGE=1``.

**Desktop Sirena UI + companion tablet:** nina-link tries **localhost delegates first**
(``NINA_LINK_ACTION_DELEGATE_URL`` if set, then ``http://127.0.0.1:8791``) so motion runs in
the Qt process that owns the serial bus. If nothing is listening, playback falls back to
opening the Dynamixel port from nina-link.

**Audio:** Local fallback schedules manifest MP3/WAV the same way as the desktop Actions screen.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from nina.link_daemon.config import load_config
from nina.link_daemon.dynamixel_bundle import (
    bus_lock,
    get_action_runner_bundle,
    release_bundle_serial,
)

log = logging.getLogger("nina.link_daemon.actions_bridge")

_DEFAULT_DELEGATE = "http://127.0.0.1:8791"


def _connection_refused(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.URLError):
        r = getattr(exc, "reason", None)
        if isinstance(r, ConnectionRefusedError):
            return True
        if isinstance(r, OSError) and getattr(r, "errno", None) == 111:
            return True
    msg = str(exc).lower()
    return "refused" in msg or "errno 111" in msg


def _delegate_urls(cfg: Any) -> List[str]:
    raw = getattr(cfg, "action_delegate_url", None)
    out: List[str] = []
    if isinstance(raw, str) and raw.strip():
        out.append(raw.strip().rstrip("/"))
    if _DEFAULT_DELEGATE not in out:
        out.append(_DEFAULT_DELEGATE)
    return out


def _post_delegate_http(base_url: str, action_name: str) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/v1/actions/play"
    body = json.dumps({"action": action_name}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return {"ok": True, "queued": True, "action": action_name}
        out = json.loads(raw)
        if isinstance(out, dict):
            return out
        return {"ok": True, "delegate_response": out}


def _try_delegate(base_url: str, action_name: str) -> Optional[Dict[str, Any]]:
    """Return response dict on success; ``None`` if nothing listens (try fallback)."""
    try:
        return _post_delegate_http(base_url, action_name)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(
            f"Action delegate HTTP {e.code}: {detail[:800]}"
        ) from e
    except urllib.error.URLError as e:
        if _connection_refused(e):
            log.info(
                "action delegate %s unreachable (%s) — trying next / local playback",
                base_url,
                e.reason,
            )
            return None
        raise RuntimeError(
            f"Action delegate error ({base_url}): {e.reason!r}"
        ) from e


def _resolve_action_audio_path(ar: Any, settings: Any, name: str) -> Optional[Path]:
    """Mirror ``NinaService.action_audio_path`` for headless nina-link."""
    rel = ar.get_action_audio(name)
    if rel:
        candidate = settings.actions_dir / rel
        if candidate.exists():
            return candidate
    for ext in (".wav", ".mp3"):
        candidate = settings.actions_dir / "audio" / f"{name}{ext}"
        if candidate.exists():
            return candidate
    return None


def play_named_action(action_name: str) -> Dict[str, Any]:
    name = action_name.strip()
    if not name:
        return {"ok": False, "error": "empty action name"}
    cfg = load_config()

    for base in _delegate_urls(cfg):
        delegated = _try_delegate(base, name)
        if delegated is not None:
            return delegated

    ar, _dxl, settings = get_action_runner_bundle()

    def run() -> None:
        try:
            from nina.services.audio_player import AudioPlayer

            player = AudioPlayer()
            audio_path = _resolve_action_audio_path(ar, settings, name)
            offset = (
                max(0.0, float(ar.get_action_audio_offset(name)))
                if audio_path is not None
                else 0.0
            )

            def schedule_audio() -> None:
                if audio_path is None:
                    return
                if offset <= 0.0:
                    player.play(audio_path)
                    log.debug("action audio immediate %s", audio_path)
                    return
                timer = threading.Timer(offset, player.play, args=(audio_path,))
                timer.daemon = True
                timer.start()
                log.debug("action audio delayed %.2fs %s", offset, audio_path)

            with bus_lock:
                schedule_audio()
                try:
                    ar.run_named_action(
                        name,
                        smooth=True,
                        sub_hz=50.0,
                        max_speed=1023,
                        speed=0.5,
                    )
                except Exception:
                    # Motion failed after audio started — stop mpg123/aplay so we don't get "audio only".
                    player.stop_all()
                    raise
                finally:
                    # Let go of /dev/ttyUSB* so Sirena UI (separate process) can drive Dynamixels.
                    release_bundle_serial()
        except Exception:
            log.exception("play_named_action %s", name)

    threading.Thread(target=run, daemon=True, name=f"nina-action-{name}").start()
    return {"ok": True, "queued": True, "action": name}
