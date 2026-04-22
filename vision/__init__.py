"""
Car interior window-control button detection.

Uses OpenCV for capture. By default runs Roboflow **in-process** locally
(``VISION_RUNTIME=embedded``, ``pip install inference``). Optional ``http`` mode
talks to a local Inference Server on ``127.0.0.1:9001`` by default.

Run ``python -m vision.runner``, ``python -m vision.server``, or
``python -m vision.window_servo --preview`` (search + track + MJPEG).
"""

from .config import VisionConfig
from .detector import ButtonDetector, build_detector
from .types import BoundingBox, ButtonDetection, FrameResult

__all__ = [
    "VisionConfig",
    "ButtonDetector",
    "build_detector",
    "BoundingBox",
    "ButtonDetection",
    "FrameResult",
]
