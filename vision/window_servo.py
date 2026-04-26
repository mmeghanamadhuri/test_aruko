"""
window_servo.py — Search, Align, Approach, then camera→presser offsets and press.


Phase overview
--------------
SEARCH   — Default bilateral: tilt-only sweep on VISION_TILT_SERVO (default S7) with timed
           legs; pan is not moved during search. Legacy: VISION_SEARCH_BILATERAL=0 uses
           mixed S6/S7 pattern. ALIGN/APPROACH still correct both pan and tilt.
ALIGN    — Center the button in frame using proportional pan/tilt control.
           Transitions to APPROACH once stable for VISION_ALIGN_STABLE_FRAMES.
APPROACH — Move arm forward in steps (VISION_APPROACH_SERVOS / DELTAS) while S6/S7
           keep the target centred. Bbox stand-off (VISION_APPROACH_AREA_FRAC) requires
           VISION_APPROACH_MIN_ARM_STEPS_FOR_AREA steps (default max(MIN_ARM_STEPS, 3)) so a
           large YOLO box alone cannot end approach after one tick. Loss→POST_H snap still
           uses VISION_APPROACH_MIN_ARM_STEPS and VISION_APPROACH_LOST_SNAP_AREA_PCT.
POST_H   — (Optional) Lateral rigid move so the **gripper** reaches the button while the
           camera stays aimed at the center (default ~4 cm camera–gripper breadth). Requires
           VISION_OFFSET_H_SERVOS + (VISION_OFFSET_H_RAW_DELTA | DELTAS | MM_PER_RAW_H).
POST_V   — Optional VISION_PRE_ACTUATOR_TILT_DELTA on VISION_PRE_ACTUATOR_TILT_SERVO (default S7)
           before the first linear-actuator extend; then extend by VISION_CAMERA_PRESS_OFFSET_V_MM
           / VISION_OFFSET_V_MM, dwell VISION_OFFSET_V_WAIT_SEC, retract, then optional extra leg.
POST_WRIST — (Optional) Move wrist servo (default S5) to a tuned goal so the linear actuator
           points vertically before contact. Set VISION_PRE_PRESS_WRIST_RAW or _DELTA.
POST_EXIT — After POST_V primary extend+dwell+retract: optional Y-axis nudge, second actuator
           extend/retract pattern, tilt home, then play VISION_POST_CYCLE_BACK_JSON (e.g. back.json).
           Set VISION_REVERT_JSON empty to skip revert_short here; use carbot menu ``neutral`` for revert.
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
import sys
import threading
import time
from collections import deque
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
    POST_WRIST = auto()
    POST_EXIT = auto()
    REVERT = auto()
    PRESS = auto()
    DONE = auto()


class PerfTracker:
    def __init__(self, report_every: int = 120):
        self.report_every = max(1, int(report_every))
        self.frame_ms: deque[float] = deque(maxlen=400)
        self.infer_ms: deque[float] = deque(maxlen=400)
        self.phase_ms: Dict[str, deque[float]] = {
            p.name: deque(maxlen=200) for p in Phase
        }
        self.phase_starts: Dict[str, float] = {}
        self.cycles: deque[float] = deque(maxlen=100)
        self._frames_seen = 0
        self._cycle_start: Optional[float] = None

    @staticmethod
    def _pct(values: deque[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = int(round((len(ordered) - 1) * pct))
        return ordered[max(0, min(len(ordered) - 1, idx))]

    def start_phase(self, phase: Phase, now: float) -> None:
        key = phase.name
        if key not in self.phase_starts:
            self.phase_starts[key] = now
        if phase == Phase.ALIGN and self._cycle_start is None:
            self._cycle_start = now

    def end_phase(self, phase: Phase, now: float) -> None:
        key = phase.name
        t0 = self.phase_starts.pop(key, None)
        if t0 is not None:
            self.phase_ms[key].append((now - t0) * 1000.0)
        if phase == Phase.DONE and self._cycle_start is not None:
            self.cycles.append((now - self._cycle_start) * 1000.0)
            self._cycle_start = None

    def mark_frame(self, frame_ms: float, infer_ms: Optional[float]) -> None:
        self.frame_ms.append(frame_ms)
        if infer_ms is not None:
            self.infer_ms.append(infer_ms)
        self._frames_seen += 1
        if self._frames_seen % self.report_every == 0:
            self.report()

    def report(self) -> None:
        if not self.frame_ms:
            return
        msg = (
            "PERF frame(ms): p50=%.1f p90=%.1f | infer(ms): p50=%.1f p90=%.1f"
            % (
                self._pct(self.frame_ms, 0.5),
                self._pct(self.frame_ms, 0.9),
                self._pct(self.infer_ms, 0.5) if self.infer_ms else 0.0,
                self._pct(self.infer_ms, 0.9) if self.infer_ms else 0.0,
            )
        )
        if self.cycles:
            msg += " | cycle(ms): p50=%.1f p90=%.1f" % (
                self._pct(self.cycles, 0.5),
                self._pct(self.cycles, 0.9),
            )
        for key in ("ALIGN", "APPROACH", "POST_H", "POST_V", "REVERT", "PRESS"):
            vals = self.phase_ms.get(key)
            if vals:
                msg += f" | {key.lower()}_p50={self._pct(vals, 0.5):.1f}"
        log.info(msg)




def _best(dets) -> Optional[object]:
    if not dets:
        return None
    return max(dets, key=lambda d: d.confidence * (d.bbox.w * d.bbox.h) ** 0.6)






def _search_moves(pan: int, tilt: int, step: int) -> List[Tuple[int, int]]:
    s = max(1, int(step))
    h = max(1, s // 2)
    return [
        (pan,  s), (pan,  -s),
        (tilt, 500), (tilt, -s),
        (pan,  h), (pan,  -h),
        (tilt, h), (tilt, -h),
    ]




def _clamp(v: float, lim: int) -> int:
    return max(-lim, min(lim, int(round(v))))


def _raw_goal_abs_error(cur: int, goal: int) -> int:
    """Smallest magnitude error between present and goal in Dynamixel raw space (0..65535)."""
    c = int(cur) & 0xFFFF
    g = int(goal) & 0xFFFF
    d = (g - c) & 0xFFFF
    if d > 0x7FFF:
        d -= 0x10000
    return abs(int(d))


def _delta_raw_rel_to_goal(cur: int, goal: int) -> int:
    """Signed shortest-path offset from ``cur`` to ``goal`` for S6/S7 relative ``servo_move``."""
    c = int(cur) & 0xFFFF
    g = int(goal) & 0xFFFF
    d = (g - c) & 0xFFFF
    if d > 0x7FFF:
        d -= 0x10000
    return max(-32768, min(32767, int(d)))


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
    from .motion_client import close_motion_rpc_connection, motion_rpc
    from .types import BoundingBox, ButtonDetection



    vcfg = VisionConfig.from_env()



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

    # Warm-up detector after camera/MJPEG startup so the preview endpoint is available sooner.
    detector = build_detector(vcfg)
    log.info("Warming up detector ...")
    detector.infer(_np.zeros((480, 640, 3), dtype=_np.uint8), camera_id=0)
    log.info("Detector ready.")


    # ── Config ────────────────────────────────────────────────────────────────
    pan  = int(os.environ.get("VISION_PAN_SERVO",  "6"))
    tilt = int(os.environ.get("VISION_TILT_SERVO", "7"))


    search_step = int(os.environ.get("VISION_SEARCH_STEP",  "360"))  # ×1.5 vs prior default
    search_spd  = int(os.environ.get("VISION_SEARCH_SPEED", "900"))  # ×1.5 vs prior
    track_spd   = int(os.environ.get("VISION_TRACK_SPEED",  "540"))  # ×1.5 vs prior


    # Adaptive gains for ALIGN and APPROACH pan/tilt correction
    kp_far  = float(os.environ.get("VISION_KP_FAR",  "0.55"))
    kp_mid  = float(os.environ.get("VISION_KP_MID",  "0.35"))
    kp_near = float(os.environ.get("VISION_KP_NEAR", "0.15"))


    inv_x = -1.0 if os.environ.get("VISION_INVERT_PAN",  "").strip() == "1" else 1.0
    inv_y = -1.0 if os.environ.get("VISION_INVERT_TILT", "").strip() == "1" else 1.0


    max_delta   = int(os.environ.get("VISION_MAX_DELTA",          "200"))
    dead_px     = float(os.environ.get("VISION_DEADZONE_PX",      "20"))
    smooth_a    = float(os.environ.get("VISION_SMOOTH_ALPHA",     "0.35"))
    stable_need = int(os.environ.get("VISION_ALIGN_STABLE_FRAMES", "1"))
    lost_max    = int(os.environ.get("VISION_LOST_FRAMES",        "25"))
    infer_ivl   = float(os.environ.get("VISION_INFER_INTERVAL_SEC", "0.05"))
    track_hold_frames = max(0, int(os.environ.get("VISION_TRACK_HOLD_FRAMES", "5")))
    track_smooth_alpha = max(
        0.0,
        min(1.0, float(os.environ.get("VISION_TRACK_SMOOTH_ALPHA", "0.65"))),
    )
    lock_target_after_align = os.environ.get("VISION_LOCK_TARGET_AFTER_ALIGN", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    lock_switch_max_px = max(
        20.0,
        float(os.environ.get("VISION_LOCK_SWITCH_MAX_PX", "120")),
    )
    track_fallback_conf = max(
        0.05,
        min(
            vcfg.confidence_threshold,
            float(
                os.environ.get(
                    "VISION_TRACK_FALLBACK_CONF",
                    str(min(vcfg.confidence_threshold, 0.35)),
                )
            ),
        ),
    )


    # ── Approach config ───────────────────────────────────────────────────────
    # Stop when bounding-box fills this fraction of the frame.
    app_area = float(os.environ.get("VISION_APPROACH_AREA_FRAC", "0.05"))
    # If detections disappear during APPROACH, optionally snap to POST_H when the last
    # bbox area (%) was at least this. Default = full standoff (app_area*100); a looser
    # value (e.g. app_area*100 - 2) re-enables early snap but can skip the arm on shaky
    # detections for some buttons.
    approach_lost_snap_area_pct = float(
        os.environ.get(
            "VISION_APPROACH_LOST_SNAP_AREA_PCT",
            str(app_area * 100.0),
        )
    )
    # Do not finish APPROACH on loss snap until this many arm steps succeeded.
    approach_min_arm_steps = int(os.environ.get("VISION_APPROACH_MIN_ARM_STEPS", "1"))
    # Area-based stand-off only: some classes already fill >= APPROACH_AREA_FRAC while still
    # far physically — require more arm steps before bbox size alone can end APPROACH.
    approach_min_arm_steps_for_area = int(
        os.environ.get(
            "VISION_APPROACH_MIN_ARM_STEPS_FOR_AREA",
            str(max(approach_min_arm_steps, 3)),
        )
    )


    # Arm step servo speed (keep slow to avoid overshoot).
    app_spd  = int(os.environ.get("VISION_APPROACH_SPEED", "540"))  # ×1.5 vs prior


    # Pixel-error threshold: arm step fires only when err <= arm_thr.
    # Above this, the step is paused and pan/tilt re-centre the button first.
    arm_thr  = float(os.environ.get("VISION_APPROACH_ARM_THR", "40")) #40


    # Pixel-error threshold: pan/tilt micro-correction sent when err > pan_thr.
    pan_thr  = float(os.environ.get("VISION_APPROACH_PAN_THR", "18")) 


    # Max pan/tilt delta during approach (smaller than normal tracking).
    max_pan_ap = int(os.environ.get("VISION_APPROACH_MAX_PAN", "70"))


    # Inference ticks to wait after an arm step before allowing the next one.
    # Gives the arm time to settle and the image to stabilise.
    step_cooldown = int(os.environ.get("VISION_APPROACH_STEP_COOLDOWN", "1"))  # ÷1.5 vs prior (min 1)
    status_poll_sleep = float(os.environ.get("VISION_STATUS_POLL_SLEEP_SEC", "0.02"))
    loop_idle_sleep = float(os.environ.get("VISION_LOOP_IDLE_SLEEP_SEC", "0.006"))
    search_sleep = float(os.environ.get("VISION_SEARCH_SLEEP_SEC", "0.025"))
    post_sleep = float(os.environ.get("VISION_POST_LOOP_SLEEP_SEC", "0.03"))
    done_sleep = float(os.environ.get("VISION_DONE_SLEEP_SEC", "0.05"))
    perf_report_every = int(os.environ.get("VISION_PERF_REPORT_EVERY", "120"))
    perf = PerfTracker(report_every=perf_report_every)


    jpeg_q = int(os.environ.get("VISION_PREVIEW_JPEG_QUALITY", "75")) #75


    # ── Runtime state ─────────────────────────────────────────────────────────
    moves  = _search_moves(pan, tilt, search_step)
    move_i = 0
    phase  = Phase.SEARCH
    prev_phase = phase
    lost   = 0
    stable_ct  = 0
    last_infer = 0.0
    last_dets: List[ButtonDetection] = []
    ema_dx = 0.0
    ema_dy = 0.0
    last_positions: Dict[str, int] = {}
    arm_cooldown_ticks = 0   # ticks remaining before next arm step
    approach_arm_steps_sent = 0
    tracked_target: Optional[ButtonDetection] = None
    track_hold_left = 0
    target_lock_active = False
    post_h_ticks = 0
    post_v_start: Optional[float] = None
    post_v_stage = 0
    press_armed = False
    press_deadline = 0.0
    post_wrist_armed = False
    post_wrist_start = 0.0
    last_area_pct: float = 0.0   # last known bbox area — persists when detection drops
    post_v_act0 = "extend"
    revert_armed = False
    post_exit_armed = False
    perf.start_phase(phase, time.monotonic())

    # Bilateral SEARCH: anchor tilt raw after short (memorised) or from first status. Sweep
    # legs use default relative moves for S7; homing between legs uses relative delta to
    # anchor (S6/S7 are relative IDs in motion_server). Pan is not swept.
    search_bilateral = os.environ.get("VISION_SEARCH_BILATERAL", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    search_sweep_sec = float(os.environ.get("VISION_SEARCH_SWEEP_SEC", "12.0"))
    search_right_sign = (
        -1 if os.environ.get("VISION_SEARCH_SWEEP_RIGHT_SIGN", "1").strip() == "-1" else 1
    )
    # After each sweep leg, wait until tilt telemetry matches captured anchor (homing move
    # needs time; starting the next relative leg immediately was cancelling convergence).
    search_home_tol_raw = int(os.environ.get("VISION_SEARCH_HOME_TOL_RAW", "12"))
    search_home_wait_sec = float(os.environ.get("VISION_SEARCH_HOME_WAIT_SEC", "10.0"))
    _home_spd_def = min(1023, int(round(min(search_spd, 380) * 1.8)))
    search_home_speed = int(
        os.environ.get(
            "VISION_SEARCH_HOME_SPEED",
            str(_home_spd_def),
        )
    )
    sweep_slot = 0  # 0 = tilt leg A, 1 = homing, 2 = tilt leg B, 3 = homing
    sweep_leg_t0 = 0.0
    anchor_pan_u16: Optional[int] = None
    anchor_tilt_u16: Optional[int] = None
    search_need_reanchor = True
    # S6/S7 present raw right after short.json (vision start). Revert pre-move uses relative
    # deltas to these goals (motion_server treats 6–7 as relative by default).
    pan_short_home_for_revert_u16: Optional[int] = None
    tilt_short_home_for_revert_u16: Optional[int] = None


    # ── Helpers ───────────────────────────────────────────────────────────────
    def rpc(cmd: dict) -> Optional[dict]:
        nonlocal last_positions
        if dry_motion:
            log.debug("dry_motion skip %s", cmd.get("cmd"))
            return {"status": "ok", "dry_run": True, "positions": last_positions}
        c = cmd.get("cmd")
        to = 12.0
        if c == "actuator":
            d = cmd.get("distance_mm")
            if d is not None:
                try:
                    to = max(90.0, float(d) / 5.0 + 35.0)
                except (TypeError, ValueError):
                    to = 90.0
            else:
                to = 95.0
        elif c == "play":
            to = 30.0
        elif c == "stop":
            to = 20.0
        elif c == "status":
            to = 6.0
        try:
            res = motion_rpc(motion_host, motion_port, cmd, timeout=to)
            if res and isinstance(res.get("positions"), dict):
                last_positions = res["positions"]
            return res
        except Exception as e:
            log.warning("RPC failed: %s", e)
            return None


    def wait_servo_at_anchor(servo_id: int, goal_u16: int) -> None:
        """
        Block until ``positions[S*]`` is within ``search_home_tol_raw`` of ``goal_u16``
        for several consecutive polls, or ``search_home_wait_sec`` elapses.
        """
        deadline = time.monotonic() + max(0.5, search_home_wait_sec)
        stable = 0
        need = 4
        last_err: Optional[int] = None
        g = int(goal_u16) & 0xFFFF
        sk = str(servo_id)
        while time.monotonic() < deadline:
            r = rpc({"cmd": "status"})
            if not r or not isinstance(r.get("positions"), dict):
                time.sleep(status_poll_sleep)
                continue
            cur = r["positions"].get(sk)
            if cur is None:
                time.sleep(status_poll_sleep)
                continue
            err = _raw_goal_abs_error(int(cur), g)
            last_err = err
            if err <= search_home_tol_raw:
                stable += 1
                if stable >= need:
                    log.info(
                        "Servo S%s at anchor raw=%d (err=%d <= tol=%d, goal=%d)",
                        sk,
                        int(cur) & 0xFFFF,
                        err,
                        search_home_tol_raw,
                        g,
                    )
                    return
            else:
                stable = 0
            time.sleep(status_poll_sleep)
        log.warning(
            "Servo S%s anchor wait timeout (goal=%d last_err=%s tol=%d)",
            sk,
            g,
            str(last_err),
            search_home_tol_raw,
        )


    def wait_motion_playback(timeout_sec: float = 240.0) -> bool:
        deadline = time.monotonic() + timeout_sec
        saw_playing = False
        while time.monotonic() < deadline:
            r = rpc({"cmd": "status"})
            if not r or r.get("status") != "ok":
                time.sleep(status_poll_sleep)
                continue
            playing = bool(r.get("is_playing"))
            if playing:
                saw_playing = True
            elif saw_playing:
                return True
            time.sleep(status_poll_sleep)
        if not saw_playing:
            time.sleep(max(0.08, status_poll_sleep * 3))
            r = rpc({"cmd": "status"})
            if r and r.get("status") == "ok" and not r.get("is_playing", True):
                return True
        return False


    def run_post_cycle_exit_sequence() -> None:
        """
        After primary POST_V extend+dwell+retract: pause, Y (tilt) nudge -ve, actuator extend,
        Y +ve, pause, retract legs, tilt home, then play VISION_POST_CYCLE_BACK_JSON.
        """
        nonlocal last_positions
        rel_back = os.environ.get("VISION_POST_CYCLE_BACK_JSON", "").strip()
        if not rel_back:
            return
        if dry_motion:
            log.info("POST_EXIT dry_motion — skipping hardware; would play %s", rel_back)
            return

        pre_pause = float(os.environ.get("VISION_POST_EXIT_PRE_PAUSE_SEC", "0.2"))
        y_sid = int(os.environ.get("VISION_POST_EXIT_Y_SERVO", str(tilt)))
        d_neg = int(os.environ.get("VISION_POST_EXIT_Y_DELTA_NEG", "-2000"))
        ext_mm = float(os.environ.get("VISION_POST_EXIT_EXTEND_MM", "55"))
        d_pos = int(os.environ.get("VISION_POST_EXIT_Y_DELTA_POS", "6300"))
        mid_pause = float(os.environ.get("VISION_POST_EXIT_AFTER_EXTEND_PAUSE_SEC", "0.35"))
        retract_legs = max(1, int(os.environ.get("VISION_POST_EXIT_RETRACT_LEGS", "2")))
        y_spd = int(
            os.environ.get(
                "VISION_POST_EXIT_Y_SPEED",
                str(min(1023, max(app_spd, search_home_speed))),
            )
        )
        act0 = post_v_act0

        log.info("POST_EXIT: %.3fs initial pause", pre_pause)
        time.sleep(max(0.0, pre_pause))

        log.info("POST_EXIT: S%d relative %+d @ speed %d (Y −ve)", y_sid, d_neg, y_spd)
        rpc({"cmd": "servo_move", "servo_id": y_sid, "value": d_neg, "speed": y_spd})
        time.sleep(0.1)

        log.info("POST_EXIT: actuator %s %.1f mm", act0, ext_mm)
        r_ex = rpc({"cmd": "actuator", "action": act0, "distance_mm": ext_mm})
        if r_ex and r_ex.get("status") != "ok":
            log.warning("POST_EXIT extend: %s", r_ex)

        log.info("POST_EXIT: S%d relative %+d @ speed %d (Y +ve)", y_sid, d_pos, y_spd)
        rpc({"cmd": "servo_move", "servo_id": y_sid, "value": d_pos, "speed": y_spd})
        time.sleep(0.08)

        log.info("POST_EXIT: %.3fs pause before retract", mid_pause)
        time.sleep(max(0.0, mid_pause))

        rev = "retract" if act0 == "extend" else "extend"
        for leg in range(retract_legs):
            log.info(
                "POST_EXIT: actuator %s %.1f mm (retract leg %d/%d)",
                rev,
                ext_mm,
                leg + 1,
                retract_legs,
            )
            rr = rpc({"cmd": "actuator", "action": rev, "distance_mm": ext_mm})
            if rr and rr.get("status") != "ok":
                log.warning("POST_EXIT retract leg %d: %s", leg + 1, rr)

        gh = tilt_short_home_for_revert_u16
        if gh is not None:
            st_t = rpc({"cmd": "status"})
            pos_t = (
                st_t.get("positions")
                if st_t and isinstance(st_t.get("positions"), dict)
                else None
            )
            if pos_t:
                last_positions.update(pos_t)
            curt = (pos_t or {}).get(str(y_sid))
            if curt is not None:
                dt = _delta_raw_rel_to_goal(int(curt), int(gh) & 0xFFFF)
                if dt != 0:
                    log.info("POST_EXIT: tilt S%d abs home raw=%d (telemetry was %s)", y_sid, gh, curt)
                    rpc(
                        {
                            "cmd": "servo_move",
                            "servo_id": y_sid,
                            "value": int(gh) & 0xFFFF,
                            "speed": y_spd,
                            "mode": "abs",
                        }
                    )
                    wait_servo_at_anchor(y_sid, int(gh) & 0xFFFF)
        else:
            log.warning("POST_EXIT: no memorised tilt home — skipping Y home")

        log.info("POST_EXIT: playing %s", rel_back)
        rpc({"cmd": "play", "file": rel_back, "loop": False})
        if not wait_motion_playback(
            float(os.environ.get("VISION_BACK_PLAYBACK_TIMEOUT_SEC", "300"))
        ):
            log.warning("POST_EXIT: %s playback timeout", rel_back)
        rpc({"cmd": "freeze"})


    def finish_post_v_actuator() -> None:
        """Leave POST_V: wrist alignment, post-exit/back.json, revert clip, or press.json."""
        nonlocal phase, post_v_start, post_v_stage, post_wrist_armed, press_armed, post_exit_armed
        post_v_start = None
        post_v_stage = 0
        post_back = os.environ.get("VISION_POST_CYCLE_BACK_JSON", "").strip()
        if (
            vcfg.pre_press_wrist_abs is not None
            or vcfg.pre_press_wrist_delta is not None
        ):
            set_phase(Phase.POST_WRIST)
            post_wrist_armed = False
        elif post_back:
            set_phase(Phase.POST_EXIT)
            post_exit_armed = False
            press_armed = False
        elif vcfg.revert_json_rel:
            set_phase(Phase.REVERT)
            press_armed = False
        else:
            set_phase(Phase.PRESS)
            press_armed = False


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


    # Wrist perpendicular lock — raw goal that keeps S5 pointed at the panel.
    # Read from env so it can be tuned without code changes.
    wrist_lock_raw = int(os.environ.get("VISION_APPROACH_WRIST_LOCK_RAW", "1325"))
    wrist_lock_spd = int(os.environ.get("VISION_APPROACH_WRIST_LOCK_SPEED", "540"))  # ×1.5 vs prior


    def fire_arm_step() -> bool:
        """
        Send one small step along the approach vector (vcfg.approach_servos /
        vcfg.approach_deltas).  Returns True if the command was sent.


        S5 (wrist) is always bundled into the same multi_servo_move so it is
        written atomically with the arm joints — no separate RPC that could race.
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


        # Always lock S5 wrist to the perpendicular angle in the same packet.
        # Bundling avoids a second RPC that could be reordered or arrive while
        # joints are mid-travel.  Use a faster speed so it snaps back quickly.
        if "5" not in step_servos:   # don't override if S5 is an approach servo
            step_servos["5"] = wrist_lock_raw
            log_parts.append(f"S5:lock({wrist_lock_raw})")


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


    st_home = rpc({"cmd": "status"})
    if st_home and isinstance(st_home.get("positions"), dict):
        pos0 = st_home["positions"]
        ph0 = pos0.get(str(pan))
        th0 = pos0.get(str(tilt))
        if ph0 is not None:
            pan_short_home_for_revert_u16 = int(ph0) & 0xFFFF
            log.info(
                "Memorised pan S%d home raw=%d (post-short.json; revert pre-move rel)",
                pan,
                pan_short_home_for_revert_u16,
            )
        else:
            log.warning(
                "Startup status: no pan S%d position — revert will skip pan home pre-move",
                pan,
            )
        if th0 is not None:
            tilt_short_home_for_revert_u16 = int(th0) & 0xFFFF
            log.info(
                "Memorised tilt S%d home raw=%d (post-short.json; revert pre-move + sweep anchor basis)",
                tilt,
                tilt_short_home_for_revert_u16,
            )
        else:
            log.warning(
                "Startup status: no tilt S%d position — revert will skip tilt home pre-move",
                tilt,
            )
    else:
        log.warning(
            "Startup status failed — revert will skip pan/tilt home pre-move where unknown"
        )


    cleanup_done = False

    def cleanup_vision_hw(*, fast_exit: bool = False) -> None:
        """Close motion socket, MJPEG, and camera once (idempotent)."""
        nonlocal cleanup_done
        if cleanup_done:
            return
        try:
            close_motion_rpc_connection(motion_host, motion_port)
        except Exception:
            pass
        if mjpeg is not None:
            try:
                mjpeg.stop()
            except Exception:
                pass

        def _release_cam() -> None:
            try:
                cam.release()
            except Exception:
                pass

        if fast_exit:
            done = threading.Event()

            def _wrap() -> None:
                _release_cam()
                done.set()

            threading.Thread(target=_wrap, daemon=True).start()
            sec = float(os.environ.get("VISION_CAM_RELEASE_TIMEOUT_SEC", "5"))
            if not done.wait(sec):
                log.warning(
                    "cam.release() did not finish in %.1fs — forcing process exit for launcher",
                    sec,
                )
        else:
            _release_cam()
        cleanup_done = True


    carbot_shell_loop = os.environ.get("CARBOT_SHELL_LOOP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    def set_phase(new_phase: Phase) -> None:
        nonlocal phase, prev_phase, tracked_target, track_hold_left, target_lock_active
        if new_phase == phase:
            return
        nowp = time.monotonic()
        perf.end_phase(phase, nowp)
        prev_phase = phase
        phase = new_phase
        if new_phase == Phase.SEARCH:
            tracked_target = None
            track_hold_left = 0
            target_lock_active = False
        perf.start_phase(phase, nowp)

    def _best_tracking_candidate(
        dets: List[ButtonDetection],
    ) -> Optional[ButtonDetection]:
        if not dets:
            return None
        if tracked_target is None:
            return _best(dets)
        tx = tracked_target.bbox.cx
        ty = tracked_target.bbox.cy

        def _key(d: ButtonDetection) -> Tuple[int, float, float]:
            same_label = 0 if d.label == tracked_target.label else 1
            dist2 = (d.bbox.cx - tx) ** 2 + (d.bbox.cy - ty) ** 2
            return (same_label, dist2, -d.confidence)

        return min(dets, key=_key)

    def _within_target_lock(det: ButtonDetection) -> bool:
        if tracked_target is None:
            return True
        dx = float(det.bbox.cx) - float(tracked_target.bbox.cx)
        dy = float(det.bbox.cy) - float(tracked_target.bbox.cy)
        dist = math.hypot(dx, dy)
        size_gate = max(
            lock_switch_max_px,
            0.75 * max(float(tracked_target.bbox.w), float(tracked_target.bbox.h)),
        )
        return det.label == tracked_target.label and dist <= size_gate

    def _update_tracked_target(obs: ButtonDetection) -> ButtonDetection:
        nonlocal tracked_target, track_hold_left
        if tracked_target is None or tracked_target.label != obs.label:
            tracked_target = ButtonDetection(
                label=obs.label,
                confidence=obs.confidence,
                bbox=BoundingBox(
                    cx=float(obs.bbox.cx),
                    cy=float(obs.bbox.cy),
                    w=float(obs.bbox.w),
                    h=float(obs.bbox.h),
                ),
                camera_id=obs.camera_id,
            )
        else:
            a = track_smooth_alpha
            prev = tracked_target.bbox
            tracked_target = ButtonDetection(
                label=obs.label,
                confidence=obs.confidence,
                bbox=BoundingBox(
                    cx=a * float(obs.bbox.cx) + (1.0 - a) * float(prev.cx),
                    cy=a * float(obs.bbox.cy) + (1.0 - a) * float(prev.cy),
                    w=a * float(obs.bbox.w) + (1.0 - a) * float(prev.w),
                    h=a * float(obs.bbox.h) + (1.0 - a) * float(prev.h),
                ),
                camera_id=obs.camera_id,
            )
        track_hold_left = track_hold_frames
        return tracked_target

    def exit_for_carbot_shell(msg: str) -> None:
        """
        Hard-exit immediately so ``carbot.sh`` can run ``pick_button`` (``back.json`` already ran in POST_EXIT).
        on *every* cycle. Do **not** call ``cleanup_vision_hw`` here: ``mjpeg.stop()`` /
        ``cam.release()`` can hang on later cycles, so Bash never saw ``launch`` return.
        ``os._exit`` tears the process down; use Ctrl+C + ``finally`` for full cleanup.
        """
        log.info(msg)
        try:
            close_motion_rpc_connection(motion_host, motion_port)
        except Exception:
            pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)


    try:
        while True:
            frame_t0 = time.monotonic()
            frame = cam.read()
            if frame is None:
                log.warning("No frame from camera")
                time.sleep(max(0.03, search_sleep))
                continue


            h_px, w_px = frame.shape[:2]
            now = time.monotonic()
            inferred = False


            used_track_hold = False
            if now - last_infer >= infer_ivl:
                last_infer = now
                inferred   = True
                infer_t0 = time.monotonic()
                dets_raw = detector.infer(frame, camera_id=0)
                infer_ms = (time.monotonic() - infer_t0) * 1000.0
                dets_raw = filter_by_allowlist(dets_raw, vcfg.label_allowlist)
                dets = filter_by_confidence(dets_raw, vcfg.confidence_threshold)
                if target_lock_active and tracked_target is not None:
                    locked = [d for d in dets if _within_target_lock(d)]
                    if locked:
                        dets = locked
                    else:
                        dets = []
                if not dets and phase in (Phase.ALIGN, Phase.APPROACH):
                    dets_fallback = filter_by_confidence(dets_raw, track_fallback_conf)
                    if target_lock_active and tracked_target is not None:
                        dets_fallback = [d for d in dets_fallback if _within_target_lock(d)]
                    cand = _best_tracking_candidate(dets_fallback)
                    if cand is not None:
                        dets = [cand]
                last_dets = dets
            else:
                infer_ms = None



            target = _best(last_dets)
            if target is not None and phase in (Phase.ALIGN, Phase.APPROACH):
                target = _update_tracked_target(target)
            elif (
                target is None
                and phase in (Phase.ALIGN, Phase.APPROACH)
                and tracked_target is not None
                and track_hold_left > 0
            ):
                target = tracked_target
                track_hold_left -= 1
                used_track_hold = True



            # ── Phase transitions on detection state ──────────────────────────
            if inferred:
                if target is None:
                    # Do not count "lost" frames while already in SEARCH — otherwise
                    # lost_max fires every ~VISION_LOST_FRAMES ticks, re-anchors, and the
                    # bilateral tilt sweep never finishes a leg or reverses direction.
                    if phase != Phase.SEARCH:
                        lost += 1


                    # If we were in APPROACH and the button disappears because
                    # the tip is right on top of it (or bbox shrinks before full stand-off),
                    # don't go back to SEARCH — continue to POST_H / actuator when enabled.
                    if (
                        phase == Phase.APPROACH
                        and last_area_pct >= approach_lost_snap_area_pct
                        and approach_arm_steps_sent >= approach_min_arm_steps
                    ):
                        log.info(
                            "Detection lost during APPROACH (last area=%.1f%% >= snap %.1f%% "
                            "stand-off=%.1f%%, arm_steps=%d/%d) — treating as close range.",
                            last_area_pct,
                            approach_lost_snap_area_pct,
                            app_area * 100.0,
                            approach_arm_steps_sent,
                            approach_min_arm_steps,
                        )
                        if vcfg.offset_after_approach:
                            set_phase(Phase.POST_H)
                            post_h_ticks = 0
                            post_v_start = None
                            post_v_stage = 0
                            press_armed = False
                        else:
                            rpc({"cmd": "freeze"})
                            set_phase(Phase.DONE)
                        lost = 0


                    _terminal = phase in (
                        Phase.POST_H,
                        Phase.POST_V,
                        Phase.POST_WRIST,
                        Phase.POST_EXIT,
                        Phase.REVERT,
                        Phase.PRESS,
                        Phase.DONE,
                    )
                    if lost > lost_max and not _terminal:
                        if (
                            phase == Phase.APPROACH
                            and vcfg.offset_after_approach
                            and approach_arm_steps_sent >= approach_min_arm_steps
                        ):
                            log.info(
                                "Target lost for %d frames during APPROACH (last area=%.1f%%, "
                                "stand-off goal=%.1f%%, arm_steps=%d/%d) — assuming occlusion; "
                                "→ POST_H → POST_V (actuator), not SEARCH",
                                lost,
                                last_area_pct,
                                app_area * 100.0,
                                approach_arm_steps_sent,
                                approach_min_arm_steps,
                            )
                            lost = 0
                            set_phase(Phase.POST_H)
                            post_h_ticks = 0
                            post_v_start = None
                            post_v_stage = 0
                            press_armed = False
                        elif phase == Phase.APPROACH and vcfg.offset_after_approach:
                            log.info(
                                "Target lost for %d frames during APPROACH but arm_steps=%d < "
                                "min %d — SEARCH (avoid skipping approach on unstable det)",
                                lost,
                                approach_arm_steps_sent,
                                approach_min_arm_steps,
                            )
                            rpc({"cmd": "stop"})
                            set_phase(Phase.SEARCH)
                            stable_ct = 0
                            ema_dx = ema_dy = 0.0
                            post_wrist_armed = False
                            last_area_pct = 0.0
                            search_need_reanchor = True
                            lost = 0
                        else:
                            log.info("Target lost for %d frames — SEARCH", lost)
                            rpc({"cmd": "stop"})
                            set_phase(Phase.SEARCH)
                            stable_ct = 0
                            ema_dx = ema_dy = 0.0
                            post_wrist_armed = False
                            last_area_pct = 0.0
                            search_need_reanchor = True
                            lost = 0


                else:
                    if lost > 0 and not used_track_hold:
                        log.info("Target re-acquired after %d lost frames", lost)
                    lost = 0
                    if phase == Phase.SEARCH:
                        log.info("Detected '%s' -> ALIGN", target.label)
                        set_phase(Phase.ALIGN)
                        stable_ct = 0
                        arm_cooldown_ticks = 0


            # ── Preview ───────────────────────────────────────────────────────
            subtitle = f"{phase.name} | det={len(last_dets)}"
            if target:
                area_pct = 100.0 * (target.bbox.w * target.bbox.h) / max(1.0, w_px * h_px)
                last_area_pct = area_pct
                subtitle += f" | {target.label} | area={area_pct:.1f}%"
            vis = draw_detections(frame, last_dets,
                      title="window_servo", subtitle=subtitle,
                      primary=target)
            if mjpeg:
                mjpeg.update_frame(encode_jpeg(vis, quality=jpeg_q))


            if dry_motion:
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(loop_idle_sleep)
                continue


            # ── After visual approach: rigid offsets + press (no bbox required) ─
            if phase == Phase.POST_H:
                if inferred:
                    if post_h_ticks == 0 and vcfg.offset_after_approach:
                        fire_offset_horizontal()
                    post_h_ticks += 1
                    if post_h_ticks >= vcfg.offset_settle_h_ticks:
                        set_phase(Phase.POST_V)
                        post_v_start = None
                        post_v_stage = 0
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(post_sleep)
                continue


            if phase == Phase.POST_V:
                now_t = time.monotonic()

                if post_v_stage == 0:
                    post_v_act0 = _flip_actuator(
                        vcfg.offset_v_actuator, vcfg.offset_v_flip
                    )
                    if vcfg.offset_v_mm > 0.5 and vcfg.pre_actuator_tilt_delta != 0:
                        sid_pre = vcfg.pre_actuator_tilt_servo
                        d_pre = vcfg.pre_actuator_tilt_delta
                        log.info(
                            "POST_V pre-actuator: S%d relative %+d raw @ speed %d (before %s %.1f mm)",
                            sid_pre,
                            d_pre,
                            vcfg.pre_actuator_tilt_speed,
                            post_v_act0,
                            vcfg.offset_v_mm,
                        )
                        r_pre = rpc(
                            {
                                "cmd": "multi_servo_move",
                                "servos": {str(sid_pre): int(d_pre)},
                                "speed": int(vcfg.pre_actuator_tilt_speed),
                            }
                        )
                        if r_pre and r_pre.get("status") != "ok":
                            log.warning("POST_V pre-actuator tilt: %s", r_pre)
                        st_pre = float(vcfg.pre_actuator_tilt_settle_sec)
                        if st_pre > 0:
                            time.sleep(st_pre)
                    if vcfg.offset_v_mm > 0.5:
                        r = rpc(
                            {
                                "cmd": "actuator",
                                "action": post_v_act0,
                                "distance_mm": vcfg.offset_v_mm,
                            }
                        )
                        if r and r.get("status") != "ok":
                            log.warning("POST_V extend actuator: %s", r)
                        log.info("POST_V extend: %s %.1f mm", post_v_act0, vcfg.offset_v_mm)
                    else:
                        log.info("POST_V extend skipped (vertical offset mm ~ 0)")
                    post_v_stage = 1
                    post_v_start = now_t
                elif post_v_stage == 1 and now_t - post_v_start >= vcfg.offset_v_wait_sec:
                    rev = "retract" if post_v_act0 == "extend" else "extend"
                    if vcfg.offset_v_mm > 0.5:
                        rr = rpc(
                            {
                                "cmd": "actuator",
                                "action": rev,
                                "distance_mm": vcfg.offset_v_mm,
                            }
                        )
                        if rr and rr.get("status") != "ok":
                            log.warning("POST_V retract actuator: %s", rr)
                        log.info("POST_V retract: %s %.1f mm", rev, vcfg.offset_v_mm)
                    if vcfg.offset_v_extra_mm > 0.5:
                        act_e = post_v_act0
                        r2 = rpc(
                            {
                                "cmd": "actuator",
                                "action": act_e,
                                "distance_mm": vcfg.offset_v_extra_mm,
                            }
                        )
                        if r2 and r2.get("status") != "ok":
                            log.warning("POST_V extra actuator leg: %s", r2)
                        log.info(
                            "POST_V extra reach: %s %.1f mm (then %.1fs settle)",
                            act_e,
                            vcfg.offset_v_extra_mm,
                            vcfg.offset_v_extra_wait_sec,
                        )
                        post_v_stage = 3
                        post_v_start = now_t
                    else:
                        finish_post_v_actuator()
                elif post_v_stage == 3 and now_t - post_v_start >= vcfg.offset_v_extra_wait_sec:
                    finish_post_v_actuator()
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(post_sleep)
                continue


            if phase == Phase.POST_WRIST:
                now_tw = time.monotonic()
                if not post_wrist_armed:
                    sid = vcfg.pre_press_wrist_servo
                    goal: Optional[int] = None
                    if vcfg.pre_press_wrist_abs is not None:
                        goal = vcfg.pre_press_wrist_abs & 0xFFFF
                        log.info(
                            "POST_WRIST: S%d -> %d (absolute — vertical actuator align before press)",
                            sid,
                            goal,
                        )
                    else:
                        status_wr = rpc({"cmd": "status"})
                        if status_wr and isinstance(status_wr.get("positions"), dict):
                            last_positions.update(status_wr["positions"])
                        cur = last_positions.get(str(sid))
                        if cur is None:
                            log.warning(
                                "POST_WRIST: no telemetry for S%d — skipping wrist move",
                                sid,
                            )
                            set_phase(Phase.PRESS)
                            press_armed = False
                            perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                            time.sleep(post_sleep)
                            continue
                        d = vcfg.pre_press_wrist_delta
                        assert d is not None
                        goal = (int(cur) + d) & 0xFFFF
                        log.info(
                            "POST_WRIST: S%d %d -> %d (delta %+d)",
                            sid,
                            int(cur),
                            goal,
                            d,
                        )
                    rpc(
                        {
                            "cmd": "multi_servo_move",
                            "servos": {str(sid): goal},
                            "speed": vcfg.pre_press_wrist_speed,
                        }
                    )
                    post_wrist_armed = True
                    post_wrist_start = now_tw
                elif now_tw - post_wrist_start >= vcfg.pre_press_wrist_wait_sec:
                    post_back = os.environ.get("VISION_POST_CYCLE_BACK_JSON", "").strip()
                    if post_back:
                        set_phase(Phase.POST_EXIT)
                        post_exit_armed = False
                    elif vcfg.revert_json_rel:
                        set_phase(Phase.REVERT)
                    else:
                        set_phase(Phase.PRESS)
                    press_armed = False
                    revert_armed = False
                    post_wrist_armed = False
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(post_sleep)
                continue


            if phase == Phase.POST_EXIT:
                if not post_exit_armed:
                    post_exit_armed = True
                    run_post_cycle_exit_sequence()
                    if carbot_shell_loop:
                        exit_for_carbot_shell(
                            "POST_EXIT complete — exiting (launcher: pick button; back.json already played)"
                        )
                    set_phase(Phase.DONE)
                    log.info("POST_EXIT complete (CARBOT_SHELL_LOOP off — staying in vision DONE)")
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(post_sleep)
                continue


            if phase == Phase.REVERT:
                if not revert_armed:
                    revert_armed = True
                    rel = vcfg.revert_json_rel
                    if not dry_motion and (
                        pan_short_home_for_revert_u16 is not None
                        or tilt_short_home_for_revert_u16 is not None
                    ):
                        st_rv = rpc({"cmd": "status"})
                        pos_rv = (
                            st_rv["positions"]
                            if st_rv and isinstance(st_rv.get("positions"), dict)
                            else None
                        )
                        if pos_rv is None:
                            log.warning(
                                "REVERT: status failed — playing %s without pan/tilt home pre-move",
                                rel,
                            )
                        else:
                            if pan_short_home_for_revert_u16 is not None:
                                curp = pos_rv.get(str(pan))
                                gh_p = int(pan_short_home_for_revert_u16) & 0xFFFF
                                if curp is not None:
                                    dp = _delta_raw_rel_to_goal(int(curp), gh_p)
                                    if dp != 0:
                                        log.info(
                                            "REVERT pre-move: pan S%d abs home raw=%d, then %s",
                                            pan,
                                            gh_p,
                                            rel,
                                        )
                                        rpc(
                                            {
                                                "cmd": "servo_move",
                                                "servo_id": pan,
                                                "value": gh_p,
                                                "speed": search_home_speed,
                                                "mode": "abs",
                                            }
                                        )
                                        wait_servo_at_anchor(pan, gh_p)
                            if tilt_short_home_for_revert_u16 is not None:
                                gh = int(tilt_short_home_for_revert_u16) & 0xFFFF
                                st_t = rpc({"cmd": "status"})
                                pos_t = (
                                    st_t["positions"]
                                    if st_t and isinstance(st_t.get("positions"), dict)
                                    else pos_rv
                                )
                                curt = pos_t.get(str(tilt))
                                if curt is not None:
                                    dt = _delta_raw_rel_to_goal(int(curt), gh)
                                    if dt != 0:
                                        log.info(
                                            "REVERT pre-move: tilt S%d abs home raw=%d, then %s",
                                            tilt,
                                            gh,
                                            rel,
                                        )
                                        rpc(
                                            {
                                                "cmd": "servo_move",
                                                "servo_id": tilt,
                                                "value": gh,
                                                "speed": search_home_speed,
                                                "mode": "abs",
                                            }
                                        )
                                        wait_servo_at_anchor(tilt, gh)
                                    else:
                                        log.info(
                                            "REVERT pre-move: tilt S%d already at memorised raw=%d — %s",
                                            tilt,
                                            gh,
                                            rel,
                                        )
                                else:
                                    log.warning(
                                        "REVERT: no tilt telemetry — playing %s without S%d pre-move",
                                        rel,
                                        tilt,
                                    )
                    elif tilt_short_home_for_revert_u16 is None and pan_short_home_for_revert_u16 is None:
                        log.warning(
                            "REVERT: no memorised pan/tilt home — playing %s without S6/S7 pre-move",
                            rel,
                        )
                    log.info("REVERT: playing %s (neutral pose)", rel)
                    rpc({"cmd": "play", "file": rel, "loop": False})
                    if not dry_motion and not wait_motion_playback(
                        float(
                            os.environ.get("VISION_REVERT_PLAYBACK_TIMEOUT_SEC", "240")
                        )
                    ):
                        log.warning(
                            "REVERT playback did not report finished within timeout"
                        )
                    rpc({"cmd": "freeze"})
                    if carbot_shell_loop:
                        exit_for_carbot_shell(
                            "Cycle complete — exiting (launcher: pick button)"
                        )
                    log.info(
                        "REVERT finished — staying in vision (set CARBOT_SHELL_LOOP=1 when using carbot.sh)"
                    )
                    set_phase(Phase.DONE)
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(post_sleep)
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
                    set_phase(Phase.DONE)
                    log.info("Sequence complete (DONE)")
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(post_sleep)
                continue


            if phase == Phase.DONE:
                if carbot_shell_loop:
                    exit_for_carbot_shell(
                        "Sequence complete (DONE) — exiting for launcher (pick button)"
                    )
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(done_sleep)
                continue


            if not inferred:
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(loop_idle_sleep)
                continue


            # ── SEARCH ───────────────────────────────────────────────────────
            # Must run when there is NO target — bilateral tilt-only sweep (or legacy pattern).
            if phase == Phase.SEARCH:
                if target is None:
                    if search_bilateral:
                        if search_need_reanchor or anchor_tilt_u16 is None:
                            st = rpc({"cmd": "status"})
                            if st and isinstance(st.get("positions"), dict):
                                pos = st["positions"]
                                ap = pos.get(str(pan))
                                at = pos.get(str(tilt))
                                if tilt_short_home_for_revert_u16 is not None:
                                    anchor_tilt_u16 = int(tilt_short_home_for_revert_u16) & 0xFFFF
                                    anchor_pan_u16 = (
                                        int(ap) & 0xFFFF if ap is not None else None
                                    )
                                    search_need_reanchor = False
                                    sweep_slot = 0
                                    sweep_leg_t0 = now
                                    log.info(
                                        "SEARCH sweep anchor S%d raw=%d (same as post-short "
                                        "memorised home; legs %.0fs each; pan not swept) S%d=%s",
                                        tilt,
                                        anchor_tilt_u16,
                                        search_sweep_sec,
                                        pan,
                                        str(anchor_pan_u16) if anchor_pan_u16 is not None else "?",
                                    )
                                elif at is not None:
                                    anchor_tilt_u16 = int(at) & 0xFFFF
                                    anchor_pan_u16 = (
                                        int(ap) & 0xFFFF if ap is not None else None
                                    )
                                    search_need_reanchor = False
                                    sweep_slot = 0
                                    sweep_leg_t0 = now
                                    log.info(
                                        "SEARCH captured live tilt S%d raw=%d (one-shot sweep "
                                        "home; legs %.0fs each, then rel home to this raw; pan "
                                        "not swept) S%d=%s",
                                        tilt,
                                        anchor_tilt_u16,
                                        search_sweep_sec,
                                        pan,
                                        str(anchor_pan_u16) if anchor_pan_u16 is not None else "?",
                                    )
                                else:
                                    perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                                    time.sleep(search_sleep)
                                    continue
                            else:
                                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                                time.sleep(search_sleep)
                                continue

                        if sweep_slot in (1, 3):
                            st2 = rpc({"cmd": "status"})
                            cur_raw: Optional[int] = None
                            if st2 and isinstance(st2.get("positions"), dict):
                                ct = st2["positions"].get(str(tilt))
                                if ct is not None:
                                    cur_raw = int(ct) & 0xFFFF
                            if anchor_tilt_u16 is not None:
                                goal = int(anchor_tilt_u16) & 0xFFFF
                                if cur_raw is not None:
                                    dhome = _delta_raw_rel_to_goal(int(cur_raw), goal)
                                    if dhome != 0:
                                        rpc(
                                            {
                                                "cmd": "servo_move",
                                                "servo_id": tilt,
                                                "value": goal,
                                                "speed": search_home_speed,
                                                "mode": "abs",
                                            }
                                        )
                                    log.info(
                                        "SEARCH homing tilt S%d abs → anchor raw=%d "
                                        "(telemetry was %s) after %s leg; waiting settle tol=%d raw",
                                        tilt,
                                        goal,
                                        str(cur_raw) if cur_raw is not None else "?",
                                        "first" if sweep_slot == 1 else "second",
                                        search_home_tol_raw,
                                    )
                                else:
                                    log.warning(
                                        "SEARCH homing: no tilt telemetry — skipping rel home "
                                        "before next leg (anchor raw=%d)",
                                        goal,
                                    )
                                if not dry_motion and cur_raw is not None:
                                    wait_servo_at_anchor(tilt, goal)
                            sweep_slot = (sweep_slot + 1) % 4
                            sweep_leg_t0 = time.monotonic()
                            perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                            time.sleep(search_sleep)
                            continue

                        if now - sweep_leg_t0 >= search_sweep_sec:
                            sweep_slot += 1
                            perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                            time.sleep(search_sleep)
                            continue

                        step_mag = max(1, int(search_step))
                        if sweep_slot == 0:
                            tilt_delta = step_mag * search_right_sign
                        else:
                            tilt_delta = -step_mag * search_right_sign
                        rpc(
                            {
                                "cmd": "servo_move",
                                "servo_id": tilt,
                                "value": int(tilt_delta),
                                "speed": search_spd,
                            }
                        )
                    else:
                        sid, delta = moves[move_i % len(moves)]
                        move_i += 1
                        rpc(
                            {
                                "cmd": "servo_move",
                                "servo_id": sid,
                                "value": int(delta),
                                "speed": search_spd,
                            }
                        )
                        log.info(
                            "SEARCH sweep: S%d delta=%+d (move %d)",
                            sid,
                            delta,
                            move_i,
                        )
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(search_sleep)
                continue


            # No target — ALIGN / APPROACH have nothing to act on.
            if target is None:
                perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
                time.sleep(loop_idle_sleep)
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
                    arm_cooldown_ticks = 1   # was 2 — ~÷1.2 faster first approach step
                    approach_arm_steps_sent = 0
                    if lock_target_after_align and tracked_target is not None:
                        target_lock_active = True
                    set_phase(Phase.APPROACH)



            # ── APPROACH ─────────────────────────────────────────────────────
            elif phase == Phase.APPROACH:
                area = (target.bbox.w * target.bbox.h) / max(1.0, float(w_px * h_px))
                if (
                    area >= app_area
                    and approach_arm_steps_sent >= approach_min_arm_steps_for_area
                ):
                    log.info(
                        "Visual stand-off reached (area=%.3f >= %.3f, arm_steps=%d/%d for area)",
                        area,
                        app_area,
                        approach_arm_steps_sent,
                        approach_min_arm_steps_for_area,
                    )
                    if vcfg.offset_after_approach:
                        set_phase(Phase.POST_H)
                        post_h_ticks = 0
                        post_v_start = None
                        post_v_stage = 0
                        press_armed = False
                        tail = (
                            " → POST_WRIST (S%d)"
                            % (vcfg.pre_press_wrist_servo,)
                            if (
                                vcfg.pre_press_wrist_abs is not None
                                or vcfg.pre_press_wrist_delta is not None
                            )
                            else ""
                        )
                        log.info("→ POST_H (lateral) → POST_V (vertical mm)%s → PRESS", tail)
                    else:
                        rpc({"cmd": "freeze"})
                        set_phase(Phase.DONE)
                    continue
                if (
                    area >= app_area
                    and approach_arm_steps_sent < approach_min_arm_steps_for_area
                ):
                    log.debug(
                        "Approach area=%.3f >= %.3f but arm_steps=%d/%d (for area) — advancing arm",
                        area,
                        app_area,
                        approach_arm_steps_sent,
                        approach_min_arm_steps_for_area,
                    )
                if abs(ex) > pan_thr or abs(ey) > pan_thr:
                    pan_tilt_correct(ex, ey, spd=track_spd, max_d=max_pan_ap)
                    log.debug("Pan/tilt micro-correct: ex=%.1f ey=%.1f err=%.1f", ex, ey, err)
                if arm_cooldown_ticks > 0:
                    arm_cooldown_ticks -= 1
                    log.debug("Arm cooldown: %d ticks remaining", arm_cooldown_ticks)
                    continue
                if err <= arm_thr:
                    fired = fire_arm_step()
                    if fired:
                        arm_cooldown_ticks = step_cooldown
                        approach_arm_steps_sent += 1
                else:
                    log.debug(
                        "Arm step PAUSED — err=%.1fpx > arm_thr=%.1fpx "
                        "(waiting for pan/tilt to re-centre)",
                        err,
                        arm_thr,
                    )


            perf.mark_frame((time.monotonic() - frame_t0) * 1000.0, infer_ms)
            time.sleep(loop_idle_sleep)



    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        cleanup_vision_hw()




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














