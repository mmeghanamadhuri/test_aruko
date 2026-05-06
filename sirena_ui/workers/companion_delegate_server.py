"""
Localhost HTTP delegate so the Jetson nina-link daemon can queue motion while this
process holds the Dynamixel serial bus (same pattern as the Qt Actions screen).

Playback uses the same ``PlaybackWorker`` as the desktop Actions screen so motion,
timing, and **audio** (mpg123/aplay + offsets) match exactly.

The localhost delegate is **on by default** while Sirena UI runs. To disable::

    export NINA_UI_ACTION_DELEGATE=0

Optional port (default **8791**)::

    export NINA_UI_ACTION_DELEGATE_PORT=8791

nina-link auto-probes ``http://127.0.0.1:8791`` before opening the Dynamixel port; you
only need ``NINA_LINK_ACTION_DELEGATE_URL`` if the delegate listens elsewhere.

**Deploy:** install this module under the same ``PYTHONPATH`` as ``python -m sirena_ui``
on the Jetson (copying one file into the wrong directory has no effect).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Type

from PyQt5.QtCore import QObject, Qt, pyqtSignal

from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.workers.companion_delegate_server")

_server: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
# Set in ``start_companion_delegate_server`` — never pass ``NinaService`` through ``pyqtSignal``
# (non-QObject payloads across threads can abort Qt with SIGABRT).
_delegate_service_ref: Optional[NinaService] = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on", "y"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _playback_runnable_fallback(service: NinaService, action_name: str) -> None:
    """Headless fallback (no QApplication): motion + subprocess audio — may miss ALSA session."""
    try:
        service.ensure_bus()
        from nina.services.audio_player import AudioPlayer

        audio_path = service.action_audio_path(action_name)
        offset = (
            max(0.0, float(service.action_audio_offset(action_name)))
            if audio_path
            else 0.0
        )
        player = AudioPlayer()

        def schedule_audio() -> None:
            if audio_path is None:
                return
            if offset <= 0.0:
                player.play(audio_path)
                return
            timer = threading.Timer(offset, player.play, args=(audio_path,))
            timer.daemon = True
            timer.start()

        with service.bus_lock:
            schedule_audio()
            service.action_runner.run_named_action(
                action_name,
                smooth=True,
                sub_hz=50.0,
                max_speed=1023,
                speed=0.5,
            )
    except Exception:
        log.exception("companion delegate playback (fallback) %s", action_name)


def _ensure_and_start_playback_worker(service: NinaService, action_name: str) -> None:
    """Runs on the Qt GUI thread — same stack as ``ActionsScreen._on_play``."""
    from PyQt5.QtWidgets import QApplication

    from sirena_ui.workers.playback_worker import PlaybackWorker

    try:
        service.ensure_bus()
    except Exception:
        log.exception("companion delegate ensure_bus")
        return

    app = QApplication.instance()
    audio_path = service.action_audio_path(action_name)
    audio_offset = (
        service.action_audio_offset(action_name) if audio_path else 0.0
    )
    # Parent + finished→deleteLater: a bare local ``PlaybackWorker`` was GC'd while the
    # QThread was still running → "QThread: Destroyed while thread is still running" / SIGABRT.
    worker = PlaybackWorker(
        service,
        action_name,
        audio_path=audio_path,
        audio_offset_sec=audio_offset,
        parent=app,
    )
    worker.finished_ok.connect(lambda n: log.debug("delegate playback finished %s", n))
    worker.failed.connect(lambda msg: log.warning("delegate playback failed: %s", msg))
    worker.finished.connect(worker.deleteLater)
    worker.start()


class _DelegatePlaybackInvoker(QObject):
    """Created on the GUI thread; HTTP threads emit here for QueuedConnection delivery.

    Signal carries **only** the action name string — see module ``_delegate_service_ref``.
    """

    playback_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.playback_requested.connect(self._deliver, Qt.QueuedConnection)

    def _deliver(self, action_name: str) -> None:
        svc = _delegate_service_ref
        if svc is None:
            log.error("companion delegate: no NinaService ref — cannot play %s", action_name)
            return
        _ensure_and_start_playback_worker(svc, action_name)


_invoker: Optional[_DelegatePlaybackInvoker] = None


def prime_delegate_invoker() -> None:
    """Call once from ``main()`` on the GUI thread after ``QApplication`` exists."""
    global _invoker
    if _invoker is None:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        _invoker = _DelegatePlaybackInvoker(parent=app)


def _schedule_delegate_playback(action_name: str) -> None:
    """HTTP handler thread must not run Qt workers; queue onto the GUI thread."""
    from PyQt5.QtWidgets import QApplication

    svc = _delegate_service_ref
    if svc is None:
        log.error("companion delegate: service ref missing")
        return

    if QApplication.instance() is None:
        log.warning("companion delegate: no QApplication, using threaded fallback")
        threading.Thread(
            target=_playback_runnable_fallback,
            args=(svc, action_name),
            daemon=True,
            name=f"sirena-delegate-fallback-{action_name}",
        ).start()
        return
    if _invoker is None:
        log.warning("companion delegate: invoker not primed — using threaded fallback")
        threading.Thread(
            target=_playback_runnable_fallback,
            args=(svc, action_name),
            daemon=True,
            name=f"sirena-delegate-fallback-{action_name}",
        ).start()
        return
    _invoker.playback_requested.emit(action_name)


def _make_handler_class(service: NinaService) -> Type[BaseHTTPRequestHandler]:
    class DelegateHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].rstrip("/") != "/v1/actions/play":
                self.send_error(404, "Not Found")
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8", errors="strict"))
            except (json.JSONDecodeError, UnicodeError):
                self.send_error(400, "Invalid JSON")
                return
            name = str(body.get("action", "")).strip()
            if not name:
                self.send_error(400, "Missing action")
                return
            _schedule_delegate_playback(name)
            payload = json.dumps(
                {"ok": True, "queued": True, "action": name}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

    return DelegateHandler


def start_companion_delegate_server(service: NinaService) -> None:
    """Bind ``127.0.0.1:NINA_UI_ACTION_DELEGATE_PORT`` unless ``NINA_UI_ACTION_DELEGATE=0``."""
    global _server, _server_thread, _delegate_service_ref

    # Default ON so tablets work without extra env; opt out with NINA_UI_ACTION_DELEGATE=0.
    if not _env_bool("NINA_UI_ACTION_DELEGATE", True):
        return
    _delegate_service_ref = service
    prime_delegate_invoker()
    port = max(1, min(65535, _env_int("NINA_UI_ACTION_DELEGATE_PORT", 8791)))
    handler_cls = _make_handler_class(service)
    try:
        _server = HTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as e:
        log.error("companion delegate bind 127.0.0.1:%s failed: %s", port, e)
        return

    def serve() -> None:
        assert _server is not None
        log.info(
            "companion delegate listening on http://127.0.0.1:%s/v1/actions/play",
            port,
        )
        try:
            _server.serve_forever()
        except Exception:
            log.exception("companion delegate server stopped")

    _server_thread = threading.Thread(target=serve, daemon=True, name="sirena-delegate-http")
    _server_thread.start()
