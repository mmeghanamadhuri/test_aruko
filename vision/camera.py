"""OpenCV camera capture (USB/V4L2 or Jetson GStreamer pipeline)."""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple, Union

import numpy as np

log = logging.getLogger("carbot.vision.camera")

try:
    import cv2
except ImportError as e:  # pragma: no cover
    cv2 = None  # type: ignore
    _cv2_err = e
else:
    _cv2_err = None


def _ensure_cv2():
    if cv2 is None:
        raise ImportError(
            "OpenCV is required. On Jetson you can use: sudo apt install python3-opencv "
            "or pip install opencv-python-headless."
        ) from _cv2_err


class CameraStream:
    def __init__(
        self,
        index: int,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ):
        _ensure_cv2()
        self.index = index
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            log.warning("Could not open camera index %s", index)
        if width is not None:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        if height is not None:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self) -> None:
        self.cap.release()


class GStreamerCapture:
    """CSI or NVMM pipeline via ``cv2.CAP_GSTREAMER`` (Jetson Nano / NX / Orin)."""

    def __init__(self, pipeline: str):
        _ensure_cv2()
        self.pipeline = pipeline
        self.cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if not self.cap.isOpened():
            log.warning("GStreamer pipeline did not open (check CARBOT_VISION_GSTREAMER)")
        self.index = -1

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self) -> None:
        self.cap.release()


Capture = Union[CameraStream, GStreamerCapture]


def open_gripper_camera() -> Capture:
    """
    Single camera on the arm (first index in ``CARBOT_VISION_CAMERAS``), or
    ``CARBOT_VISION_GSTREAMER`` if set (full pipeline string ending in appsink).
    """
    gst = os.environ.get("CARBOT_VISION_GSTREAMER", "").strip()
    if gst:
        return GStreamerCapture(gst)
    first = _parse_first_index(os.environ.get("CARBOT_VISION_CAMERAS", "0"))
    return CameraStream(first)


def _parse_first_index(raw: str) -> int:
    raw = raw.strip() or "0"
    return int(raw.split(",")[0].strip())


class MultiCamera:
    def __init__(
        self,
        indices: List[int],
        width: Optional[int] = None,
        height: Optional[int] = None,
    ):
        self.streams = [CameraStream(i, width=width, height=height) for i in indices]

    def read_all(self) -> List[Tuple[int, Optional[np.ndarray]]]:
        out: List[Tuple[int, Optional[np.ndarray]]] = []
        for stream in self.streams:
            out.append((stream.index, stream.read()))
        return out

    def release(self) -> None:
        for s in self.streams:
            s.release()
