"""
Gripper-camera search + visual tracking for in-car window buttons.

Phases
------
1. **SEARCH** — No confident detection: nudge pan/tilt servos (relative moves) on a
   small pattern so the arm “looks around” until a button appears.
2. **TRACK** — Center the best detection in the frame using proportional control
   (pixel error → ``servo_move`` deltas on the same pan/tilt servos).

Requires ``motion_server.py`` running on the Jetson (default TCP 5000). Tune
servo IDs and gains for your arm kinematics (camera near gripper).

Run::

    export PYTHONPATH=.
    python -m vision.window_servo --preview

Open ``http://<jetson-ip>:8080/`` in a browser to see the annotated stream.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from enum import Enum, auto
from typing import List, Optional, Tuple

from .annotate import draw_detections, encode_jpeg
from .camera import open_gripper_camera
from .config import VisionConfig
from .detector import (
    build_detector,
    filter_by_allowlist,
    filter_by_confidence,
)
from .mjpeg_server import MJPEGServer
from .motion_client import motion_rpc
from .types import ButtonDetection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("carbot.vision.window_servo")


class Phase(Enum):
    SEARCH = auto()
    TRACK = auto()


def _best_detection(dets: List[ButtonDetection]) -> Optional[ButtonDetection]:
    if not dets:
        return None

    def score(d: ButtonDetection) -> float:
        a = max(1.0, d.bbox.w * d.bbox.h)
        return float(d.confidence) * (a ** 0.5)

    return max(dets, key=score)


def _search_moves(
    pan: int, tilt: int, step: int
) -> List[Tuple[int, int]]:
    s = max(1, int(step))
    half = max(1, s // 2)
    return [
        (pan, s),
        (pan, -s),
        (tilt, s),
        (tilt, -s),
        (pan, half),
        (pan, -half),
        (tilt, half),
        (tilt, -half),
    ]


def _clamp_int(v: float, lim: int) -> int:
    x = int(round(v))
    if x > lim:
        return lim
    if x < -lim:
        return -lim
    return x


def run(
    *,
    preview: bool,
    dry_motion: bool,
    motion_host: str,
    motion_port: int,
) -> None:
    vcfg = VisionConfig.from_env()
    detector = build_detector(vcfg)

    cam = open_gripper_camera()
    preview_port = int(os.environ.get("VISION_PREVIEW_PORT", "8080"))
    preview_host = os.environ.get("VISION_PREVIEW_HOST", "0.0.0.0")
    mjpeg: Optional[MJPEGServer] = None
    if preview:
        mjpeg = MJPEGServer(host=preview_host, port=preview_port)
        mjpeg.start_background()

    pan = int(os.environ.get("VISION_PAN_SERVO", "6"))
    tilt = int(os.environ.get("VISION_TILT_SERVO", "7"))
    search_step = int(os.environ.get("VISION_SEARCH_STEP", "90"))
    search_speed = int(os.environ.get("VISION_SEARCH_SPEED", "200"))
    track_speed = int(os.environ.get("VISION_TRACK_SPEED", "180"))
    kp_x = float(os.environ.get("VISION_KP_X", "0.35"))
    kp_y = float(os.environ.get("VISION_KP_Y", "0.35"))
    inv_x = -1.0 if os.environ.get("VISION_INVERT_PAN", "").strip() == "1" else 1.0
    inv_y = -1.0 if os.environ.get("VISION_INVERT_TILT", "").strip() == "1" else 1.0
    max_delta = int(os.environ.get("VISION_MAX_DELTA", "140"))
    dead_px = float(os.environ.get("VISION_DEADZONE_PX", "28"))
    lost_max = int(os.environ.get("VISION_LOST_FRAMES", "10"))
    infer_interval = float(os.environ.get("VISION_INFER_INTERVAL_SEC", "0.35"))
    jpeg_q = int(os.environ.get("VISION_PREVIEW_JPEG_QUALITY", "75"))

    freeze_on_start = os.environ.get("VISION_FREEZE_ON_START", "1").strip() not in (
        "0",
        "false",
        "no",
    )

    moves = _search_moves(pan, tilt, search_step)
    move_i = 0
    phase = Phase.SEARCH
    lost = 0
    last_infer = 0.0
    last_dets: List[ButtonDetection] = []

    if pan not in (6, 7) or tilt not in (6, 7):
        log.warning(
            "PAN_SERVO=%s TILT_SERVO=%s — visual search uses relative *deltas*; "
            "IDs 6–7 match carbot REL servos. Other IDs may behave incorrectly.",
            pan,
            tilt,
        )

    log.info(
        "window_servo motion=%s:%s dry_motion=%s preview=%s phase=SEARCH",
        motion_host,
        motion_port,
        dry_motion,
        preview,
    )

    def rpc(cmd: dict) -> Optional[dict]:
        if dry_motion:
            log.debug("dry_motion skip %s", cmd.get("cmd"))
            return {"status": "ok", "dry_run": True}
        return motion_rpc(motion_host, motion_port, cmd)

    if freeze_on_start and not dry_motion:
        r = rpc({"cmd": "freeze"})
        log.info("freeze → %s", r)

    try:
        while True:
            frame = cam.read()
            if frame is None:
                log.warning("No frame")
                time.sleep(0.2)
                continue

            h, w = frame.shape[:2]
            now = time.monotonic()
            inferred = False
            if now - last_infer >= infer_interval:
                last_infer = now
                inferred = True
                dets = detector.infer(frame, camera_id=0)
                dets = filter_by_confidence(dets, vcfg.confidence_threshold)
                dets = filter_by_allowlist(dets, vcfg.label_allowlist)
                last_dets = dets

            target = _best_detection(last_dets)

            if inferred:
                if target is None:
                    if phase == Phase.TRACK:
                        lost += 1
                        if lost >= lost_max:
                            log.info("Lost target → SEARCH")
                            phase = Phase.SEARCH
                            lost = 0
                else:
                    lost = 0
                    phase = Phase.TRACK

            subtitle = f"{phase.name} | det={len(last_dets)}"
            if target:
                subtitle += f" | {target.label}"
            vis = draw_detections(
                frame,
                last_dets,
                title="window_servo",
                subtitle=subtitle,
                primary=target,
            )
            if mjpeg:
                mjpeg.update_frame(encode_jpeg(vis, quality=jpeg_q))

            if not inferred:
                time.sleep(0.01)
                continue

            if dry_motion:
                time.sleep(0.02)
                continue

            if target is None:
                if phase == Phase.SEARCH:
                    sid, delta = moves[move_i % len(moves)]
                    move_i += 1
                    r = rpc(
                        {
                            "cmd": "servo_move",
                            "servo_id": sid,
                            "value": int(delta),
                            "speed": search_speed,
                        }
                    )
                    if r and r.get("status") != "ok":
                        log.warning("servo_move search: %s", r)
                time.sleep(0.02)
                continue

            ex = float(target.bbox.cx) - 0.5 * w
            ey = float(target.bbox.cy) - 0.5 * h
            if abs(ex) < dead_px and abs(ey) < dead_px:
                time.sleep(0.02)
                continue

            d_pan = _clamp_int(-inv_x * kp_x * ex, max_delta)
            d_tilt = _clamp_int(-inv_y * kp_y * ey, max_delta)

            if d_pan != 0:
                r = rpc(
                    {
                        "cmd": "servo_move",
                        "servo_id": pan,
                        "value": d_pan,
                        "speed": track_speed,
                    }
                )
                if r and r.get("status") != "ok":
                    log.warning("servo_move pan: %s", r)
            if d_tilt != 0:
                r = rpc(
                    {
                        "cmd": "servo_move",
                        "servo_id": tilt,
                        "value": d_tilt,
                        "speed": track_speed,
                    }
                )
                if r and r.get("status") != "ok":
                    log.warning("servo_move tilt: %s", r)

            if os.environ.get("VISION_APPROACH_ENABLE", "").strip() in ("1", "true", "yes"):
                area_frac = (target.bbox.w * target.bbox.h) / max(1.0, float(w * h))
                thr = float(os.environ.get("VISION_APPROACH_AREA_FRAC", "0.12"))
                if area_frac >= thr and abs(ex) < dead_px * 2 and abs(ey) < dead_px * 2:
                    mm = float(os.environ.get("VISION_APPROACH_MM", "3"))
                    r = rpc({"cmd": "actuator", "action": "extend", "distance_mm": mm})
                    if r:
                        log.info("approach extend → %s", r.get("status"))
                    time.sleep(float(os.environ.get("VISION_APPROACH_PAUSE", "0.3")))

            time.sleep(0.02)

    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        cam.release()
        if mjpeg:
            mjpeg.stop()


def main() -> None:
    p = argparse.ArgumentParser(description="Search + track window buttons (gripper camera)")
    p.add_argument(
        "--preview",
        action="store_true",
        help="Serve MJPEG browser preview (VISION_PREVIEW_PORT, default 8080).",
    )
    p.add_argument(
        "--dry-motion",
        action="store_true",
        help="Run vision only; do not send TCP commands to motion_server.",
    )
    p.add_argument("--motion-host", default=os.environ.get("MOTION_HOST", "127.0.0.1"))
    p.add_argument(
        "--motion-port",
        type=int,
        default=int(os.environ.get("MOTION_PORT", "5000")),
    )
    args = p.parse_args()
    run(
        preview=args.preview,
        dry_motion=args.dry_motion,
        motion_host=args.motion_host,
        motion_port=args.motion_port,
    )


if __name__ == "__main__":
    main()
