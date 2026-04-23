#!/usr/bin/env python3
"""Quick benchmark for YOLO vs TensorRT detector latency."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from typing import Dict

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.config import VisionConfig
from vision.detector import build_detector


def bench_runtime(runtime: str, iterations: int, model_path: str, engine_path: str) -> float:
    os.environ["VISION_RUNTIME"] = runtime
    os.environ["VISION_MODEL_PATH"] = model_path
    os.environ["VISION_TRT_ENGINE_PATH"] = engine_path
    os.environ.setdefault("VISION_TRT_AUTO_EXPORT", "1")
    os.environ.setdefault("VISION_TRT_PRECISION", "fp16")

    cfg = VisionConfig.from_env()
    detector = build_detector(cfg)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    detector.infer(frame, camera_id=0)  # warmup
    t0 = time.perf_counter()
    for _ in range(iterations):
        detector.infer(frame, camera_id=0)
    elapsed = time.perf_counter() - t0
    return (elapsed / max(1, iterations)) * 1000.0


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark vision detector runtimes")
    p.add_argument("--iterations", type=int, default=20)
    p.add_argument("--model-path", default="best.pt")
    p.add_argument("--engine-path", default="best.engine")
    args = p.parse_args()

    results: Dict[str, str] = {}
    for runtime in ("yolo", "yolo_tensorrt"):
        try:
            ms = bench_runtime(
                runtime=runtime,
                iterations=args.iterations,
                model_path=args.model_path,
                engine_path=args.engine_path,
            )
            results[runtime] = f"{ms:.2f} ms"
        except Exception as exc:
            results[runtime] = f"error: {exc}"

    print("Detector benchmark results:")
    for rt, val in results.items():
        print(f"  {rt}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
