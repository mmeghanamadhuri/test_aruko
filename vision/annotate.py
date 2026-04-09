"""Draw detections and HUD on BGR frames (preview / debugging)."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .types import ButtonDetection

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore


def draw_detections(
    frame_bgr: np.ndarray,
    detections: List[ButtonDetection],
    *,
    title: str = "",
    subtitle: str = "",
    primary: Optional[ButtonDetection] = None,
) -> np.ndarray:
    if cv2 is None:
        return frame_bgr
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    cx, cy = w // 2, h // 2
    cv2.drawMarker(out, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 24, 2)

    for d in detections:
        x1, y1, x2, y2 = (int(x) for x in d.bbox.to_xyxy())
        color: Tuple[int, int, int] = (0, 200, 0)
        if primary is not None and d is primary:
            color = (0, 255, 0)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"{d.label} {d.confidence:.2f}"
        cv2.putText(
            out,
            label,
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )

    if title:
        cv2.putText(
            out,
            title,
            (8, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
    if subtitle:
        cv2.putText(
            out,
            subtitle,
            (8, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
    return out


def encode_jpeg(frame_bgr: np.ndarray, quality: int = 80) -> bytes:
    if cv2 is None:
        raise ImportError("OpenCV required for JPEG encode")
    ok, buf = cv2.imencode(
        ".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return buf.tobytes()
