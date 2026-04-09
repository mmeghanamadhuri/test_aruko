"""Threaded MJPEG HTTP server so you can view the gripper camera in a browser."""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

log = logging.getLogger("carbot.vision.mjpeg")


class MJPEGServer:
    """
    Serves ``GET /`` as multipart JPEG (works in Chrome/Firefox/Safari).

    Call ``update_frame(jpeg_bytes)`` from your capture loop (30–60 fps is fine).
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = int(port)
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def update_frame(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            self._jpeg = jpeg_bytes

    def _get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg

    def _make_handler(self) -> Any:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                log.debug("%s - %s", self.address_string(), fmt % args)

            def do_GET(self) -> None:
                if self.path not in ("/", "/index.html"):
                    self.send_error(404, "Use GET /")
                    return
                self.send_response(200)
                self.send_header(
                    "Content-type",
                    "multipart/x-mixed-replace; boundary=frame",
                )
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                try:
                    while True:
                        jpg = parent._get_jpeg()
                        if jpg:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode())
                            self.wfile.write(jpg)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        return Handler

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        handler = self._make_handler()
        self._httpd = HTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        log.info("MJPEG preview http://%s:%s/ (open on laptop if 0.0.0.0 → use jetson-ip:%s)", self.host, self.port, self.port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
