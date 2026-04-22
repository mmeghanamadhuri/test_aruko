"""Continuous capture + inference loop (console logging)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import List, Optional

from .camera import MultiCamera
from .config import VisionConfig
from .detector import build_detector, filter_by_allowlist, filter_by_confidence
from .types import ButtonDetection, FrameResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("carbot.vision.runner")


def _summarize(dets: List[ButtonDetection]) -> str:
    if not dets:
        return "(none)"
    parts = [f"{d.label}:{d.confidence:.2f}" for d in dets]
    return ", ".join(parts)


def run_loop(json_lines: bool = False) -> None:
    cfg = VisionConfig.from_env()
    detector = build_detector(cfg)
    cams = MultiCamera(cfg.camera_indices)

    log.info(
        "Vision runner — runtime=%s cameras=%s model=%s thr=%.2f interval=%.2fs",
        cfg.runtime,
        cfg.camera_indices,
        cfg.model_id or "(mock)",
        cfg.confidence_threshold,
        cfg.infer_interval_sec,
    )

    try:
        while True:
            t0 = time.perf_counter()
            batch: List[FrameResult] = []

            for cam_idx, frame in cams.read_all():
                if frame is None:
                    log.warning("Camera %s: no frame", cam_idx)
                    batch.append(FrameResult(camera_id=cam_idx, detections=[]))
                    continue

                dets = detector.infer(frame, camera_id=cam_idx)
                dets = filter_by_confidence(dets, cfg.confidence_threshold)
                dets = filter_by_allowlist(dets, cfg.label_allowlist)
                batch.append(FrameResult(camera_id=cam_idx, detections=dets))

            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if json_lines:
                payload = {
                    "inference_ms": round(elapsed_ms, 2),
                    "cameras": [fr.as_dict() for fr in batch],
                }
                print(json.dumps(payload), flush=True)
            else:
                for fr in batch:
                    log.info(
                        "cam=%s %d det [%s] (%.1f ms)",
                        fr.camera_id,
                        len(fr.detections),
                        _summarize(fr.detections),
                        elapsed_ms / max(len(cfg.camera_indices), 1),
                    )

            time.sleep(max(0.0, cfg.infer_interval_sec))
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        cams.release()


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Car window button vision loop")
    p.add_argument(
        "--json-lines",
        action="store_true",
        help="Print one JSON object per frame cycle (for piping / logs).",
    )
    args = p.parse_args(argv)
    run_loop(json_lines=args.json_lines)


if __name__ == "__main__":
    main()
