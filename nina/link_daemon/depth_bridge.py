"""RealSense D435 colorized depth stream for nina-link (shares one device for MJPEG + autonomy)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

log = logging.getLogger("nina.link_daemon.depth_bridge")

_lock = threading.RLock()
_refcount = 0
_depth: Any = None  # RealSenseD435 | None
_status: Dict[str, Any] = {
    "ok": True,
    "open": False,
    "message": "idle",
}


def _camera():
    global _depth
    if _depth is None:
        from nina.sensors.realsense_d435 import RealSenseD435

        _depth = RealSenseD435()
    return _depth


def acquire(reason: str = "consumer") -> Tuple[bool, str]:
    """Increment refcount and open the pipeline on first acquire."""
    global _refcount
    with _lock:
        _refcount += 1
        if _refcount > 1:
            ok = bool(_status.get("open"))
            return ok, str(_status.get("message", ""))
        cam = _camera()
        try:
            cam.open()
            _status["open"] = True
            _status["message"] = f"open ({reason})"
            log.info("Depth bridge opened (%s)", reason)
            return True, "depth ready"
        except Exception as exc:
            _refcount = 0
            _status["open"] = False
            _status["message"] = str(exc)
            log.warning("depth open failed: %s", exc)
            return False, str(exc)


def release(reason: str = "consumer") -> None:
    """Drop refcount; close pipeline on last release."""
    global _refcount
    with _lock:
        if _refcount <= 0:
            return
        _refcount -= 1
        if _refcount > 0:
            return
        cam = _depth
        if cam is None:
            return
        try:
            cam.set_color_publish(False)
        except Exception:
            pass
        try:
            cam.close()
        except Exception as exc:
            log.warning("depth close: %s", exc)
        _status["open"] = False
        _status["message"] = "closed"
        log.info("Depth bridge closed (%s)", reason)


def status_payload() -> Dict[str, Any]:
    with _lock:
        return {
            "ok": True,
            "camera_open": bool(_status.get("open")),
            "message": str(_status.get("message", "")),
            "refcount": _refcount,
        }


def shared_camera():
    """Return the singleton camera object; must hold at least one acquire()."""
    return _camera()


def iter_depth_mjpeg(
    should_stop: Callable[[], bool],
    fps_cap: float = 12.0,
) -> Iterator[bytes]:
    """Multipart JPEG frame bodies for ``StreamingResponse``."""
    import cv2

    ok, _msg = acquire("mjpeg_stream")
    if not ok:
        return

    cam = _camera()
    try:
        cam.set_color_publish(True)
    except Exception as exc:
        log.warning("set_color_publish: %s", exc)

    min_interval = 1.0 / max(1.0, min(30.0, fps_cap))
    while not should_stop():
        t0 = time.monotonic()
        try:
            tup = cam.latest_color_image()
            if tup is None:
                time.sleep(0.03)
                continue
            w, h, buf = tup
            import numpy as np

            arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
            enc_ok, jpeg = cv2.imencode(
                ".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 78]
            )
            if enc_ok:
                yield jpeg.tobytes()
        except Exception:
            log.exception("depth MJPEG frame")
            time.sleep(0.05)
        elapsed = time.monotonic() - t0
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    try:
        cam.set_color_publish(False)
    except Exception:
        pass
    release("mjpeg_stream")
