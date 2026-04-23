"""Car window / panel button detection via Roboflow Inference (local embedded or HTTP)."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, FrozenSet, List, Optional

import numpy as np

from .config import VisionConfig
from .types import BoundingBox, ButtonDetection

log = logging.getLogger("carbot.vision.detector")


def _resolve_label_name(
    cls_id: int, primary_names: Any, fallback_names: Optional[Dict[int, str]] = None
) -> str:
    name: Optional[str] = None
    if isinstance(primary_names, dict):
        raw = primary_names.get(cls_id)
        if raw is not None:
            name = str(raw)
    if fallback_names:
        generic = name is None or name == f"class{cls_id}" or name == f"class_{cls_id}"
        if generic:
            alt = fallback_names.get(cls_id)
            if alt is not None:
                name = str(alt)
    if name is None:
        return f"class_{cls_id}"
    return name


def _bgr_to_model_input(frame: np.ndarray) -> np.ndarray:
    """Roboflow / most training pipelines expect RGB."""
    try:
        import cv2
    except ImportError:
        return frame
    
    if len(frame.shape) == 3 and frame.shape[2] == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return frame


def _parse_prediction_item(p: Dict[str, Any], camera_id: int) -> Optional[ButtonDetection]:
    label = p.get("class") or p.get("class_name") or p.get("label")
    if not label:
        return None
    conf = p.get("confidence")
    if conf is None:
        conf = p.get("score", 0.0)
    try:
        confidence = float(conf)
    except (TypeError, ValueError):
        confidence = 0.0

    x = p.get("x")
    y = p.get("y")
    w = p.get("width")
    h = p.get("height")
    if x is None or y is None or w is None or h is None:
        return None

    return ButtonDetection(
        label=str(label),
        confidence=confidence,
        bbox=BoundingBox(
            cx=float(x),
            cy=float(y),
            w=float(w),
            h=float(h),
        ),
        camera_id=camera_id,
    )


def parse_roboflow_result(result: Any, camera_id: int) -> List[ButtonDetection]:
    """Normalize Roboflow object-detection JSON into ButtonDetection list."""
    if result is None:
        return []

    if isinstance(result, dict):
        preds = result.get("predictions")
        if preds is None:
            preds = []
    elif isinstance(result, list):
        preds = result
    else:
        log.debug("Unexpected infer result type: %s", type(result))
        return []

    if not isinstance(preds, list):
        return []

    out: List[ButtonDetection] = []
    for p in preds:
        if not isinstance(p, dict):
            continue
        det = _parse_prediction_item(p, camera_id)
        if det is not None:
            out.append(det)
    return out


class ButtonDetector(ABC):
    @abstractmethod
    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        ...


class MockButtonDetector(ButtonDetector):
    """Returns no detections; use for wiring tests without Roboflow."""

    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        return []


def _http_api_key_placeholder(api_url: str, api_key: str) -> str:
    """Local inference servers often ignore the key; cloud requires a real ROBOFLOW_API_KEY."""
    if api_key:
        return api_key
    u = api_url.lower()
    if "127.0.0.1" in u or "localhost" in u:
        return "not-required-for-local-server"
    return api_key


def _cloud_http_url(api_url: str) -> bool:
    return "roboflow.com" in api_url.lower()


class RoboflowHttpDetector(ButtonDetector):
    """
    Sends frames to a Roboflow **Inference Server** over HTTP (same machine or LAN).

    Default URL is ``http://127.0.0.1:9001`` (``inference server start``).
    For Roboflow Cloud instead, set ``ROBOFLOW_API_URL=https://serverless.roboflow.com``
    and ``ROBOFLOW_API_KEY``.

    ``model_id`` format: ``workspace/project/version`` (e.g. ``acme/window-buttons/2``).
    """

    def __init__(self, cfg: VisionConfig):
        try:
            from inference_sdk import InferenceHTTPClient
        except ImportError as e:
            raise ImportError(
                "HTTP vision mode needs: pip install inference-sdk"
            ) from e

        self._cfg = cfg
        key = _http_api_key_placeholder(cfg.api_url, cfg.api_key)
        self._client = InferenceHTTPClient(api_url=cfg.api_url, api_key=key)
        self._model_id = cfg.model_id

    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        img = _bgr_to_model_input(frame)
        try:
            result = self._client.infer(img, model_id=self._model_id)
        except Exception as e:
            log.warning("Roboflow HTTP infer failed: %s", e)
            return []
        return parse_roboflow_result(result, camera_id)


class RoboflowEmbeddedDetector(ButtonDetector):
    """
    Runs the model **in-process** via ``inference.get_model`` (weights on disk / GPU).
    No separate inference server. Typical for Jetson with ``inference`` or ``inference-gpu``.
    """

    def __init__(self, cfg: VisionConfig):
        try:
            from inference import get_model
        except ImportError as e:
            raise ImportError(
                "Embedded vision mode needs: pip install inference   "
                "(Jetson GPU: pip install inference-gpu — see Roboflow docs)"
            ) from e

        self._cfg = cfg
        kwargs: Dict[str, Any] = {"model_id": cfg.model_id}
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        self._model = get_model(**kwargs)

    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        img = _bgr_to_model_input(frame)
        try:
            result = self._model.infer(img)
        except Exception as e:
            log.warning("Roboflow embedded infer failed: %s", e)
            return []
        if isinstance(result, list) and result:
            result = result[0]
        return parse_roboflow_result(result, camera_id)


class UltralyticsLocalDetector(ButtonDetector):
    """
    Runs an Ultralytics YOLO model locally using a Pytorch `.pt` file.
    """

    def __init__(self, cfg: VisionConfig):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError(
                "YOLO vision mode needs: pip install ultralytics"
            ) from e

        self._cfg = cfg
        if not os.path.exists(cfg.model_path):
            raise FileNotFoundError(f"YOLO model not found at: {cfg.model_path}")

        # Let ultralytics handle device placement during first predict() (warm-up).
        # Explicit .to("cuda") triggers heap corruption on Jetson due to native allocator conflicts.
        self._model = YOLO(cfg.model_path)
        self._predict_conf = max(0.001, min(0.05, cfg.confidence_threshold))

    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        try:
            # Keep Ultralytics' internal confidence gate low; the app applies its own threshold later.
            results = self._model.predict(frame, verbose=False, conf=self._predict_conf)
            if not results:
                return []
            
            result = results[0]
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                return []
            
            names = result.names
            out: List[ButtonDetection] = []
            for i in range(len(boxes)):
                box = boxes[i]
                # xywh format from ultralytics: center x, center y, width, height
                xywh = box.xywh[0].cpu().numpy()
                cx, cy, w, h = xywh.tolist()
                
                conf = float(box.conf[0].cpu().numpy())
                cls_id = int(box.cls[0].cpu().numpy())
                label = _resolve_label_name(cls_id, names)
                
                out.append(ButtonDetection(
                    label=label,
                    confidence=conf,
                    bbox=BoundingBox(
                        cx=float(cx),
                        cy=float(cy),
                        w=float(w),
                        h=float(h),
                    ),
                    camera_id=camera_id,
                ))
            return out
        except Exception as e:
            log.warning("Ultralytics local infer failed: %s", e)
            return []


class UltralyticsTensorRTDetector(ButtonDetector):
    """
    Runs Ultralytics YOLO with a TensorRT `.engine` on Jetson.
    Falls back to exporting from `.pt` when enabled.
    """

    def __init__(self, cfg: VisionConfig):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError(
                "TensorRT YOLO mode needs: pip install ultralytics"
            ) from e

        self._cfg = cfg
        engine_path = cfg.trt_engine_path.strip()
        if not engine_path:
            model_dir, model_name = os.path.split(cfg.model_path)
            stem, _ = os.path.splitext(model_name)
            engine_path = os.path.join(model_dir or ".", f"{stem}.engine")

        if not os.path.exists(engine_path):
            if not cfg.trt_auto_export:
                raise FileNotFoundError(
                    f"TensorRT engine not found at: {engine_path}. "
                    "Set VISION_TRT_ENGINE_PATH or enable VISION_TRT_AUTO_EXPORT=1."
                )
            try:
                import torch
            except ImportError as e:
                raise RuntimeError("TensorRT auto-export requires torch with CUDA support") from e
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "TensorRT auto-export requested but CUDA is not available. "
                    "Use VISION_RUNTIME=yolo, provide a prebuilt .engine, or fix CUDA/driver compatibility."
                )
            if not os.path.exists(cfg.model_path):
                raise FileNotFoundError(
                    f"YOLO model not found at: {cfg.model_path} (required for TensorRT export)"
                )
            log.info(
                "TensorRT engine missing, exporting from %s (precision=%s) ...",
                cfg.model_path,
                cfg.trt_precision,
            )
            pt_model = YOLO(cfg.model_path)
            export_kwargs: Dict[str, Any] = {
                "format": "engine",
                "half": cfg.trt_precision == "fp16",
            }
            if cfg.trt_precision == "int8":
                export_kwargs["int8"] = True
            exported = pt_model.export(**export_kwargs)
            if isinstance(exported, str) and exported.strip():
                engine_path = exported.strip()
            elif not os.path.exists(engine_path):
                raise RuntimeError("TensorRT export completed but no engine path was returned")

        self._engine_path = engine_path
        log.info("Vision runtime=yolo_tensorrt (engine=%s)", self._engine_path)
        self._model = YOLO(self._engine_path, task="detect")
        self._names = self._load_class_names(YOLO, cfg.model_path)
        self._predict_conf = max(0.001, min(0.05, cfg.confidence_threshold))

    @staticmethod
    def _load_class_names(YOLO: Any, model_path: str) -> Dict[int, str]:
        try:
            base_model = YOLO(model_path, task="detect")
            names = getattr(base_model, "names", None) or {}
            if isinstance(names, dict):
                return {int(k): str(v) for k, v in names.items()}
        except Exception as e:
            log.warning("TensorRT metadata fallback: could not load class names from %s (%s)", model_path, e)
        return {}

    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        try:
            results = self._model.predict(frame, verbose=False, conf=self._predict_conf)
            if not results:
                return []

            result = results[0]
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                return []

            names = result.names if isinstance(result.names, dict) and result.names else {}
            out: List[ButtonDetection] = []
            for i in range(len(boxes)):
                box = boxes[i]
                xywh = box.xywh[0].cpu().numpy()
                cx, cy, w, h = xywh.tolist()
                conf = float(box.conf[0].cpu().numpy())
                cls_id = int(box.cls[0].cpu().numpy())
                label = _resolve_label_name(cls_id, names, self._names)

                out.append(
                    ButtonDetection(
                        label=label,
                        confidence=conf,
                        bbox=BoundingBox(
                            cx=float(cx),
                            cy=float(cy),
                            w=float(w),
                            h=float(h),
                        ),
                        camera_id=camera_id,
                    )
                )
            return out
        except Exception as e:
            log.warning("Ultralytics TensorRT infer failed: %s", e)
            return []


def build_detector(cfg: VisionConfig) -> ButtonDetector:
    mock = os.environ.get("VISION_MOCK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if mock:
        log.info("VISION_MOCK enabled — using MockButtonDetector")
        return MockButtonDetector()

    if cfg.runtime == "yolo":
        log.info("Vision runtime=yolo (ultralytics local weights: %s)", cfg.model_path)
        return UltralyticsLocalDetector(cfg)
    if cfg.runtime == "yolo_tensorrt":
        try:
            return UltralyticsTensorRTDetector(cfg)
        except Exception as e:
            log.warning(
                "TensorRT runtime unavailable (%s). Falling back to runtime=yolo with model %s",
                e,
                cfg.model_path,
            )
            return UltralyticsLocalDetector(cfg)

    if not cfg.model_id:
        raise ValueError(
            "Set ROBOFLOW_MODEL_ID (workspace/project/version) for Roboflow runtimes, or use VISION_RUNTIME=yolo / VISION_MOCK=1."
        )

    if cfg.runtime == "http":
        if _cloud_http_url(cfg.api_url) and not cfg.api_key:
            raise ValueError(
                "Cloud HTTP inference requires ROBOFLOW_API_KEY, or use local server "
                "(default ROBOFLOW_API_URL=http://127.0.0.1:9001) or VISION_RUNTIME=embedded."
            )
        log.info("Vision runtime=http api_url=%s", cfg.api_url)
        return RoboflowHttpDetector(cfg)

    log.info("Vision runtime=embedded (in-process inference)")
    return RoboflowEmbeddedDetector(cfg)


def filter_by_confidence(
    dets: List[ButtonDetection], min_conf: float
) -> List[ButtonDetection]:
    return [d for d in dets if d.confidence >= min_conf]


def filter_by_allowlist(
    dets: List[ButtonDetection], allow: Optional[FrozenSet[str]]
) -> List[ButtonDetection]:
    if not allow:
        return dets
    return [d for d in dets if d.label in allow]
