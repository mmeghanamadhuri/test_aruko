#!/usr/bin/env python3
"""Generate printable ArUco images for Nina (DICT_4X4_50 by default).

Output: ``nina/markers/aruco_4x4_50/marker_ID.png`` (black on white, 400 px).

**Printing:** open the PNG, print at 100% scale (no fit-to-page). For reliable
detection at distance, use a physical marker side length of at least ~12–15 cm
on matte paper; avoid glossy lamination glare.

**Defaults:** IDs 0–3 match ``NINA_ARUCO_TARGET_IDS`` (default ``0`` only).

Requires OpenCV with ``cv2.aruco`` (``pip install opencv-python-headless``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dict",
        default="DICT_4X4_50",
        help="OpenCV predefined dict name (default: DICT_4X4_50)",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default="0,1,2,3",
        help="Comma-separated marker IDs (default: 0,1,2,3)",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=400,
        help="Image side length in pixels (default: 400)",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: repo nina/markers/aruco_4x4_50)",
    )
    args = parser.parse_args()

    try:
        import cv2
    except ImportError:
        print("Install OpenCV: pip install opencv-python-headless", file=sys.stderr)
        return 1

    if not hasattr(cv2, "aruco"):
        print("OpenCV has no aruco module.", file=sys.stderr)
        return 1

    dname = args.dict.strip().upper()
    dict_id = getattr(cv2.aruco, dname, None)
    if dict_id is None:
        print(f"Unknown dictionary {args.dict!r}", file=sys.stderr)
        return 1
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)

    repo_root = Path(__file__).resolve().parents[1]
    out_dir = args.out_dir
    if out_dir is None:
        folder_map = {
            "DICT_4X4_50": "aruco_4x4_50",
            "DICT_6X6_250": "aruco_6x6_250",
        }
        sub = folder_map.get(dname, "aruco_" + dname.lower().replace("dict_", ""))
        out_dir = repo_root / "nina" / "markers" / sub
    out_dir.mkdir(parents=True, exist_ok=True)

    id_list = []
    for part in args.ids.split(","):
        part = part.strip()
        if part:
            id_list.append(int(part))

    for mid in id_list:
        img = None
        if hasattr(cv2.aruco, "generateImageMarker"):
            img = cv2.aruco.generateImageMarker(dictionary, mid, int(args.size))
        if img is None:
            img = _draw_marker_legacy(cv2, dictionary, mid, int(args.size))
        path = out_dir / f"marker_{mid}.png"
        if not cv2.imwrite(str(path), img):
            print(f"Failed to write {path}", file=sys.stderr)
            return 1
        print(path)

    print(f"Wrote {len(id_list)} marker(s) to {out_dir}")
    return 0


def _draw_marker_legacy(cv2, dictionary, mid: int, side: int):
    """OpenCV < 4.7 fallback."""
    img = [[255] * side for _ in range(side)]
    import numpy as np

    arr = np.array(img, dtype=np.uint8)
    cv2.aruco.drawMarker(dictionary, mid, side, arr, 1)
    return arr


if __name__ == "__main__":
    raise SystemExit(main())
