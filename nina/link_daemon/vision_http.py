"""MJPEG + vision controls for the companion app (optional ``VisionPipeline`` from sirena_ui)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

log = logging.getLogger("nina.link_daemon.vision_http")

_pipeline = None
_pipeline_err: Optional[str] = None
_pl_lock = threading.Lock()

_face_enabled = False
_object_enabled = False
_opts_lock = threading.Lock()


def _try_import_pipeline():
    global _pipeline, _pipeline_err
    with _pl_lock:
        if _pipeline is not None or _pipeline_err:
            return
        try:
            from sirena_ui.workers.vision_pipeline import VisionPipeline

            _pipeline = VisionPipeline()
            log.info("VisionPipeline constructed")
        except Exception as e:
            _pipeline_err = f"{type(e).__name__}: {e}"
            log.warning("VisionPipeline unavailable: %s", _pipeline_err)


def vision_pipeline_error() -> Optional[str]:
    _try_import_pipeline()
    return _pipeline_err


def get_pipeline():
    _try_import_pipeline()
    if _pipeline is None:
        raise RuntimeError(_pipeline_err or "VisionPipeline unavailable")
    return _pipeline


def vision_status_payload() -> Dict[str, Any]:
    try:
        p = get_pipeline()
        st = p.status()
        return {
            "ok": True,
            "camera_open": st.camera_open,
            "face_ready": st.face_ready,
            "object_ready": st.object_ready,
            "message": st.message,
            "face_enabled": _face_enabled,
            "object_enabled": _object_enabled,
        }
    except Exception as e:
        return {
            "ok": False,
            "camera_open": False,
            "message": str(e),
            "face_enabled": False,
            "object_enabled": False,
        }


def set_vision_options(
    *,
    face: Optional[bool] = None,
    object_: Optional[bool] = None,
    object_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    global _face_enabled, _object_enabled
    p = get_pipeline()
    err_face: Optional[str] = None
    err_obj: Optional[str] = None
    with _opts_lock:
        if face is not None:
            err_face = p.set_face_enabled(face)
            _face_enabled = bool(face) and err_face is None
            if err_face:
                _face_enabled = False
        if object_ is not None:
            err_obj = p.set_object_enabled(object_)
            _object_enabled = bool(object_) and err_obj is None
            if err_obj:
                _object_enabled = False
        if object_confidence is not None:
            p.set_object_confidence(float(object_confidence))
    out = vision_status_payload()
    out["toggle_face_error"] = err_face
    out["toggle_object_error"] = err_obj
    return out


def open_camera_if_needed() -> Dict[str, Any]:
    p = get_pipeline()
    st = p.open()
    return {
        "camera_open": st.camera_open,
        "message": st.message,
        "face_ready": st.face_ready,
        "object_ready": st.object_ready,
    }


def close_camera() -> None:
    try:
        p = get_pipeline()
        p.close()
    except Exception:
        log.exception("vision close")


def iter_mjpeg_frames(
    should_stop: Callable[[], bool],
    fps_cap: float = 15.0,
) -> Iterator[bytes]:
    """Yield multipart JPEG chunks (body fragments only; caller sets boundary)."""
    import cv2

    p = get_pipeline()
    st = p.open()
    if not st.camera_open:
        log.warning("MJPEG: camera not open: %s", st.message)
        return

    min_interval = 1.0 / max(1.0, min(30.0, fps_cap))
    while not should_stop():
        t0 = time.monotonic()
        try:
            annotated, _dets = p.step()
            if annotated is None:
                time.sleep(0.03)
                continue
            ok, jpeg = cv2.imencode(
                ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
            )
            if not ok:
                continue
            yield jpeg.tobytes()
        except Exception:
            log.exception("MJPEG frame")
            time.sleep(0.05)
        elapsed = time.monotonic() - t0
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)


def last_detections_json() -> List[Dict[str, Any]]:
    """Run one vision step and return serializable detections (for optional UI polling)."""
    p = get_pipeline()
    p.open()
    _frame, dets = p.step()
    out: List[Dict[str, Any]] = []
    for d in dets:
        out.append(
            {
                "kind": d.kind,
                "label": d.label,
                "confidence": float(d.confidence),
                "bbox": [int(d.bbox[0]), int(d.bbox[1]), int(d.bbox[2]), int(d.bbox[3])],
                "identity": d.identity,
                "identity_score": d.identity_score,
            }
        )
    return out
