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
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from nina.services.face_db import FaceDB
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

# SFace ONNX (MIT-licensed) from the OpenCV model zoo. This is the
# recommended pairing with YuNet for face *recognition*; YuNet returns
# the 5 facial landmarks SFace's `alignCrop` consumes. ~38 MB.
_SFACE_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
_SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"

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


@dataclass
class _FaceHit:
    """One YuNet detection plus the raw row needed for SFace alignment."""

    detection: Detection
    row: object  # numpy ndarray shape (15,) - x,y,w,h, 5 landmarks, score


@dataclass
class EnrollmentResult:
    """Outcome of a face-enrollment session, surfaced to the GUI."""

    ok: bool
    samples: int
    attempts: int
    message: str


class _YuNetFaceDetector:
    """Tiny wrapper around `cv2.FaceDetectorYN`.

    YuNet expects RGB / BGR uint8 frames and returns (N, 15) float
    arrays where each row is [x, y, w, h, lm_x0, lm_y0, ..., score].
    We forward the bbox + score for the UI and keep the raw row so
    SFace's `alignCrop` can use the 5 facial landmarks for
    recognition.
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

    def detect(self, frame_bgr) -> List[_FaceHit]:
        h, w = frame_bgr.shape[:2]
        if self._last_size != (w, h):
            self._detector.setInputSize((w, h))
            self._last_size = (w, h)
        _, faces = self._detector.detect(frame_bgr)
        if faces is None:
            return []
        out: List[_FaceHit] = []
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
                _FaceHit(
                    detection=Detection(
                        kind=KIND_FACE,
                        label="face",
                        confidence=score,
                        bbox=(x1, y1, x2, y2),
                    ),
                    row=row,
                )
            )
        return out


# ======================================================================
# Face recognizer (SFace)
# ======================================================================


class _SFaceRecognizer:
    """Wrapper around `cv2.FaceRecognizerSF`.

    SFace produces a 128-dim feature vector per face. We keep the raw
    output (we L2-normalize inside `FaceDB.upsert`) and rely on
    `FaceDB.find_best_match` for cosine matching against the enrolled
    embeddings.

    Alignment relies on the 5 facial landmarks YuNet returns; passing
    the raw YuNet row to `alignCrop` is the documented happy path.
    """

    def __init__(self, model_path: Path) -> None:
        if cv2 is None:
            raise RuntimeError(
                "OpenCV is required for face recognition."
            )
        if not hasattr(cv2, "FaceRecognizerSF_create"):
            raise RuntimeError(
                "cv2.FaceRecognizerSF_create is missing. Upgrade OpenCV "
                "to >= 4.5.4 (pip install -U opencv-python-headless)."
            )
        # Empty 'config' string + default backend/target -> CPU ONNX.
        self._recognizer = cv2.FaceRecognizerSF_create(str(model_path), "")

    def embed(self, frame_bgr, yunet_row) -> Optional[List[float]]:
        """Return a 128-dim Python list (CPU-friendly) for one face,
        or None if alignment fails."""
        try:
            aligned = self._recognizer.alignCrop(frame_bgr, yunet_row)
            feature = self._recognizer.feature(aligned)
        except Exception as exc:
            log.warning("SFace embed failed: %s", exc)
            return None
        if feature is None:
            return None
        try:
            flat = feature.flatten().tolist()
        except Exception:
            flat = list(feature)
        return [float(v) for v in flat]


def _ensure_sface_model() -> Path:
    """Download the SFace ONNX once (~38 MB) and cache it under
    nina/models/weights."""
    cache_dir = _models_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / _SFACE_FILENAME
    if target.exists() and target.stat().st_size > 0:
        return target
    log.info("Downloading SFace recognition model -> %s", target)
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        urllib.request.urlretrieve(_SFACE_URL, tmp)
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


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


def _patch_torch_load_for_ultralytics() -> None:
    """PyTorch 2.6 flipped ``torch.load(..., weights_only=...)`` from
    ``False`` to ``True`` by default, which causes Ultralytics' YOLO
    checkpoint loading to fail with::

        WeightsUnpickler error: Unsupported global:
            GLOBAL ultralytics.nn.tasks.DetectionModel ...

    The yolov8n.pt we ship comes from the official Ultralytics CDN and
    is cached under ``nina/models/weights/``, so it's safe to load in
    full-pickle mode. We patch ``torch.load`` once, idempotently, to
    default ``weights_only=False`` if the caller hasn't requested
    otherwise. We also try ``add_safe_globals`` for the common
    Ultralytics classes, so callers that DO pass ``weights_only=True``
    keep working too.
    """
    try:
        import torch  # type: ignore
    except ImportError:
        return

    if not getattr(torch.load, "_nina_weights_only_compat", False):
        original_load = torch.load

        def _patched_load(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("weights_only", False)
            return original_load(*args, **kwargs)

        _patched_load._nina_weights_only_compat = True  # type: ignore[attr-defined]
        torch.load = _patched_load  # type: ignore[assignment]

    add_safe_globals = getattr(
        getattr(torch, "serialization", None), "add_safe_globals", None
    )
    if add_safe_globals is None:
        return  # PyTorch < 2.6, nothing else to do

    safe: list = []
    try:
        from ultralytics.nn import tasks as _tasks  # type: ignore

        for cls_name in (
            "DetectionModel",
            "SegmentationModel",
            "PoseModel",
            "ClassificationModel",
            "OBBModel",
        ):
            cls = getattr(_tasks, cls_name, None)
            if cls is not None:
                safe.append(cls)
    except Exception:
        pass
    try:
        from ultralytics.nn import modules as _mods  # type: ignore

        for cls_name in (
            "Conv",
            "C2f",
            "SPPF",
            "Bottleneck",
            "Concat",
            "Detect",
            "DFL",
        ):
            cls = getattr(_mods, cls_name, None)
            if cls is not None:
                safe.append(cls)
    except Exception:
        pass
    if safe:
        try:
            add_safe_globals(safe)
        except Exception:
            pass


class _YoloObjectDetector:
    """Ultralytics YOLOv8n with optional TensorRT acceleration.

    On Jetson we attempt to load (or export) a TensorRT engine so
    inference runs on the GPU at ~10-30 FPS depending on the board;
    everywhere else we fall back to PyTorch (CPU on dev hosts, CUDA
    on workstations) so the screen still functions.

    The default `confidence` is 0.8 (80%): on Nina we'd rather skip a
    weakly-detected box than draw a flicker of false positives. Drop
    it via the `NINA_VISION_OBJECT_CONF` env var or the
    `VisionPipeline.set_object_confidence()` runtime setter.
    """

    def __init__(
        self,
        weights_path: Path,
        confidence: float = 0.8,
        prefer_tensorrt: bool = True,
    ) -> None:
        _patch_torch_load_for_ultralytics()

        try:
            from ultralytics import YOLO  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is required for object detection. "
                "Use the same Python as nina-link, e.g.\n"
                "  python3 -m pip install ultralytics\n"
                "or install the full headless vision stack:\n"
                "  pip install -r sirena_ui/requirements-headless.txt\n"
                "(On Jetson install a JetPack-matching PyTorch wheel before ultralytics if needed.)"
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

    @property
    def confidence(self) -> float:
        return self._confidence

    def set_confidence(self, value: float) -> None:
        """Update the per-prediction confidence threshold.

        Ultralytics' YOLO consumes this each call, so it's safe to
        change live without re-loading the model.
        """
        self._confidence = max(0.0, min(1.0, float(value)))

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
        object_confidence: Optional[float] = None,
        prefer_tensorrt: Optional[bool] = None,
        face_db_path: Optional[Path] = None,
    ) -> None:
        self._camera_index = (
            int(os.environ.get("NINA_VISION_CAMERA", "0"))
            if camera_index is None
            else int(camera_index)
        )
        self._width = int(width)
        self._height = int(height)
        self._face_threshold = float(face_score_threshold)
        # Object-detection confidence floor. Default 0.80 keeps the
        # "Detected" rail and the bbox overlay tight - we'd rather
        # drop a marginal box than flicker false positives across the
        # GUI. Override at runtime via env var NINA_VISION_OBJECT_CONF
        # (0.0..1.0, e.g. 0.7 for slightly looser, 0.9 for stricter)
        # or programmatically via set_object_confidence().
        if object_confidence is None:
            try:
                object_confidence = float(
                    os.environ.get("NINA_VISION_OBJECT_CONF", "0.8")
                )
            except ValueError:
                object_confidence = 0.8
        self._object_confidence = max(0.0, min(1.0, float(object_confidence)))
        self._prefer_trt = (
            self._env_bool("NINA_VISION_TRT", True)
            if prefer_tensorrt is None
            else bool(prefer_tensorrt)
        )

        self._cap = None
        self._face: Optional[_YuNetFaceDetector] = None
        self._sface: Optional[_SFaceRecognizer] = None
        self._object: Optional[_YoloObjectDetector] = None

        self._face_enabled = False
        self._object_enabled = False

        # Face recognition DB. Persisted alongside the rest of Nina's
        # mutable state under nina/data/. Lazy: we don't load anything
        # until the user enables face detection or enrolls someone.
        if face_db_path is None:
            repo_root = Path(__file__).resolve().parents[2]
            face_db_path = repo_root / "nina" / "data" / "faces.json"
        self._face_db: FaceDB = FaceDB(face_db_path)

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
            if self._cap is not None:
                self._status = VisionStatus(camera_open=True, message="Camera ready")
                return self._status

            # Try the configured index first, then fall back to probing
            # the rest of /dev/video*. Jetson Orin enumerates ISP /
            # encoder nodes as low-numbered video devices even when no
            # real camera is plugged in, so the actual USB webcam often
            # lands at video1, video3, video5 etc. Operators previously
            # had to guess and set NINA_VISION_CAMERA by hand; now the
            # pipeline finds it.
            self._camera_index_initial = int(self._camera_index)
            tried: List[Tuple[int, str]] = []
            cap, picked, msg = self._try_open_index(self._camera_index, tried)

            if cap is None and self._auto_probe:
                for idx in self._candidate_indices():
                    if idx == self._camera_index:
                        continue
                    cap, picked, msg = self._try_open_index(idx, tried)
                    if cap is not None:
                        break

            if cap is None:
                self._status = VisionStatus(
                    message=self._format_open_failure(tried),
                )
                return self._status

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))
            self._cap = cap
            initial = int(self._camera_index_initial)
            self._camera_index = int(picked)
            if picked != initial:
                # Auto-probe found a different device than the operator
                # asked for - say so in the pill so the next-best
                # diagnostic step (set NINA_VISION_CAMERA={picked}
                # explicitly so we don't re-probe every launch) is
                # obvious.
                ready_msg = (
                    f"Camera ready on /dev/video{picked} "
                    f"(auto-probed; configured was video{initial})"
                )
            else:
                ready_msg = "Camera ready"
            self._status = VisionStatus(camera_open=True, message=ready_msg)
            return self._status

    # Auto-probe controls. Tests / one-off bringup may want to disable
    # the fallback to make a configured-index failure deterministic.
    @property
    def _auto_probe(self) -> bool:
        return self._env_bool("NINA_VISION_AUTO_PROBE", True)

    def _candidate_indices(self) -> List[int]:
        """Return candidate /dev/video* indices to probe, in priority
        order. Honours `NINA_VISION_CANDIDATES` (comma-separated) when
        set; otherwise falls back to the indices that actually exist
        as /dev/video* device files (so we don't waste 100 ms per
        bogus index on hosts where the camera is really at video8)."""
        raw = os.environ.get("NINA_VISION_CANDIDATES", "").strip()
        if raw:
            try:
                return [int(p) for p in raw.split(",") if p.strip()]
            except ValueError:
                log.warning("NINA_VISION_CANDIDATES not parseable: %r", raw)
        # Enumerate /dev/video* and sort numerically so the lowest
        # index wins ties (matches how V4L2 / cv2 traditionally
        # enumerate cameras).
        out: List[int] = []
        try:
            for name in os.listdir("/dev"):
                if not name.startswith("video"):
                    continue
                tail = name[len("video"):]
                if tail.isdigit():
                    out.append(int(tail))
        except OSError:
            pass
        out.sort()
        return out or list(range(0, 10))

    def _try_open_index(
        self, idx: int, tried: List[Tuple[int, str]]
    ) -> Tuple[Optional[object], int, str]:
        """Attempt to open `/dev/video{idx}` and grab one frame.

        Records the outcome in `tried` (a per-call audit trail used by
        the failure message). Returns the cv2 capture handle on
        success, None on failure.
        """
        path = f"/dev/video{idx}"
        if not os.path.exists(path):
            tried.append((idx, "no device"))
            return None, idx, "no device"
        if not os.access(path, os.R_OK | os.W_OK):
            # Most common cause: user not in the `video` group. Surface
            # the fix verbatim so the operator doesn't need to guess.
            tried.append(
                (idx, "no permission (try: sudo usermod -aG video $USER)")
            )
            return None, idx, "no permission"

        try:
            cap = cv2.VideoCapture(idx)
        except Exception as exc:  # pragma: no cover - cv2 rarely raises
            tried.append((idx, f"VideoCapture raised: {exc}"))
            return None, idx, str(exc)

        if not cap.isOpened():
            tried.append((idx, "isOpened()=False (driver rejected open)"))
            try:
                cap.release()
            except Exception:
                pass
            return None, idx, "not opened"

        # Open succeeded but on Jetson the ISP / encoder nodes
        # (video10..video13 typically) DO open via V4L2 and then fail
        # to deliver frames. Confirm the device actually streams a
        # frame before we accept this index, otherwise the GUI would
        # show "Camera ready" forever and serve a black viewport.
        ok, frame = cap.read()
        if not ok or frame is None:
            tried.append((idx, "opened but no frame (ISP / encoder node?)"))
            try:
                cap.release()
            except Exception:
                pass
            return None, idx, "no frame"

        return cap, idx, "ok"

    @staticmethod
    def _format_open_failure(tried: List[Tuple[int, str]]) -> str:
        """Compose the operator-facing pill text from the audit trail.

        Prioritises the most-actionable hint. Permission errors win
        over 'no device' because they're easy to fix; 'opened but no
        frame' wins over 'no device' because it tells the operator
        the bot saw a camera-shaped thing and rejected it."""
        if not tried:
            return "Camera not connected (no /dev/video* probed)"
        # Promote permission errors to the front - they're the single
        # most common cause of "the camera was just working".
        perm = [(i, m) for (i, m) in tried if "permission" in m]
        if perm:
            return (
                f"Camera /dev/video{perm[0][0]} not readable: "
                f"{perm[0][1]}"
            )
        no_frame = [(i, m) for (i, m) in tried if "no frame" in m]
        if no_frame:
            indices = ", ".join(f"video{i}" for i, _ in no_frame)
            return (
                f"Camera nodes opened but delivered no frames "
                f"({indices}); USB webcam not plugged in, or "
                f"another process is holding it"
            )
        # Default: show what we tried.
        attempted = ", ".join(f"video{i}({m})" for i, m in tried[:5])
        return f"Camera not connected. Tried: {attempted}"

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

    def set_face_enabled(self, enabled: bool) -> Optional[str]:
        """Toggle face detection. Returns None on success, or a
        human-readable error message if init fails."""
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
                    err_msg = f"{type(exc).__name__}: {exc}"
                    self._status.message = f"Face: {err_msg}"
                    self._face_enabled = False
                    self._status.face_ready = False
                    return err_msg
            # SFace is best-effort -- if the download / load fails, face
            # *detection* still works, the per-face label just stays
            # generic ("face") instead of carrying a name.
            if enabled and self._sface is None:
                try:
                    sface_path = _ensure_sface_model()
                    self._sface = _SFaceRecognizer(sface_path)
                except Exception as exc:
                    log.warning("Face recognizer unavailable: %s", exc)
                    self._sface = None
            self._face_enabled = bool(enabled)
            return None

    def get_object_confidence(self) -> float:
        """Current YOLO confidence floor (0.0..1.0)."""
        with self._lock:
            return float(self._object_confidence)

    def set_object_confidence(self, value: float) -> None:
        """Update the YOLO confidence threshold live.

        Pushes the new value into the running detector if one exists,
        so the next predict() call picks it up without rebuilding the
        TensorRT engine. Callers are free to call this from any
        thread; it's lock-protected.
        """
        clamped = max(0.0, min(1.0, float(value)))
        with self._lock:
            self._object_confidence = clamped
            if self._object is not None:
                try:
                    self._object.set_confidence(clamped)
                except Exception:
                    log.exception("Failed to push live confidence to YOLO")

    def set_object_enabled(self, enabled: bool) -> Optional[str]:
        """Toggle object detection. Returns None on success, or a
        human-readable error message if init fails (so the GUI can
        pop a dialog and bounce the toggle back to OFF).
        """
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
                    err_msg = f"{type(exc).__name__}: {exc}"
                    self._status.message = f"Object: {err_msg}"
                    self._object_enabled = False
                    self._status.object_ready = False
                    return err_msg
            self._object_enabled = bool(enabled)
            return None

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
            sface = self._sface if self._face_enabled else None
            obj = self._object if self._object_enabled else None
            face_db = self._face_db
        if cap is None or cv2 is None:
            return None, []
        ok, frame = cap.read()
        if not ok or frame is None:
            return None, []

        detections: List[Detection] = []
        if face is not None:
            try:
                hits = face.detect(frame)
            except Exception as exc:
                log.warning("face.detect failed: %s", exc)
                hits = []
            # Recognition is opt-in on success: if we have an SFace
            # recognizer AND at least one enrolled face, look up each
            # detected face's identity. Otherwise we just emit the bare
            # face detections (label "face") as before.
            do_recog = sface is not None and not face_db.is_empty()
            for hit in hits:
                if do_recog:
                    try:
                        emb = sface.embed(frame, hit.row)
                    except Exception as exc:
                        log.warning("face.embed failed: %s", exc)
                        emb = None
                    if emb is not None:
                        match = face_db.find_best_match(emb)
                        if match is not None:
                            name, score = match
                            hit.detection.identity = name
                            hit.detection.identity_score = score
                            hit.detection.label = name
                detections.append(hit.detection)
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
    # Face enrollment
    # ------------------------------------------------------------------

    def enroll_face(
        self,
        name: str,
        *,
        target_samples: int = 8,
        max_attempts: int = 80,
        min_confidence: float = 0.85,
        progress_cb=None,
    ) -> "EnrollmentResult":
        """Capture `target_samples` frames where exactly one face is
        visible, average their SFace embeddings, and persist the
        result under `name` in the FaceDB.

        Runs on the worker thread (we own `self._lock` only briefly to
        snapshot resources). The camera and detectors must already be
        ready -- the caller is expected to have set face detection on
        before triggering this.

        Returns an `EnrollmentResult` describing how it went so the GUI
        can show a friendly message regardless of success or failure.
        """
        name = (name or "").strip()
        if not name:
            return EnrollmentResult(
                ok=False,
                samples=0,
                attempts=0,
                message="Name cannot be empty.",
            )

        with self._lock:
            cap = self._cap
            face = self._face
            sface = self._sface
        if cv2 is None or cap is None:
            return EnrollmentResult(
                ok=False,
                samples=0,
                attempts=0,
                message="Camera not open.",
            )
        if face is None:
            return EnrollmentResult(
                ok=False,
                samples=0,
                attempts=0,
                message=(
                    "Face detection isn't initialised. Toggle "
                    "'Face detection' on first."
                ),
            )
        if sface is None:
            return EnrollmentResult(
                ok=False,
                samples=0,
                attempts=0,
                message=(
                    "Face recognition model unavailable. Make sure the "
                    "Jetson has internet for the first SFace download."
                ),
            )

        embeddings: List[List[float]] = []
        attempts = 0
        skipped_multiple = 0
        skipped_low_conf = 0
        skipped_no_face = 0

        while len(embeddings) < target_samples and attempts < max_attempts:
            attempts += 1
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue
            try:
                hits = face.detect(frame)
            except Exception as exc:
                log.warning("enroll: face.detect failed: %s", exc)
                hits = []

            if not hits:
                skipped_no_face += 1
                time.sleep(0.05)
                continue
            if len(hits) > 1:
                skipped_multiple += 1
                time.sleep(0.05)
                continue
            hit = hits[0]
            if hit.detection.confidence < float(min_confidence):
                skipped_low_conf += 1
                time.sleep(0.05)
                continue
            emb = sface.embed(frame, hit.row)
            if emb is None:
                time.sleep(0.05)
                continue
            embeddings.append(emb)
            if progress_cb is not None:
                try:
                    progress_cb(len(embeddings), target_samples)
                except Exception:
                    # A misbehaving callback shouldn't kill an
                    # otherwise successful enrollment.
                    pass

        if not embeddings:
            reason = "No high-confidence face captured."
            if skipped_multiple:
                reason = (
                    "Multiple faces were visible -- enrollment requires "
                    "exactly one face in frame."
                )
            elif skipped_no_face >= attempts // 2:
                reason = "Couldn't see a face. Move closer to the camera."
            elif skipped_low_conf:
                reason = (
                    "Face was detected but at low confidence. Improve "
                    "lighting and look directly at the camera."
                )
            return EnrollmentResult(
                ok=False,
                samples=0,
                attempts=attempts,
                message=reason,
            )

        # Average the embeddings element-wise. FaceDB.upsert L2-
        # normalizes the result, which is the standard SFace mean-of-
        # samples trick (samples land near each other on the unit
        # sphere, so averaging then re-normalizing is equivalent to a
        # spherical mean for our purposes).
        dim = len(embeddings[0])
        mean = [0.0] * dim
        for emb in embeddings:
            for i, v in enumerate(emb):
                mean[i] += v
        scale = 1.0 / float(len(embeddings))
        mean = [v * scale for v in mean]

        try:
            self._face_db.upsert(name, mean, samples=len(embeddings))
        except Exception as exc:
            return EnrollmentResult(
                ok=False,
                samples=len(embeddings),
                attempts=attempts,
                message=f"Failed to save face: {exc}",
            )

        return EnrollmentResult(
            ok=True,
            samples=len(embeddings),
            attempts=attempts,
            message=f"Enrolled '{name}' from {len(embeddings)} samples.",
        )

    def list_enrolled_faces(self) -> List[str]:
        return self._face_db.names()

    def remove_enrolled_face(self, name: str) -> bool:
        return self._face_db.remove(name)

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
            # For recognised faces show "name match%"; otherwise show the
            # detection confidence so the operator can still see how
            # certain YuNet was about a generic face.
            if det.kind == KIND_FACE and det.identity and det.identity_score is not None:
                pct = int(round(det.identity_score * 100))
                label = f"{det.identity} {pct}%"
            else:
                pct = int(round(det.confidence * 100))
                label = f"{det.label} {pct}%"
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
