"""
window_servo.py — Search, Align, Approach, then camera→presser offsets and press.

Phase overview
--------------
SEARCH   — Sweep S6 (pan) & S7 (tilt) until a button is detected.
ALIGN    — Center the button in frame using proportional pan/tilt control.
           Transitions to APPROACH once stable for VISION_ALIGN_STABLE_FRAMES.
APPROACH — Move arm forward in steps (VISION_APPROACH_SERVOS / DELTAS) while S6/S7
           keep the target centred. Ends when bbox area >= VISION_APPROACH_AREA_FRAC.
POST_H   — (Optional) Lateral rigid move so the **gripper** reaches the button while the
           camera stays aimed at the center (default ~4 cm camera–gripper breadth). Requires
           VISION_OFFSET_H_SERVOS + (VISION_OFFSET_H_RAW_DELTA | DELTAS | MM_PER_RAW_H).
POST_V   — (Optional) Move along the actuator / “reach” axis by VISION_CAMERA_PRESS_OFFSET_V_MM
           (default 6.5 cm) via linear actuator extend/retract (VISION_OFFSET_V_ACTUATOR).
PRESS    — (Optional) Play VISION_PRESS_JSON (e.g. actions/press.json) once.
DONE     — Freeze all servos.

Key design decisions
--------------------
* Visual approach stops at a **safe stand-off** (bbox area); rigid offsets then
  bring the **tip** to the contact point using measured camera–presser geometry.
* Horizontal compensation uses the same absolute-goal pattern as approach arm steps.
* Vertical compensation prefers the **linear actuator** (mm); tune direction with
  VISION_OFFSET_V_ACTUATOR plus **VISION_OFFSET_V_FLIP** to reverse. Optional second
  leg **VISION_OFFSET_V_EXTRA_MM** reaches the contact after the camera
  offset leg. **VISION_OFFSET_H_FLIP** negates lateral deltas.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import time
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("carbot.vision.window_servo")


class Phase(Enum):
    SEARCH = auto()
    ALIGN = auto()
    APPROACH = auto()
    POST_H = auto()
    POST_V = auto()
    PRESS = auto()
    DONE = auto()


def _best(dets) -> Optional[object]:
    if not dets:
        return None
    return max(dets, key=lambda d: d.confidence * (d.bbox.w * d.bbox.h) ** 0.5)


def _search_moves(pan: int, tilt: int, step: int) -> List[Tuple[int, int]]:
    s = max(1, int(step))
    h = max(1, s // 2)
    return [
        (pan,  s), (pan,  -s),
        (tilt, s), (tilt, -s),
        (pan,  h), (pan,  -h),
        (tilt, h), (tilt, -h),
    ]


def _clamp(v: float, lim: int) -> int:
    return max(-lim, min(lim, int(round(v))))


def _flip_actuator(action: str, flip: bool) -> str:
    if not flip:
        return action
    return "retract" if action == "extend" else "extend"


def run(
    *,
    preview: bool,
    dry_motion: bool,
    motion_host: str,
    motion_port: int,
) -> None:

    # Stabilise CUDA heap before any other native import
    import torch
    torch.cuda.is_available()

    import numpy as _np

    from .config import VisionConfig
    from .detector import build_detector, filter_by_allowlist, filter_by_confidence
    from .motion_client import motion_rpc
    from .types import ButtonDetection

    vcfg = VisionConfig.from_env()

    # Warm-up detector before OpenCV/GStreamer init (avoids heap collision on Jetson)
    detector = build_detector(vcfg)
    log.info("Warming up detector ...")
    detector.infer(_np.zeros((480, 640, 3), dtype=_np.uint8), camera_id=0)
    log.info("Detector ready.")

    from .annotate import draw_detections, encode_jpeg
    from .camera import open_gripper_camera
    from .mjpeg_server import MJPEGServer

    cam = open_gripper_camera()

    preview_port = int(os.environ.get("VISION_PREVIEW_PORT", "8080"))
    preview_host = os.environ.get("VISION_PREVIEW_HOST", "0.0.0.0")
    mjpeg: Optional[MJPEGServer] = None
    if preview:
        mjpeg = MJPEGServer(host=preview_host, port=preview_port)
        mjpeg.start_background()

    # ── Config ────────────────────────────────────────────────────────────────
    pan  = int(os.environ.get("VISION_PAN_SERVO",  "6"))
    tilt = int(os.environ.get("VISION_TILT_SERVO", "7"))

    search_step = int(os.environ.get("VISION_SEARCH_STEP",  "90"))
    search_spd  = int(os.environ.get("VISION_SEARCH_SPEED", "200"))
    track_spd   = int(os.environ.get("VISION_TRACK_SPEED",  "300"))

    # Adaptive gains for ALIGN and APPROACH pan/tilt correction
    kp_far  = float(os.environ.get("VISION_KP_FAR",  "0.55"))
    kp_mid  = float(os.environ.get("VISION_KP_MID",  "0.35"))
    kp_near = float(os.environ.get("VISION_KP_NEAR", "0.15"))

    inv_x = -1.0 if os.environ.get("VISION_INVERT_PAN",  "").strip() == "1" else 1.0
    inv_y = -1.0 if os.environ.get("VISION_INVERT_TILT", "").strip() == "1" else 1.0

    max_delta   = int(os.environ.get("VISION_MAX_DELTA",          "200"))
    dead_px     = float(os.environ.get("VISION_DEADZONE_PX",      "20"))
    smooth_a    = float(os.environ.get("VISION_SMOOTH_ALPHA",     "0.35"))
    stable_need = int(os.environ.get("VISION_ALIGN_STABLE_FRAMES", "4"))
    lost_max    = int(os.environ.get("VISION_LOST_FRAMES",        "25"))
    infer_ivl   = float(os.environ.get("VISION_INFER_INTERVAL_SEC","0.12"))

    # ── Approach config ───────────────────────────────────────────────────────
    # Stop when bounding-box fills this fraction of the frame.
    app_area = float(os.environ.get("VISION_APPROACH_AREA_FRAC", "0.18"))

    # Arm step servo speed (keep slow to avoid overshoot).
    app_spd  = int(os.environ.get("VISION_APPROACH_SPEED", "150"))

    # Pixel-error threshold: arm step fires only when err <= arm_thr.
    # Above this, the step is paused and pan/tilt re-centre the button first.
    arm_thr  = float(os.environ.get("VISION_APPROACH_ARM_THR", "40"))

    # Pixel-error threshold: pan/tilt micro-correction sent when err > pan_thr.
    pan_thr  = float(os.environ.get("VISION_APPROACH_PAN_THR", "18"))

    # Max pan/tilt delta during approach (smaller than normal tracking).
    max_pan_ap = int(os.environ.get("VISION_APPROACH_MAX_PAN", "70"))

    # Inference ticks to wait after an arm step before allowing the next one.
    # Gives the arm time to settle and the image to stabilise.
    step_cooldown = int(os.environ.get("VISION_APPROACH_STEP_COOLDOWN", "3"))

    jpeg_q = int(os.environ.get("VISION_PREVIEW_JPEG_QUALITY", "75"))

    # ── Runtime state ─────────────────────────────────────────────────────────
    moves  = _search_moves(pan, tilt, search_step)
    move_i = 0
    phase  = Phase.SEARCH
    lost   = 0
    stable_ct  = 0
    last_infer = 0.0
    last_dets: List[ButtonDetection] = []
    ema_dx = 0.0
    ema_dy = 0.0
    last_positions: Dict[str, int] = {}
    arm_cooldown_ticks = 0   # ticks remaining before next arm step
    post_h_ticks = 0
    post_v_start: Optional[float] = None
    post_v_stage = 0
    press_armed = False
    press_deadline = 0.0

    # ── Helpers ───────────────────────────────────────────────────────────────
    def rpc(cmd: dict) -> Optional[dict]:
        nonlocal last_positions
        if dry_motion:
            log.debug("dry_motion skip %s", cmd.get("cmd"))
            return {"status": "ok", "dry_run": True, "positions": last_positions}
        try:
            res = motion_rpc(motion_host, motion_port, cmd)
            if res and isinstance(res.get("positions"), dict):
                last_positions = res["positions"]
            return res
        except Exception as e:
            log.warning("RPC failed: %s", e)
            return None

    def pan_tilt_correct(ex: float, ey: float, *, spd: int, max_d: int) -> None:
        """Send a proportional pan/tilt correction for the given pixel error."""
        err = math.hypot(ex, ey)
        kp  = kp_far if err > 120 else kp_mid if err > 40 else kp_near
        nonlocal ema_dx, ema_dy
        ema_dx = smooth_a * (inv_x * kp * ex) + (1.0 - smooth_a) * ema_dx
        ema_dy = smooth_a * (inv_y * kp * ey) + (1.0 - smooth_a) * ema_dy
        d_pan  = _clamp(-ema_dx, max_d)
        d_tilt = _clamp(-ema_dy, max_d)
        rpc({"cmd": "multi_servo_move",
             "servos": {str(pan): d_pan, str(tilt): d_tilt},
             "speed": spd})

    def fire_offset_horizontal() -> bool:
        """
        Apply one rigid correction: present + delta → absolute goal per servo
        (same convention as fire_arm_step). Skips if not configured.
        """
        servos = vcfg.offset_h_servos
        deltas = vcfg.offset_h_deltas_raw
        if not servos or len(servos) != len(deltas):
            log.warning(
                "POST_H skipped — gripper lateral offset not applied. Set "
                "VISION_OFFSET_H_SERVOS=4 (example) and one of: VISION_OFFSET_H_RAW_DELTA "
                "(one calibrated raw step for ~VISION_CAMERA_PRESS_OFFSET_H_MM mm), or "
                "VISION_OFFSET_H_DELTAS (same length as servos), or VISION_MM_PER_RAW_H with "
                "a single servo id."
            )
            return False

        status_resp = rpc({"cmd": "status"})
        if status_resp and isinstance(status_resp.get("positions"), dict):
            last_positions.update(status_resp["positions"])

        step_servos: Dict[str, int] = {}
        log_parts: List[str] = []
        rel_ids = {6, 7}
        hmul = -1 if vcfg.offset_h_flip else 1
        for i, sid in enumerate(servos):
            delta = int(deltas[i]) * hmul
            cur = last_positions.get(str(sid))
            if cur is None:
                log.warning("POST_H: no telemetry for S%d — skipping horizontal offset", sid)
                return False
            if sid in rel_ids:
                step_servos[str(sid)] = delta
                log_parts.append(f"S{sid}:rel({delta:+d})")
            else:
                goal = (int(cur) + delta) & 0xFFFF
                step_servos[str(sid)] = goal
                log_parts.append(f"S{sid}:{cur}->{goal}({delta:+d})")

        log.info("POST_H camera→presser lateral (~%.1f mm nominal): %s", vcfg.offset_h_mm, " | ".join(log_parts))
        rpc({"cmd": "multi_servo_move", "servos": step_servos, "speed": app_spd})
        return True

    def fire_arm_step() -> bool:
        """
        Send one small step along the approach vector (vcfg.approach_servos /
        vcfg.approach_deltas).  Returns True if the command was sent.
        """
        # Refresh telemetry so we know current positions before stepping
        status_resp = rpc({"cmd": "status"})
        if status_resp and isinstance(status_resp.get("positions"), dict):
            last_positions.update(status_resp["positions"])

        step_servos: Dict[str, int] = {}
        log_parts:   List[str]      = []

        for i, sid in enumerate(vcfg.approach_servos):
            delta = vcfg.approach_deltas[i] if i < len(vcfg.approach_deltas) else 0
            cur   = last_positions.get(str(sid))
            if cur is None:
                log.warning("S%d: no position telemetry — skipping arm step", sid)
                continue
            goal = (int(cur) + delta * vcfg.approach_dir) & 0xFFFF
            step_servos[str(sid)] = goal
            log_parts.append(f"S{sid}:{cur}->{goal}({delta:+d})")

        if not step_servos:
            log.warning("Arm step skipped: no telemetry for servos %s",
                        vcfg.approach_servos)
            return False

        log.info("ARM STEP [approach]: %s", " | ".join(log_parts))
        rpc({"cmd": "multi_servo_move", "servos": step_servos, "speed": app_spd})
        return True

    # ── Startup ───────────────────────────────────────────────────────────────
    if not dry_motion:
        rpc({"cmd": "freeze"})

    log.info("window_servo ready — phase=SEARCH")

    if vcfg.offset_after_approach:
        ok_h = bool(
            vcfg.offset_h_servos
            and len(vcfg.offset_h_servos) == len(vcfg.offset_h_deltas_raw)
        )
        if not ok_h:
            log.error(
                "VISION_OFFSET_AFTER_APPROACH is on but POST_H has no lateral servo move "
                "configured. The camera can be centered on the button while the gripper "
                "remains ~%.0f mm off — set VISION_OFFSET_H_SERVOS plus "
                "VISION_OFFSET_H_RAW_DELTA (recommended) or DELTAS / MM_PER_RAW_H. "
                "See vision/env.example.",
                vcfg.offset_h_mm,
            )

    try:
        while True:
            frame = cam.read()
            if frame is None:
                log.warning("No frame from camera")
                time.sleep(0.1)
                continue

            h_px, w_px = frame.shape[:2]
            now = time.monotonic()
            inferred = False

            if now - last_infer >= infer_ivl:
                last_infer = now
                inferred   = True
                dets = detector.infer(frame, camera_id=0)
                dets = filter_by_confidence(dets, vcfg.confidence_threshold)
                dets = filter_by_allowlist(dets, vcfg.label_allowlist)
                last_dets = dets

            target = _best(last_dets)

            # ── Phase transitions on detection state ──────────────────────────
            if inferred:
                if target is None:
                    lost += 1

                    if lost > lost_max:
                        log.info("Target lost for %d frames — SEARCH", lost)
                        rpc({"cmd": "stop"})
                        phase     = Phase.SEARCH
                        stable_ct = 0
                        ema_dx = ema_dy = 0.0

                else:
                    if lost > 0:
                        log.info("Target re-acquired after %d lost frames", lost)
                    lost = 0
                    if phase == Phase.SEARCH:
                        log.info("Detected '%s' -> ALIGN", target.label)
                        phase     = Phase.ALIGN
                        stable_ct = 0
                        arm_cooldown_ticks = 0

            # ── Preview ───────────────────────────────────────────────────────
            subtitle = f"{phase.name} | det={len(last_dets)}"
            if target:
                area_pct = 100.0 * (target.bbox.w * target.bbox.h) / max(1.0, w_px * h_px)
                subtitle += f" | {target.label} | area={area_pct:.1f}%"
            vis = draw_detections(frame, last_dets,
                                  title="window_servo", subtitle=subtitle,
                                  primary=target)
            if mjpeg:
                mjpeg.update_frame(encode_jpeg(vis, quality=jpeg_q))

            if dry_motion:
                time.sleep(0.01)
                continue

            # ── After visual approach: rigid offsets + press (no bbox required) ─
            if phase == Phase.POST_H:
                if inferred:
                    if post_h_ticks == 0 and vcfg.offset_after_approach:
                        fire_offset_horizontal()
                    post_h_ticks += 1
                    if post_h_ticks >= vcfg.offset_settle_h_ticks:
                        phase = Phase.POST_V
                        post_v_start = None
                        post_v_stage = 0
                time.sleep(0.02)
                continue

            if phase == Phase.POST_V:
                now_t = time.monotonic()
                if post_v_start is None:
                    post_v_start = now_t
                    post_v_stage = 0

                if post_v_stage == 0:
                    act = _flip_actuator(vcfg.offset_v_actuator, vcfg.offset_v_flip)
                    if vcfg.offset_v_mm > 0.5:
                        r = rpc(
                            {
                                "cmd": "actuator",
                                "action": act,
                                "distance_mm": vcfg.offset_v_mm,
                            }
                        )
                        if r and r.get("status") != "ok":
                            log.warning("POST_V stage1 actuator: %s", r)
                        log.info("POST_V stage1: %s %.1f mm", act, vcfg.offset_v_mm)
                    else:
                        log.info("POST_V stage1 skipped (VISION_CAMERA_PRESS_OFFSET_V_MM ~ 0)")
                    post_v_stage = 1
                elif post_v_stage == 1 and now_t - post_v_start >= vcfg.offset_v_wait_sec:
                    post_v_start = now_t
                    act2 = _flip_actuator(vcfg.offset_v_actuator, vcfg.offset_v_flip)
                    if vcfg.offset_v_extra_mm > 0.5:
                        r2 = rpc(
                            {
                                "cmd": "actuator",
                                "action": act2,
                                "distance_mm": vcfg.offset_v_extra_mm,
                            }
                        )
                        if r2 and r2.get("status") != "ok":
                            log.warning("POST_V stage2 actuator: %s", r2)
                        log.info(
                            "POST_V stage2 (reach): %s %.1f mm",
                            act2,
                            vcfg.offset_v_extra_mm,
                        )
                        post_v_stage = 2
                    else:
                        phase = Phase.PRESS
                        press_armed = False
                        post_v_start = None
                        post_v_stage = 0
                elif post_v_stage == 2 and now_t - post_v_start >= vcfg.offset_v_extra_wait_sec:
                    phase = Phase.PRESS
                    press_armed = False
                    post_v_start = None
                    post_v_stage = 0
                time.sleep(0.05)
                continue

            if phase == Phase.PRESS:
                if not press_armed:
                    press_armed = True
                    if vcfg.press_json_rel:
                        log.info("PRESS: playing %s", vcfg.press_json_rel)
                        rpc(
                            {
                                "cmd": "play",
                                "file": vcfg.press_json_rel,
                                "loop": False,
                            }
                        )
                    else:
                        log.info("PRESS skipped (empty VISION_PRESS_JSON)")
                    press_deadline = time.monotonic() + vcfg.offset_settle_press_sec
                elif time.monotonic() >= press_deadline:
                    rpc({"cmd": "freeze"})
                    phase = Phase.DONE
                    log.info("Sequence complete (DONE)")
                time.sleep(0.05)
                continue

            if phase == Phase.DONE:
                time.sleep(0.1)
                continue

            if not inferred:
                time.sleep(0.01)
                continue

            # No target — nothing to act on (SEARCH / ALIGN / APPROACH only)
            if target is None:
                time.sleep(0.01)
                continue

            # ── SEARCH ───────────────────────────────────────────────────────
            if phase == Phase.SEARCH:
                sid, delta = moves[move_i % len(moves)]
                move_i += 1
                rpc({"cmd": "servo_move", "servo_id": sid,
                     "value": int(delta), "speed": search_spd})
                time.sleep(0.05)
                continue

            # Pixel error from frame centre — used by both ALIGN and APPROACH
            ex  = target.bbox.cx - 0.5 * w_px
            ey  = target.bbox.cy - 0.5 * h_px
            err = math.hypot(ex, ey)

            # ── ALIGN ─────────────────────────────────────────────────────────
            if phase == Phase.ALIGN:
                pan_tilt_correct(ex, ey, spd=track_spd, max_d=max_delta)

                wiggle = dead_px * 1.5
                if abs(ex) < dead_px and abs(ey) < dead_px:
                    stable_ct += 1
                    log.info("Stable %d/%d (err=%.1fpx)", stable_ct, stable_need, err)
                elif stable_ct > 0 and abs(ex) < wiggle and abs(ey) < wiggle:
                    pass  # hold count in wiggle zone
                else:
                    if stable_ct > 0:
                        log.info("Stability reset at err=%.1fpx", err)
                    stable_ct = 0

                if stable_ct >= stable_need:
                    log.info("Aligned -> APPROACH")
                    stable_ct = 0
                    arm_cooldown_ticks = 2   # let arm settle before first step
                    phase = Phase.APPROACH

            # ── APPROACH ─────────────────────────────────────────────────────
            elif phase == Phase.APPROACH:

                # 1. Stopping criterion — purely visual, works at any height/distance
                area = (target.bbox.w * target.bbox.h) / max(1.0, float(w_px * h_px))
                if area >= app_area:
                    log.info(
                        "Visual stand-off reached (area=%.3f >= %.3f)",
                        area,
                        app_area,
                    )
                    if vcfg.offset_after_approach:
                        phase = Phase.POST_H
                        post_h_ticks = 0
                        post_v_start = None
                        post_v_stage = 0
                        press_armed = False
                        log.info("→ POST_H (lateral) → POST_V (vertical mm) → PRESS")
                    else:
                        rpc({"cmd": "freeze"})
                        phase = Phase.DONE
                    continue

                # 2. Pan/tilt micro-correction — keeps button in frame while arm moves.
                #    Always runs (independent of arm step cooldown) so tracking never stops.
                if abs(ex) > pan_thr or abs(ey) > pan_thr:
                    pan_tilt_correct(ex, ey, spd=track_spd, max_d=max_pan_ap)
                    log.debug("Pan/tilt micro-correct: ex=%.1f ey=%.1f err=%.1f", ex, ey, err)

                # 3. Cooldown — wait for arm to settle after previous step
                if arm_cooldown_ticks > 0:
                    arm_cooldown_ticks -= 1
                    log.debug("Arm cooldown: %d ticks remaining", arm_cooldown_ticks)
                    continue

                # 4. Arm step — fires only when button is well-centred.
                #    If it drifted (err > arm_thr), pan/tilt above re-centres first;
                #    the arm will step on the next tick once err drops below arm_thr.
                if err <= arm_thr:
                    fired = fire_arm_step()
                    if fired:
                        arm_cooldown_ticks = step_cooldown
                else:
                    log.debug(
                        "Arm step PAUSED — err=%.1fpx > arm_thr=%.1fpx "
                        "(waiting for pan/tilt to re-centre)", err, arm_thr
                    )

            time.sleep(0.01)

    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        cam.release()
        if mjpeg:
            mjpeg.stop()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Autonomous window-button search + align + approach"
    )
    p.add_argument("--preview",     action="store_true",
                   help="Serve MJPEG preview on VISION_PREVIEW_PORT (default 8080).")
    p.add_argument("--dry-motion",  action="store_true",
                   help="Vision only; no motion commands sent.")
    p.add_argument("--motion-host", default=os.environ.get("MOTION_HOST", "127.0.0.1"))
    p.add_argument("--motion-port", type=int,
                   default=int(os.environ.get("MOTION_PORT", "5000")))
    args = p.parse_args()
    run(
        preview=args.preview,
        dry_motion=args.dry_motion,
        motion_host=args.motion_host,
        motion_port=args.motion_port,
    )


if __name__ == "__main__":
    main()


