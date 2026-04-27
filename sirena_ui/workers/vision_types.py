"""Shared types for the Nina vision pipeline.

Kept deliberately separate from `vision.types` so the GUI's
face/object-detection pipeline doesn't pull in the carbot button
detector's domain types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# Detection categories the UI groups results by.
KIND_FACE = "face"
KIND_OBJECT = "object"


@dataclass
class Detection:
    """A single detection in image-space (pixel) coordinates.

    `bbox` is an axis-aligned rectangle stored as (x1, y1, x2, y2)
    so it round-trips cleanly through OpenCV / Qt drawing helpers.

    For face detections we additionally surface the recognition result
    when an SFace embedding matched a name in the FaceDB. `identity`
    is None for unknown faces (or when recognition is disabled / no
    faces have been enrolled yet).
    """

    kind: str
    label: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    identity: Optional[str] = None
    identity_score: Optional[float] = None


@dataclass
class VisionStatus:
    """Coarse-grained status the screen renders as a pill."""

    camera_open: bool = False
    face_ready: bool = False
    object_ready: bool = False
    message: str = ""

    def is_ok(self) -> bool:
        return self.camera_open and (self.face_ready or self.object_ready)
