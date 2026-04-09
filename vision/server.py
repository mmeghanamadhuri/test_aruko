"""
TCP JSON-line server: exposes latest window-button detections.

Default port 5001 (motion_server uses 5000). One line per message, newline-terminated.

Commands (client → server):
  {"cmd": "latest"}   — last inference snapshot from all cameras
  {"cmd": "ping"}     — {"status":"ok"}

Environment: same as runner (ROBOFLOW_*, CARBOT_VISION_CAMERAS, etc.)
plus VISION_SERVER_HOST (default 0.0.0.0), VISION_SERVER_PORT (default 5001).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .camera import MultiCamera
from .config import VisionConfig
from .detector import build_detector, filter_by_allowlist, filter_by_confidence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("carbot.vision.server")


class VisionServer:
    def __init__(self) -> None:
        self.cfg = VisionConfig.from_env()
        self._detector = build_detector(self.cfg)
        self._cams = MultiCamera(self.cfg.camera_indices)
        self._lock = threading.Lock()
        self._latest: Dict[str, Any] = {
            "status": "ok",
            "ts": None,
            "inference_ms": 0.0,
            "cameras": [],
        }
        self._shutdown = threading.Event()
        self._worker: Optional[threading.Thread] = None

        self.host = os.environ.get("VISION_SERVER_HOST", "0.0.0.0")
        self.port = int(os.environ.get("VISION_SERVER_PORT", "5001"))

    def _infer_tick(self) -> None:
        t0 = time.perf_counter()
        cameras_out: List[Dict[str, Any]] = []

        for cam_idx, frame in self._cams.read_all():
            if frame is None:
                cameras_out.append({"camera_id": cam_idx, "detections": [], "error": "no_frame"})
                continue
            dets = self._detector.infer(frame, camera_id=cam_idx)
            dets = filter_by_confidence(dets, self.cfg.confidence_threshold)
            dets = filter_by_allowlist(dets, self.cfg.label_allowlist)
            cameras_out.append(
                {
                    "camera_id": cam_idx,
                    "detections": [d.as_dict() for d in dets],
                }
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        snap = {
            "status": "ok",
            "ts": datetime.now(timezone.utc).isoformat(),
            "inference_ms": round(elapsed_ms, 2),
            "cameras": cameras_out,
        }
        with self._lock:
            self._latest = snap

    def _worker_loop(self) -> None:
        interval = self.cfg.infer_interval_sec
        while not self._shutdown.is_set():
            try:
                self._infer_tick()
            except Exception as e:
                log.exception("Inference tick failed: %s", e)
            if self._shutdown.wait(timeout=interval):
                break

    def start_worker(self) -> None:
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        log.info("Vision inference worker started (interval=%.2fs)", self.cfg.infer_interval_sec)

    def stop_worker(self) -> None:
        self._shutdown.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self._cams.release()

    def get_latest(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def send_resp(self, sock: socket.socket, resp: Dict[str, Any]) -> None:
        try:
            msg = json.dumps(resp) + "\n"
            sock.sendall(msg.encode("utf-8"))
        except Exception as e:
            log.error("send failed: %s", e)

    def handle_client(self, client_sock: socket.socket) -> None:
        buffer = ""
        try:
            client_sock.settimeout(30.0)
            while True:
                try:
                    data = client_sock.recv(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            self.send_resp(
                                client_sock, {"status": "error", "error": "Invalid JSON"}
                            )
                            continue
                        if not isinstance(msg, dict) or "cmd" not in msg:
                            self.send_resp(
                                client_sock, {"status": "error", "error": "Missing cmd"}
                            )
                            continue
                        cmd = msg["cmd"]
                        if cmd == "ping":
                            self.send_resp(client_sock, {"status": "ok", "service": "vision"})
                        elif cmd == "latest":
                            self.send_resp(client_sock, self.get_latest())
                        else:
                            self.send_resp(
                                client_sock,
                                {"status": "error", "error": f"Unknown cmd '{cmd}'"},
                            )
                except socket.timeout:
                    continue
        except Exception as e:
            log.error("client error: %s", e)
        finally:
            client_sock.close()

    def serve_forever(self) -> None:
        self.start_worker()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(5)
        srv.settimeout(1.0)
        log.info("Vision TCP server on %s:%s", self.host, self.port)

        try:
            while not self._shutdown.is_set():
                try:
                    client, addr = srv.accept()
                    log.info("Connection from %s", addr)
                    t = threading.Thread(
                        target=self.handle_client, args=(client,), daemon=True
                    )
                    t.start()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            log.info("Keyboard interrupt")
        finally:
            self._shutdown.set()
            srv.close()
            self.stop_worker()
            log.info("Vision server stopped.")


def main() -> None:
    VisionServer().serve_forever()


if __name__ == "__main__":
    main()
