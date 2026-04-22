"""Shared types for window-button vision."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class BoundingBox:
    """Roboflow-style box: center x/y, width, height in pixels."""

    cx: float
    cy: float
    w: float
    h: float

    def to_xyxy(self) -> Tuple[float, float, float, float]:
        x1 = self.cx - self.w / 2
        y1 = self.cy - self.h / 2
        x2 = self.cx + self.w / 2
        y2 = self.cy + self.h / 2
        return x1, y1, x2, y2


@dataclass
class ButtonDetection:
    label: str
    confidence: float
    bbox: BoundingBox
    camera_id: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "bbox": {
                "cx": self.bbox.cx,
                "cy": self.bbox.cy,
                "w": self.bbox.w,
                "h": self.bbox.h,
            },
            "camera_id": self.camera_id,
        }


@dataclass
class FrameResult:
    """All detections for one captured frame (single camera)."""

    camera_id: int
    detections: List[ButtonDetection] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "detections": [d.as_dict() for d in self.detections],
        }
