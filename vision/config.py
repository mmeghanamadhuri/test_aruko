"""Environment-driven configuration for the vision module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet, List, Optional


def _parse_camera_indices(raw: str) -> List[int]:
    raw = raw.strip()
    if not raw:
        return [0]
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out or [0]


def _default_api_url() -> str:
    """Self-hosted Roboflow Inference Server default (``inference server start`` → port 9001)."""
    raw = os.environ.get("ROBOFLOW_API_URL")
    if raw is None or not str(raw).strip():
        return "http://127.0.0.1:9001"
    return str(raw).strip()


@dataclass
class VisionConfig:
    """runtime: ``embedded`` = in-process (``pip install inference``); ``http`` = InferenceHTTPClient."""

    api_key: str
    api_url: str
    model_id: str
    runtime: str
    camera_indices: List[int]
    confidence_threshold: float
    infer_interval_sec: float
    label_allowlist: Optional[FrozenSet[str]]

    @classmethod
    def from_env(cls) -> VisionConfig:
        allow = os.environ.get("VISION_LABEL_ALLOWLIST", "").strip()
        allowset: Optional[FrozenSet[str]]
        if allow:
            allowset = frozenset(str(x.strip()) for x in allow.split(",") if x.strip())
        else:
            allowset = None

        runtime = os.environ.get("VISION_RUNTIME", "embedded").strip().lower()
        if runtime not in ("embedded", "http"):
            runtime = "embedded"

        return cls(
            api_key=os.environ.get("ROBOFLOW_API_KEY", "").strip(),
            api_url=_default_api_url(),
            model_id=os.environ.get("ROBOFLOW_MODEL_ID", "").strip(),
            runtime=runtime,
            camera_indices=_parse_camera_indices(
                os.environ.get("CARBOT_VISION_CAMERAS", "0")
            ),
            confidence_threshold=float(os.environ.get("VISION_CONFIDENCE", "0.4")),
            infer_interval_sec=float(os.environ.get("VISION_INFER_INTERVAL_SEC", "0.5")),
            label_allowlist=allowset,
        )
