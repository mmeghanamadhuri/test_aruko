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
    """runtime: ``embedded`` = in-process (``pip install inference``); ``http`` = InferenceHTTPClient; ``yolo`` = local Pytorch weights via ultralytics."""

    api_key: str
    api_url: str
    model_id: str
    model_path: str
    runtime: str
    camera_indices: List[int]
    confidence_threshold: float
    infer_interval_sec: float
    label_allowlist: Optional[FrozenSet[str]]
    approach_servos: List[int]
    approach_deltas: List[int]
    approach_dir: int
    # After visual approach: align presser to button using camera–presser geometry (see window_servo).
    offset_after_approach: bool
    offset_h_mm: float
    offset_v_mm: float
    offset_h_servos: List[int]
    offset_h_deltas_raw: List[int]
    mm_per_raw_h: float
    offset_v_actuator: str  # "extend" | "retract"
    offset_h_flip: bool
    offset_v_flip: bool
    offset_v_extra_mm: float
    offset_v_extra_wait_sec: float
    offset_settle_h_ticks: int
    offset_v_wait_sec: float
    offset_settle_press_sec: float
    press_json_rel: str

    @classmethod
    def from_env(cls) -> VisionConfig:
        allow = os.environ.get("VISION_LABEL_ALLOWLIST", "").strip()
        allowset: Optional[FrozenSet[str]]
        if allow:
            allowset = frozenset(str(x.strip()) for x in allow.split(",") if x.strip())
        else:
            allowset = None

        runtime = os.environ.get("VISION_RUNTIME", "embedded").strip().lower()
        if runtime not in ("embedded", "http", "yolo"):
            runtime = "embedded"

        off_en = os.environ.get("VISION_OFFSET_AFTER_APPROACH", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        h_mm = float(os.environ.get("VISION_CAMERA_PRESS_OFFSET_H_MM", "30"))
        v_mm = float(os.environ.get("VISION_CAMERA_PRESS_OFFSET_V_MM", "65"))
        h_serv = [int(x.strip()) for x in os.environ.get("VISION_OFFSET_H_SERVOS", "").split(",") if x.strip()]
        h_deltas = [int(x.strip()) for x in os.environ.get("VISION_OFFSET_H_DELTAS", "").split(",") if x.strip()]
        mm_pr_h = float(os.environ.get("VISION_MM_PER_RAW_H", "0"))
        h_sign = -1 if os.environ.get("VISION_OFFSET_H_SIGN", "1").strip() == "-1" else 1
        v_act = os.environ.get("VISION_OFFSET_V_ACTUATOR", "extend").strip().lower()
        if v_act not in ("extend", "retract"):
            v_act = "extend"

        h_flip = os.environ.get("VISION_OFFSET_H_FLIP", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not h_deltas and mm_pr_h > 0.0 and len(h_serv) == 1 and h_mm > 0:
            h_deltas = [int(round(h_sign * h_mm / mm_pr_h))]

        # One calibrated raw delta for the lateral shift (camera centered, gripper still
        # offset — move this joint by N raw units to bring the tip ~VISION_CAMERA_PRESS_OFFSET_H_MM).
        raw_one = os.environ.get("VISION_OFFSET_H_RAW_DELTA", "").strip()
        if not h_deltas and len(h_serv) == 1 and raw_one:
            try:
                h_deltas = [int(round(float(raw_one)))]
            except ValueError:
                pass

        v_flip = os.environ.get("VISION_OFFSET_V_FLIP", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        v_extra = float(os.environ.get("VISION_OFFSET_V_EXTRA_MM", "45"))
        v_extra_wait = float(os.environ.get("VISION_OFFSET_V_EXTRA_WAIT_SEC", "10"))

        return cls(
            api_key=os.environ.get("ROBOFLOW_API_KEY", "").strip(),
            api_url=_default_api_url(),
            model_id=os.environ.get("ROBOFLOW_MODEL_ID", "").strip(),
            model_path=os.environ.get("VISION_MODEL_PATH", "best.pt").strip(),
            runtime=runtime,
            camera_indices=_parse_camera_indices(
                os.environ.get("CARBOT_VISION_CAMERAS", "0")
            ),
            confidence_threshold=float(os.environ.get("VISION_CONFIDENCE", "0.4")),
            infer_interval_sec=float(os.environ.get("VISION_INFER_INTERVAL_SEC", "0.5")),
            label_allowlist=allowset,
            approach_servos=[int(x.strip()) for x in os.environ.get("VISION_APPROACH_SERVOS", "3").split(",") if x.strip()],
            approach_deltas=[int(x.strip()) for x in os.environ.get("VISION_APPROACH_DELTAS", "30").split(",") if x.strip()],
            approach_dir=int(os.environ.get("VISION_APPROACH_DIR", "1")),
            offset_after_approach=off_en,
            offset_h_mm=h_mm,
            offset_v_mm=v_mm,
            offset_h_servos=h_serv,
            offset_h_deltas_raw=h_deltas,
            mm_per_raw_h=mm_pr_h,
            offset_v_actuator=v_act,
            offset_h_flip=h_flip,
            offset_v_flip=v_flip,
            offset_v_extra_mm=v_extra,
            offset_v_extra_wait_sec=v_extra_wait,
            offset_settle_h_ticks=int(os.environ.get("VISION_OFFSET_SETTLE_H_TICKS", "6")),
            offset_v_wait_sec=float(os.environ.get("VISION_OFFSET_V_WAIT_SEC", "14")),
            offset_settle_press_sec=float(os.environ.get("VISION_OFFSET_PRESS_WAIT_SEC", "4")),
            press_json_rel=os.environ.get("VISION_PRESS_JSON", "actions/press.json").strip(),
        )
