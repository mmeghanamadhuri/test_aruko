"""
Qt facade over `VisionPipeline`.

Owns a dedicated worker thread that pumps `pipeline.step()` at the
configured target rate, emits the annotated frame as a `QImage`, and
publishes detection / FPS / status updates as Qt signals.

Anything that can block - camera open, YuNet ONNX download, the
first YOLO + TensorRT export on a Jetson - is dispatched onto the
worker thread via a small command queue so the GUI never stalls
when the user toggles a feature on.

Public surface (used by `VisionScreen`):

  signals
    frame_ready(QImage)
    detections_changed(list)            # list[Detection]
    fps_changed(float)
    status_changed(dict)                # asdict(VisionStatus)

  methods
    start()                             # idempotent
    stop()
    set_face_enabled(bool)
    set_object_enabled(bool)
    set_resolution(width, height)
    snapshot() -> Path | None
    status() -> VisionStatus

Falls back gracefully when OpenCV / Ultralytics / a camera aren't
available - the screen reads `VisionStatus.message` and renders a
warn pill instead of pretending the system is healthy.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QImage

from sirena_ui.workers.vision_pipeline import VisionPipeline
from sirena_ui.workers.vision_types import Detection, VisionStatus


log = logging.getLogger("sirena_ui.vision.worker")


# Target capture/inference rate. The pipeline can drop below this if
# inference is slow (Nano + TRT object detection runs at ~10 FPS),
# but capping the loop here keeps the GUI thread from being flooded
# on faster hosts.
_TARGET_FPS = 20.0


class VisionWorker(QObject):
    frame_ready = pyqtSignal(QImage)
    detections_changed = pyqtSignal(list)
    fps_changed = pyqtSignal(float)
    status_changed = pyqtSignal(dict)

    def __init__(
        self,
        snapshots_dir: Optional[Path] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._pipeline = VisionPipeline()
        self._snapshots_dir = (
            Path(snapshots_dir).expanduser()
            if snapshots_dir is not None
            else Path.home() / "Pictures" / "nina-snapshots"
        )

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # Commands the worker thread executes between frames. Keep
        # bounded so a runaway producer can't balloon memory.
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=32)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spin up the worker thread. The thread always runs while the
        screen is active; opening the camera + lazy detector init are
        scheduled as commands so we never block the caller."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="VisionWorker",
                daemon=True,
            )
            self._thread.start()
        # First job: open the camera (may surface "OpenCV missing" or
        # "no /dev/video0" via status_changed without raising).
        self._enqueue(self._cmd_open_camera)

    def stop(self) -> None:
        thread: Optional[threading.Thread]
        with self._lock:
            self._stop_evt.set()
            thread = self._thread
            self._thread = None
        # Wake the worker if it's blocked on the queue.
        try:
            self._cmd_queue.put_nowait(self._cmd_noop)
        except queue.Full:
            pass
        if thread is not None:
            thread.join(timeout=2.0)
        self._pipeline.close()
        self._emit_status(self._pipeline.status())

    def shutdown(self) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Pass-through controls (all dispatched onto the worker thread)
    # ------------------------------------------------------------------

    def set_face_enabled(self, enabled: bool) -> None:
        self._enqueue(lambda: self._cmd_set_face(bool(enabled)))

    def set_object_enabled(self, enabled: bool) -> None:
        self._enqueue(lambda: self._cmd_set_object(bool(enabled)))

    def set_resolution(self, width: int, height: int) -> None:
        w, h = int(width), int(height)
        self._enqueue(lambda: self._pipeline.set_resolution(w, h))

    def snapshot(self) -> Optional[Path]:
        """Snapshot is read-only against the latest annotated frame
        held in the pipeline, so it's safe to run on the calling
        thread."""
        return self._pipeline.snapshot(self._snapshots_dir)

    def status(self) -> VisionStatus:
        return self._pipeline.status()

    # ------------------------------------------------------------------
    # Worker loop + command handlers
    # ------------------------------------------------------------------

    def _run(self) -> None:
        period = 1.0 / _TARGET_FPS
        ema_dt = period
        last_fps_emit = 0.0

        while not self._stop_evt.is_set():
            self._drain_commands()
            if self._stop_evt.is_set():
                break

            t0 = time.perf_counter()
            try:
                frame, detections = self._pipeline.step()
            except Exception as exc:
                log.exception("VisionPipeline.step raised: %s", exc)
                frame, detections = None, []

            if frame is None:
                # Camera not open or failed read - park until either a
                # new command lands or a short retry timeout elapses.
                self._wait_for_command(0.1)
                continue

            self._publish_frame(frame, detections)

            dt = time.perf_counter() - t0
            ema_dt = 0.85 * ema_dt + 0.15 * max(dt, 1e-3)

            now = time.perf_counter()
            if now - last_fps_emit > 0.5:
                self.fps_changed.emit(1.0 / ema_dt if ema_dt > 0 else 0.0)
                last_fps_emit = now

            sleep_for = period - dt
            if sleep_for > 0 and self._stop_evt.wait(sleep_for):
                break

    # ---- command dispatch helpers --------------------------------------

    def _enqueue(self, fn: Callable[[], None]) -> None:
        try:
            self._cmd_queue.put_nowait(fn)
        except queue.Full:
            log.warning("Vision command queue is full; dropping a command")

    def _drain_commands(self) -> None:
        while True:
            try:
                fn = self._cmd_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as exc:
                log.exception("Vision command failed: %s", exc)

    def _wait_for_command(self, timeout: float) -> None:
        try:
            fn = self._cmd_queue.get(timeout=timeout)
        except queue.Empty:
            return
        try:
            fn()
        except Exception as exc:
            log.exception("Vision command failed: %s", exc)

    # ---- commands (always run on the worker thread) ----------------

    def _cmd_noop(self) -> None:
        pass

    def _cmd_open_camera(self) -> None:
        status = self._pipeline.open()
        self._emit_status(status)

    def _cmd_set_face(self, enabled: bool) -> None:
        # Show a transient "loading" pill on enable so the user knows
        # the model is warming up (YuNet is fast, but we keep the
        # pattern symmetric with object detection).
        if enabled and not self._pipeline.status().face_ready:
            self._announce("Loading face detector...")
        self._pipeline.set_face_enabled(enabled)
        self._emit_status(self._pipeline.status())

    def _cmd_set_object(self, enabled: bool) -> None:
        # YOLO + TRT export can take minutes the first time on Jetson;
        # surface the wait so the user doesn't think the screen is
        # frozen.
        if enabled and not self._pipeline.status().object_ready:
            self._announce(
                "Loading object detector (first run may take a few minutes)..."
            )
        self._pipeline.set_object_enabled(enabled)
        self._emit_status(self._pipeline.status())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _publish_frame(self, frame_bgr, detections: List[Detection]) -> None:
        qimg = self._bgr_to_qimage(frame_bgr)
        if qimg is not None:
            self.frame_ready.emit(qimg)
        # Emit a stable Python list so Qt's queued connection deep-copies
        # via reference (not memoryview pointing into a dying ndarray).
        self.detections_changed.emit(list(detections))

    @staticmethod
    def _bgr_to_qimage(frame_bgr) -> Optional[QImage]:
        try:
            h, w = frame_bgr.shape[:2]
            channels = frame_bgr.shape[2] if frame_bgr.ndim == 3 else 1
        except Exception:
            return None
        if channels != 3:
            return None
        bytes_per_line = 3 * w
        # `Format_BGR888` available since Qt 5.14; we copy() so the
        # underlying buffer outlives this method call (frame_bgr is
        # owned by the worker thread / cv2 capture).
        return QImage(
            bytes(frame_bgr.data),
            w,
            h,
            bytes_per_line,
            QImage.Format_BGR888,
        ).copy()

    def _announce(self, message: str) -> None:
        """Push a transient status message via the same signal the
        screen subscribes to, without touching `pipeline.status()`."""
        snapshot = self._pipeline.status()
        self.status_changed.emit(
            {
                "camera_open": snapshot.camera_open,
                "face_ready": snapshot.face_ready,
                "object_ready": snapshot.object_ready,
                "message": message,
            }
        )

    def _emit_status(self, status: VisionStatus) -> None:
        self.status_changed.emit(asdict(status))
