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

    def infer(self, frame: np.ndarray, camera_id: int = 0) -> List[ButtonDetection]:
        try:
            # YOLO predict handles typical numpy arrays. verbose=False reduces log spam.
            results = self._model.predict(frame, verbose=False)
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
                label = str(names.get(cls_id, f"class_{cls_id}"))
                
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
