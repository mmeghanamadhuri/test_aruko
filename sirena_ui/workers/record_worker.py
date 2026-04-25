"""
Background QThread that records an action from live motors.

Mirrors the `record-action` CLI flow but exposes:
  - per-frame progress signal,
  - cooperative stop request that saves whatever frames captured so far.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from sirena_ui.workers.nina_service import NinaService


class RecordWorker(QThread):
    countdown = pyqtSignal(int)
    progress = pyqtSignal(int, int, float)
    finished_ok = pyqtSignal(str, int)
    failed = pyqtSignal(str)

    def __init__(
        self,
        service: NinaService,
        name: str,
        seconds: float,
        hz: float,
        countdown_sec: float = 3.0,
        register: bool = True,
        hold_after: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._name = name
        self._seconds = seconds
        self._hz = hz
        self._countdown_sec = max(0.0, countdown_sec)
        self._register = register
        self._hold_after = hold_after
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            with self._service.bus_lock:
                dxl = self._service.dxl
                dxl.set_torque_all(False)

                whole = int(self._countdown_sec)
                for remaining in range(whole, 0, -1):
                    if self._stop_event.is_set():
                        return self._abort("Stopped before recording started.")
                    self.countdown.emit(remaining)
                    time.sleep(1.0)
                fractional = self._countdown_sec - whole
                if fractional > 0:
                    time.sleep(fractional)

                interval = 1.0 / max(1.0, self._hz)
                target_frames = max(1, int(self._seconds * self._hz))
                frames = []
                start = time.monotonic()
                for i in range(target_frames):
                    if self._stop_event.is_set():
                        break
                    frames.append(dxl.capture_frame(duration=interval))
                    elapsed = time.monotonic() - start
                    self.progress.emit(i + 1, target_frames, elapsed)
                    time.sleep(interval)

                if self._hold_after:
                    dxl.set_torque_all(True)

                out_path: Path = self._service.settings.recordings_dir / f"{self._name}.json"
                payload = {
                    "robot": "nina",
                    "description": f"Recorded action: {self._name}",
                    "frame_count": len(frames),
                    "frames": frames,
                }
                out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

                if self._register:
                    self._service.action_runner.register_action(
                        self._name, f"recordings/{self._name}.json"
                    )

            self.finished_ok.emit(self._name, len(frames))
        except Exception as exc:  # pragma: no cover - reported back to UI
            self.failed.emit(str(exc))

    def _abort(self, reason: str) -> None:
        try:
            if self._hold_after:
                self._service.dxl.set_torque_all(True)
        finally:
            self.failed.emit(reason)
