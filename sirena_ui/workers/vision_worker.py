"""
Qt facade over `VisionPipeline`.

Owns a dedicated worker thread that pumps the camera + vision pipeline
at the configured target rate.  **Preview latency:** after each frame
grab we push a downscaled `QImage` immediately (overlaying the *previous*
frame's detections), then run face/object inference and refresh the
preview again with fresh boxes. That way the RGB stream stays fluid even
when YuNet + YOLO make the inference half of the loop slow.

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

GUI previews use a downscaled frame (``NINA_VISION_PREVIEW_MAX_W``) and
the Qt ``QTimer`` emits at most once per ``NINA_VISION_PREVIEW_MS``
whenever the latest preview buffer changed (avoids queued backlog of
full-size ``QImage`` values on the GUI thread).
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage

from sirena_ui.workers.vision_pipeline import EnrollmentResult, VisionPipeline
from sirena_ui.workers.vision_types import Detection, VisionStatus


log = logging.getLogger("sirena_ui.vision.worker")


# Target loop rate (read + infer). Inference dominates on Jetson when
# both detectors are on; when inference is faster than this, we sleep
# to avoid pointless CPU spin.
_TARGET_FPS = float(os.environ.get("NINA_VISION_TARGET_FPS", "30"))

# Preview path: downscale + coalesce emits so the GUI is not handed a
# QueuedConnection backlog of full-size QImages (major latency source).
_PREVIEW_MAX_W = int(os.environ.get("NINA_VISION_PREVIEW_MAX_W", "640"))
_PREVIEW_TIMER_MS = max(16, int(os.environ.get("NINA_VISION_PREVIEW_MS", "20")))


class VisionWorker(QObject):
    frame_ready = pyqtSignal(QImage)
    detections_changed = pyqtSignal(list)
    fps_changed = pyqtSignal(float)
    status_changed = pyqtSignal(dict)
    # Fired once per detection cycle with the set of recognised names
    # in the current frame. The screen uses this to drive the greeting
    # cooldown (no need to greet "hari" every frame).
    faces_recognized = pyqtSignal(list)
    # Lifecycle for an enrollment session triggered via `enroll_face`.
    enrollment_progress = pyqtSignal(int, int)  # samples, target
    enrollment_finished = pyqtSignal(dict)      # asdict(EnrollmentResult)
    # Emitted when toggling a detector ON failed to initialise its
    # model (ultralytics missing, ONNX download failed, etc). The
    # screen uses these to pop an explanatory dialog AND bounce the
    # corresponding toggle back to OFF so it doesn't lie about state.
    face_enable_failed = pyqtSignal(str)
    object_enable_failed = pyqtSignal(str)

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

        # Reference count for screens that want the camera to stay open
        # across navigation (Drive screen Front-camera card + Vision
        # screen + Perception screen). Without this, navigating
        # Vision -> Drive would call Vision.on_leave -> stop() and the
        # Drive screen's live preview would go black even though Drive
        # had already requested the feed. acquire()/release() do the
        # right refcount + start/stop bookkeeping; legacy callers that
        # used start()/stop() directly still work, they just bypass
        # the refcount (intentional - they're for tests / one-shot
        # tools that own the worker exclusively).
        self._refcount = 0
        self._refcount_lock = threading.Lock()

        self._preview_lock = threading.Lock()
        self._preview_image: Optional[QImage] = None
        self._preview_serial = 0
        self._preview_last_sent = -1
        self._preview_timer = QTimer(self)
        self._preview_timer.setTimerType(Qt.PreciseTimer)
        self._preview_timer.timeout.connect(self._emit_preview_if_new)

        # Detections drawn on the leading (pre-inference) preview — last
        # completed frame's boxes until fresh inference lands.
        self._stale_preview_detections: List[Detection] = []

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
            with self._preview_lock:
                self._preview_image = None
                self._preview_serial = 0
            self._preview_last_sent = -1
            self._stale_preview_detections = []
            self._thread = threading.Thread(
                target=self._run,
                name="VisionWorker",
                daemon=True,
            )
            self._thread.start()
        self._preview_timer.start(_PREVIEW_TIMER_MS)
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
        self._preview_timer.stop()
        with self._preview_lock:
            self._preview_image = None
            self._preview_serial = 0
        self._preview_last_sent = -1
        self._stale_preview_detections = []
        self._pipeline.close()
        self._emit_status(self._pipeline.status())

    def shutdown(self) -> None:
        # Force everything down regardless of refcount - app shutdown
        # path; nobody is going to call release() to balance.
        with self._refcount_lock:
            self._refcount = 0
        self.stop()

    def acquire(self) -> None:
        """Take a reference on the camera + worker thread, starting it
        if this was the first reference.

        Use this from screens that want the camera live across
        navigation (Drive screen, Vision screen, Perception screen).
        Pair every acquire() with exactly one release(). Calling
        start() directly bypasses the refcount and will cause the next
        release() from another holder to stop the worker out from
        under you.
        """
        with self._refcount_lock:
            self._refcount += 1
            should_start = self._refcount == 1
        if should_start:
            self.start()

    def release(self) -> None:
        """Drop a reference; stop the worker on the last release.

        No-op (and does NOT underflow) when called more times than
        acquire() so a screen that gets shutdown twice doesn't
        accidentally stop the camera for everyone else.
        """
        with self._refcount_lock:
            if self._refcount <= 0:
                return
            self._refcount -= 1
            should_stop = self._refcount == 0
        if should_stop:
            self.stop()

    # ------------------------------------------------------------------
    # Pass-through controls (all dispatched onto the worker thread)
    # ------------------------------------------------------------------

    def set_face_enabled(self, enabled: bool) -> None:
        self._enqueue(lambda: self._cmd_set_face(bool(enabled)))

    def set_object_enabled(self, enabled: bool) -> None:
        self._enqueue(lambda: self._cmd_set_object(bool(enabled)))

    def set_object_confidence(self, value: float) -> None:
        """Update YOLO's confidence floor live.

        Pipeline-side setter is cheap (no model rebuild), so we run it
        synchronously instead of going through the command queue --
        a slider that drags from 70% to 90% would otherwise lag a
        frame behind the cursor.
        """
        self._pipeline.set_object_confidence(float(value))

    def get_object_confidence(self) -> float:
        return self._pipeline.get_object_confidence()

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

    def capture_dimensions(self) -> Tuple[int, int]:
        return self._pipeline.capture_dimensions()

    def enroll_face(self, name: str, target_samples: int = 8) -> None:
        """Capture face samples for `name` and add to the FaceDB.

        Runs on the worker thread so we can grab consecutive frames
        without fighting the live capture loop. Progress + outcome are
        published via `enrollment_progress` / `enrollment_finished`.
        """
        self._enqueue(lambda: self._cmd_enroll(name, int(target_samples)))

    def list_faces(self) -> List[str]:
        return self._pipeline.list_enrolled_faces()

    def remove_face(self, name: str) -> bool:
        return self._pipeline.remove_enrolled_face(name)

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
                frame = self._pipeline.read_frame()
            except Exception as exc:
                log.exception("VisionPipeline.read_frame raised: %s", exc)
                frame = None

            if frame is None:
                # Camera not open or failed read - park until either a
                # new command lands or a short retry timeout elapses.
                self._wait_for_command(0.1)
                continue

            # Leading preview: show this frame immediately with the last
            # known detections so motion stays fluid while inference runs.
            try:
                lead = VisionPipeline._annotate(frame, self._stale_preview_detections)
                self._refresh_preview_only(lead)
            except Exception:
                log.exception("vision lead preview failed")
                self._refresh_preview_only(frame)

            try:
                annotated, detections = self._pipeline.infer_and_annotate(frame)
            except Exception as exc:
                log.exception("VisionPipeline.infer_and_annotate raised: %s", exc)
                annotated = frame
                detections = []

            self._stale_preview_detections = list(detections)
            self._publish_frame(annotated, detections)

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
        err = self._pipeline.set_face_enabled(enabled)
        self._emit_status(self._pipeline.status())
        if err and enabled:
            self.face_enable_failed.emit(err)

    def _cmd_set_object(self, enabled: bool) -> None:
        # YOLO + TRT export can take minutes the first time on Jetson;
        # surface the wait so the user doesn't think the screen is
        # frozen.
        if enabled and not self._pipeline.status().object_ready:
            self._announce(
                "Loading object detector (first run may take a few minutes)..."
            )
        err = self._pipeline.set_object_enabled(enabled)
        self._emit_status(self._pipeline.status())
        if err and enabled:
            self.object_enable_failed.emit(err)

    def _cmd_enroll(self, name: str, target_samples: int) -> None:
        self._announce(f"Capturing face samples for '{name}'...")
        # Force face detection on so the operator doesn't need to
        # toggle it manually before clicking Train. SFace lazy-init
        # rides along inside set_face_enabled.
        self._pipeline.set_face_enabled(True)
        self.enrollment_progress.emit(0, int(target_samples))

        def _on_progress(captured: int, target: int) -> None:
            self.enrollment_progress.emit(int(captured), int(target))

        result: EnrollmentResult = self._pipeline.enroll_face(
            name,
            target_samples=int(target_samples),
            progress_cb=_on_progress,
        )
        # Surface result both as a structured payload (for the dialog)
        # and as a fresh status pill so the operator sees feedback even
        # if the dialog has already been dismissed.
        payload = {
            "ok": bool(result.ok),
            "samples": int(result.samples),
            "attempts": int(result.attempts),
            "message": str(result.message),
            "name": str(name),
        }
        self.enrollment_finished.emit(payload)
        self._announce(result.message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_preview_if_new(self) -> None:
        with self._preview_lock:
            serial = self._preview_serial
            img = self._preview_image
        if serial == self._preview_last_sent or img is None:
            return
        self._preview_last_sent = serial
        self.frame_ready.emit(img)

    def _refresh_preview_only(self, frame_bgr) -> None:
        """Push a new downscaled ``QImage`` to the preview buffer (no signals)."""
        preview_bgr = self._maybe_downscale_for_preview(frame_bgr)
        qimg = self._bgr_to_qimage(preview_bgr)
        if qimg is not None:
            with self._preview_lock:
                self._preview_image = qimg
                self._preview_serial += 1

    def _publish_frame(self, frame_bgr, detections: List[Detection]) -> None:
        self._refresh_preview_only(frame_bgr)
        # Emit a stable Python list so Qt's queued connection deep-copies
        # via reference (not memoryview pointing into a dying ndarray).
        self.detections_changed.emit(list(detections))
        # Names of any recognised people in this frame. We dedupe so
        # multiple faces of the same enrolled person (rare but
        # possible if YuNet double-detects) don't double-trigger
        # downstream greeters.
        recognised: List[str] = []
        seen = set()
        for det in detections:
            if det.identity and det.identity not in seen:
                seen.add(det.identity)
                recognised.append(det.identity)
        if recognised:
            self.faces_recognized.emit(recognised)

    @staticmethod
    def _maybe_downscale_for_preview(frame_bgr):
        if _PREVIEW_MAX_W <= 0:
            return frame_bgr
        try:
            import cv2

            h, w = frame_bgr.shape[:2]
        except Exception:
            return frame_bgr
        if w <= _PREVIEW_MAX_W:
            return frame_bgr
        nh = max(1, int(round(h * (_PREVIEW_MAX_W / float(w)))))
        return cv2.resize(
            frame_bgr, (_PREVIEW_MAX_W, nh), interpolation=cv2.INTER_AREA
        )

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
