

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
    # After POST_V (and optional wrist): play this motion file, then exit (for shell loop).
    revert_json_rel: str
    # After POST_V, before PRESS: aim linear actuator vertically by setting wrist servo (default S5).
    pre_press_wrist_servo: int
    pre_press_wrist_abs: Optional[int]
    pre_press_wrist_delta: Optional[int]
    pre_press_wrist_speed: int
    pre_press_wrist_wait_sec: float
    # Immediately before POST_V linear-actuator extend: optional tilt (default S7) raw delta.
    pre_actuator_tilt_servo: int
    pre_actuator_tilt_delta: int
    pre_actuator_tilt_speed: int
    pre_actuator_tilt_settle_sec: float
    # POST_V: optional chunked linear-actuator extend + relative joint “creep” (backlash / preload).
    post_v_backlash_assist: bool
    post_v_extend_chunk_mm: float
    post_v_backlash_servos: List[int]
    post_v_backlash_raw_deltas: List[int]
    post_v_backlash_speed: int
    post_v_backlash_settle_sec: float




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
        h_mm = float(os.environ.get("VISION_CAMERA_PRESS_OFFSET_H_MM", "40"))
        v_cam = os.environ.get("VISION_CAMERA_PRESS_OFFSET_V_MM", "").strip()
        v_alt = os.environ.get("VISION_OFFSET_V_MM", "").strip()
        if v_cam:
            v_mm = float(v_cam)
        elif v_alt:
            v_mm = float(v_alt)
        else:
            v_mm = 65.0
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
        v_extra_wait = float(os.environ.get("VISION_OFFSET_V_EXTRA_WAIT_SEC", "3.3333333"))




        pw_raw = os.environ.get("VISION_PRE_PRESS_WRIST_RAW", "").strip()
        pw_delta_s = os.environ.get("VISION_PRE_PRESS_WRIST_DELTA", "").strip()
        pw_abs: Optional[int] = None
        pw_delta: Optional[int] = None
        if pw_raw:
            try:
                pw_abs = int(pw_raw)
            except ValueError:
                pass
        elif pw_delta_s:
            try:
                pw_delta = int(pw_delta_s)
            except ValueError:
                pass




        try:
            pre_act_tilt_delta = int(
                os.environ.get("VISION_PRE_ACTUATOR_TILT_DELTA", "-2200").strip()
            )
        except ValueError:
            pre_act_tilt_delta = -2200

        bl_assist = os.environ.get("VISION_POST_V_BACKLASH_ASSIST", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        try:
            post_v_chunk_mm = float(
                os.environ.get("VISION_POST_V_EXTEND_CHUNK_MM", "5").strip() or "5"
            )
        except ValueError:
            post_v_chunk_mm = 5.0
        bl_servos = [
            int(x.strip())
            for x in os.environ.get("VISION_POST_V_BACKLASH_SERVOS", "2,3,4").split(",")
            if x.strip()
        ]
        bl_raw_s = os.environ.get("VISION_POST_V_BACKLASH_RAW_DELTAS", "").strip()
        bl_deltas: List[int] = []
        if bl_raw_s:
            bl_deltas = [int(x.strip()) for x in bl_raw_s.split(",") if x.strip()]
        elif bl_servos and bl_assist:
            deg_s = os.environ.get("VISION_POST_V_BACKLASH_DEG", "").strip()
            if deg_s:
                try:
                    deg_v = float(deg_s)
                except ValueError:
                    deg_v = 7.5
            else:
                deg_v = 7.5
            # motion_server uses ~0.088° per raw tick for logging; use same scale for defaults.
            dr = int(round(deg_v / 0.088))
            bl_deltas = [dr for _ in bl_servos] if dr else []
        if bl_assist and (
            not bl_servos or not bl_deltas or len(bl_deltas) != len(bl_servos)
        ):
            bl_assist = False
            bl_deltas = []
        try:
            bl_spd = int(os.environ.get("VISION_POST_V_BACKLASH_SPEED", "396").strip() or "396")
        except ValueError:
            bl_spd = 396
        try:
            bl_settle = float(
                os.environ.get("VISION_POST_V_BACKLASH_SETTLE_SEC", "0.0444445").strip()
                or "0.0444445"
            )
        except ValueError:
            bl_settle = 0.0444445

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
            infer_interval_sec=float(os.environ.get("VISION_INFER_INTERVAL_SEC", "0.2777778")),
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
            offset_settle_h_ticks=int(os.environ.get("VISION_OFFSET_SETTLE_H_TICKS", "3")),
            offset_v_wait_sec=float(os.environ.get("VISION_OFFSET_V_WAIT_SEC", "3.8888889")),
            offset_settle_press_sec=float(os.environ.get("VISION_OFFSET_PRESS_WAIT_SEC", "1.1111111")),
            press_json_rel=os.environ.get("VISION_PRESS_JSON", "actions/press.json").strip(),
            revert_json_rel=os.environ.get("VISION_REVERT_JSON", "").strip(),
            pre_press_wrist_servo=int(os.environ.get("VISION_PRE_PRESS_WRIST_SERVO", "5")),
            pre_press_wrist_abs=pw_abs,
            pre_press_wrist_delta=pw_delta,
            pre_press_wrist_speed=int(os.environ.get("VISION_PRE_PRESS_WRIST_SPEED", "396")),
            pre_press_wrist_wait_sec=float(os.environ.get("VISION_PRE_PRESS_WRIST_WAIT_SEC", "0.8333333")),
            pre_actuator_tilt_servo=int(
                os.environ.get("VISION_PRE_ACTUATOR_TILT_SERVO", "7").strip() or "7"
            ),
            pre_actuator_tilt_delta=pre_act_tilt_delta,
            pre_actuator_tilt_speed=int(
                os.environ.get("VISION_PRE_ACTUATOR_TILT_SPEED", "1023").strip() or "1023"
            ),
            pre_actuator_tilt_settle_sec=float(
                os.environ.get("VISION_PRE_ACTUATOR_TILT_SETTLE_SEC", "0.1944445").strip()
                or "0.1944445"
            ),
            post_v_backlash_assist=bl_assist,
            post_v_extend_chunk_mm=post_v_chunk_mm,
            post_v_backlash_servos=bl_servos,
            post_v_backlash_raw_deltas=bl_deltas,
            post_v_backlash_speed=bl_spd,
            post_v_backlash_settle_sec=bl_settle,
        )











