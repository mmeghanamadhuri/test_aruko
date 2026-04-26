"""
Nina vision pipeline: USB camera + face & object detection.

Models (all run locally on the Jetson, GPU when available):

  * Face detection - YuNet via cv2.FaceDetectorYN.
    Built into OpenCV >= 4.5.4. The ONNX model is ~340 KB and is
    cached under nina/models/weights/ on first use.

  * Object detection - Ultralytics YOLOv8n (COCO 80 classes).
    On Jetson we auto-export to a TensorRT FP16 engine on first run
    (NINA_VISION_TRT=1 by default when CUDA is detected) so inference
    runs on the GPU; on dev hosts we fall back to a CPU PyTorch run
    so the screen still works without a GPU.

The pipeline is plain Python; the Qt-side `VisionWorker` runs
`step()` on a worker thread and forwards results to the GUI via
signals.

If OpenCV / Ultralytics aren't installed, or the camera can't be
opened, every public call short-circuits and the surfaced
`VisionStatus.message` explains why - the Vision screen renders that
verbatim so the operator knows the difference between "camera
unplugged" and "ultralytics not installed".
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from sirena_ui.workers.vision_types import (
    KIND_FACE,
    KIND_OBJECT,
    Detection,
    VisionStatus,
)


log = logging.getLogger("sirena_ui.vision")


# ----------------------------------------------------------------------
# Optional deps - imported lazily so dev hosts without cv2/ultralytics
# can still launch the GUI; the Vision screen will just render a
# "Vision unavailable" pill explaining the missing dependency.
# ----------------------------------------------------------------------

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is an indirect dep of cv2
    np = None  # type: ignore[assignment]

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore[assignment]


# YuNet ONNX (MIT-licensed) from the OpenCV model zoo. The 2023-Mar
# revision is the smallest current release; ~230 KB. We point at
# media.githubusercontent.com because the model is stored in Git LFS:
# the regular `raw.githubusercontent.com` URL would hand back a tiny
# LFS pointer file instead of the binary.
_YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"

# Default object-detection weights. Ultralytics auto-downloads on
# first use; we pin the smallest variant so the cold-start cost is
# manageable on a Nano (TRT export still takes ~3 minutes the first
# time, but only once per host).
_DEFAULT_YOLO_WEIGHTS = "yolov8n.pt"


def _models_root() -> Path:
    """Where to cache vision model weights/engines."""
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    return repo_root / "nina" / "models" / "weights"


# ======================================================================
# Face detector (YuNet)
# ======================================================================


class _YuNetFaceDetector:
    """Tiny wrapper around `cv2.FaceDetectorYN`.

    YuNet expects RGB / BGR uint8 frames and returns (N, 15) float
    arrays where each row is [x, y, w, h, lm_x0, lm_y0, ..., score].
    We only forward the bbox + score - the screen doesn't draw
    landmarks today.
    """

    def __init__(self, model_path: Path, score_threshold: float = 0.7) -> None:
        if cv2 is None:
            raise RuntimeError(
                "OpenCV is required for face detection. "
                "Install with: pip install opencv-python-headless"
            )
        if not hasattr(cv2, "FaceDetectorYN_create"):
            raise RuntimeError(
                "cv2.FaceDetectorYN_create is missing. Upgrade OpenCV to "
                ">= 4.5.4 (pip install -U opencv-python-headless)."
            )
        self._model_path = model_path
        self._score_threshold = float(score_threshold)
        self._detector = cv2.FaceDetectorYN_create(
            str(model_path),
            "",
            (320, 320),
            self._score_threshold,
            0.3,
            5000,
        )
        self._last_size: Optional[Tuple[int, int]] = None

    def detect(self, frame_bgr) -> List[Detection]:
        h, w = frame_bgr.shape[:2]
        if self._last_size != (w, h):
            self._detector.setInputSize((w, h))
            self._last_size = (w, h)
        _, faces = self._detector.detect(frame_bgr)
        if faces is None:
            return []
        out: List[Detection] = []
        for row in faces:
            x, y, fw, fh = row[0:4]
            score = float(row[-1])
            x1 = max(0, int(x))
            y1 = max(0, int(y))
            x2 = min(w, int(x + fw))
            y2 = min(h, int(y + fh))
            if x2 <= x1 or y2 <= y1:
                continue
            out.append(
                Detection(
                    kind=KIND_FACE,
                    label="face",
                    confidence=score,
                    bbox=(x1, y1, x2, y2),
                )
            )
        return out


def _ensure_yunet_model() -> Path:
    """Download the YuNet ONNX once and cache it under nina/models/weights."""
    cache_dir = _models_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / _YUNET_FILENAME
    if target.exists() and target.stat().st_size > 0:
        return target
    log.info("Downloading YuNet face model -> %s", target)
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        urllib.request.urlretrieve(_YUNET_URL, tmp)
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


# ======================================================================
# Object detector (Ultralytics YOLOv8n, optional TensorRT engine)
# ======================================================================


class _YoloObjectDetector:
    """Ultralytics YOLOv8n with optional TensorRT acceleration.

    On Jetson we attempt to load (or export) a TensorRT engine so
    inference runs on the GPU at ~10-30 FPS depending on the board;
    everywhere else we fall back to PyTorch (CPU on dev hosts, CUDA
    on workstations) so the screen still functions.
    """

    def __init__(
        self,
        weights_path: Path,
        confidence: float = 0.4,
        prefer_tensorrt: bool = True,
    ) -> None:
        try:
            from ultralytics import YOLO  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is required for object detection. "
                "Install with: pip install ultralytics"
            ) from exc

        self._YOLO = YOLO
        self._weights_path = weights_path
        self._confidence = float(confidence)
        self._engine_path: Optional[Path] = None

        # First, try to load (or build) a TensorRT engine. If anything
        # along the path fails (no CUDA, no torch, export error...), we
        # silently fall back to the PyTorch model.
        self._model = None
        if prefer_tensorrt and self._cuda_available():
            try:
                self._engine_path = self._ensure_engine(weights_path)
                self._model = YOLO(str(self._engine_path), task="detect")
                log.info("Object detector: TensorRT engine %s", self._engine_path)
            except Exception as exc:
                log.warning("TensorRT path unavailable (%s); using PyTorch", exc)
                self._model = None

        if self._model is None:
            self._model = YOLO(str(weights_path))
            log.info("Object detector: PyTorch %s", weights_path)

        # Cache class-name map up-front so we don't pay per-call.
        try:
            base = YOLO(str(weights_path), task="detect")
            self._names = {int(k): str(v) for k, v in (base.names or {}).items()}
        except Exception:
            self._names = {}

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import torch  # type: ignore

            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _ensure_engine(self, weights_path: Path) -> Path:
        """Return the path to a TensorRT engine, exporting from .pt if needed."""
        engine_path = weights_path.with_suffix(".engine")
        if engine_path.exists() and engine_path.stat().st_size > 0:
            return engine_path
        log.info(
            "Exporting TensorRT engine for %s (FP16) - this can take a few "
            "minutes on Jetson Nano, only happens once",
            weights_path,
        )
        pt = self._YOLO(str(weights_path))
        result = pt.export(format="engine", half=True)
        if isinstance(result, str) and result.strip():
            return Path(result.strip())
        if engine_path.exists():
            return engine_path
        raise RuntimeError("YOLO export(format='engine') did not produce a file")

    def detect(self, frame_bgr) -> List[Detection]:
        try:
            results = self._model.predict(
                frame_bgr,
                verbose=False,
                conf=self._confidence,
            )
        except Exception as exc:
            log.warning("YOLO predict failed: %s", exc)
            return []
        if not results:
            return []
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        names = result.names or self._names
        out: List[Detection] = []
        for i in range(len(boxes)):
            box = boxes[i]
            xyxy = box.xyxy[0].cpu().numpy().tolist()
            x1, y1, x2, y2 = (int(round(v)) for v in xyxy)
            conf = float(box.conf[0].cpu().numpy())
            cls_id = int(box.cls[0].cpu().numpy())
            label = str(names.get(cls_id, f"class_{cls_id}"))
            out.append(
                Detection(
                    kind=KIND_OBJECT,
                    label=label,
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                )
            )
        return out


def _resolve_yolo_weights() -> Path:
    """Find or seed the YOLOv8n weights. Ultralytics will auto-download
    the file on first instantiation if it isn't already on disk."""
    cache_dir = _models_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    override = os.environ.get("NINA_VISION_YOLO_WEIGHTS", "").strip()
    if override:
        return Path(override).expanduser()
    return cache_dir / _DEFAULT_YOLO_WEIGHTS


# ======================================================================
# Pipeline
# ======================================================================


# Default annotation colours (BGR for OpenCV).
_FACE_COLOR = (52, 199, 89)     # Sirena-friendly green
_OBJECT_COLOR = (10, 132, 255)  # Sirena-friendly blue


class VisionPipeline:
    """Camera + detector orchestration.

    Public entry points:
      * `open()`            - opens the USB camera + lazily preps detectors.
      * `step()`            - read one frame, run enabled detectors,
                              return (annotated_bgr, list[Detection]).
      * `set_face_enabled` / `set_object_enabled`
      * `set_resolution(width, height)`
      * `snapshot(path)`    - dump the latest annotated frame to disk.
      * `close()`           - releases the camera.
      * `status()`          - VisionStatus snapshot (camera + per-model
                              readiness + a human-readable failure
                              reason).
    """

    def __init__(
        self,
        *,
        camera_index: Optional[int] = None,
        width: int = 640,
        height: int = 480,
        face_score_threshold: float = 0.7,
        object_confidence: float = 0.4,
        prefer_tensorrt: Optional[bool] = None,
    ) -> None:
        self._camera_index = (
            int(os.environ.get("NINA_VISION_CAMERA", "0"))
            if camera_index is None
            else int(camera_index)
        )
        self._width = int(width)
        self._height = int(height)
        self._face_threshold = float(face_score_threshold)
        self._object_confidence = float(object_confidence)
        self._prefer_trt = (
            self._env_bool("NINA_VISION_TRT", True)
            if prefer_tensorrt is None
            else bool(prefer_tensorrt)
        )

        self._cap = None
        self._face: Optional[_YuNetFaceDetector] = None
        self._object: Optional[_YoloObjectDetector] = None

        self._face_enabled = False
        self._object_enabled = False

        self._lock = threading.RLock()
        self._last_frame = None  # most recent annotated BGR frame
        self._status = VisionStatus(message="Vision idle")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> VisionStatus:
        with self._lock:
            if cv2 is None:
                self._status = VisionStatus(
                    message="OpenCV not installed - pip install opencv-python-headless",
                )
                return self._status
            if self._cap is None:
                cap = cv2.VideoCapture(self._camera_index)
                if not cap.isOpened():
                    self._status = VisionStatus(
                        message=f"Camera /dev/video{self._camera_index} not found",
                    )
                    cap.release()
                    return self._status
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))
                self._cap = cap
            self._status = VisionStatus(camera_open=True, message="Camera ready")
            return self._status

    def close(self) -> None:
        with self._lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            self._status = VisionStatus(message="Camera closed")

    def set_resolution(self, width: int, height: int) -> None:
        with self._lock:
            self._width = int(width)
            self._height = int(height)
            if self._cap is not None and cv2 is not None:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))

    # ------------------------------------------------------------------
    # Detector toggles
    # ------------------------------------------------------------------

    def set_face_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled and self._face is None:
                try:
                    model_path = _ensure_yunet_model()
                    self._face = _YuNetFaceDetector(
                        model_path,
                        score_threshold=self._face_threshold,
                    )
                    self._status.face_ready = True
                except Exception as exc:
                    log.warning("Face detector init failed: %s", exc)
                    self._status.message = f"Face: {exc}"
                    self._face_enabled = False
                    self._status.face_ready = False
                    return
            self._face_enabled = bool(enabled)

    def set_object_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled and self._object is None:
                try:
                    weights = _resolve_yolo_weights()
                    self._object = _YoloObjectDetector(
                        weights,
                        confidence=self._object_confidence,
                        prefer_tensorrt=self._prefer_trt,
                    )
                    self._status.object_ready = True
                except Exception as exc:
                    log.warning("Object detector init failed: %s", exc)
                    self._status.message = f"Object: {exc}"
                    self._object_enabled = False
                    self._status.object_ready = False
                    return
            self._object_enabled = bool(enabled)

    # ------------------------------------------------------------------
    # Frame loop
    # ------------------------------------------------------------------

    def step(self):
        """Pull one frame, optionally run the enabled detectors, and
        return `(annotated_bgr, [Detection])`. Returns `(None, [])` if
        the camera isn't available."""
        with self._lock:
            cap = self._cap
            face = self._face if self._face_enabled else None
            obj = self._object if self._object_enabled else None
        if cap is None or cv2 is None:
            return None, []
        ok, frame = cap.read()
        if not ok or frame is None:
            return None, []

        detections: List[Detection] = []
        if face is not None:
            try:
                detections.extend(face.detect(frame))
            except Exception as exc:
                log.warning("face.detect failed: %s", exc)
        if obj is not None:
            try:
                detections.extend(obj.detect(frame))
            except Exception as exc:
                log.warning("object.detect failed: %s", exc)

        annotated = self._annotate(frame, detections)
        with self._lock:
            self._last_frame = annotated
        return annotated, detections

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def snapshot(self, dest_dir: Path) -> Optional[Path]:
        with self._lock:
            frame = self._last_frame
        if frame is None or cv2 is None:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"snapshot-{int(time.time())}.jpg"
        ok = cv2.imwrite(str(out), frame)
        return out if ok else None

    def status(self) -> VisionStatus:
        with self._lock:
            return VisionStatus(
                camera_open=self._status.camera_open,
                face_ready=self._status.face_ready,
                object_ready=self._status.object_ready,
                message=self._status.message,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on", "y")

    @staticmethod
    def _annotate(frame_bgr, detections: List[Detection]):
        if cv2 is None or not detections:
            return frame_bgr
        out = frame_bgr.copy()
        for det in detections:
            color = _FACE_COLOR if det.kind == KIND_FACE else _OBJECT_COLOR
            x1, y1, x2, y2 = det.bbox
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            label = f"{det.label} {int(round(det.confidence * 100))}%"
            (tw, th), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
            )
            cv2.rectangle(
                out,
                (x1, max(0, y1 - th - 6)),
                (x1 + tw + 6, y1),
                color,
                -1,
            )
            cv2.putText(
                out,
                label,
                (x1 + 3, max(th, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return out
