"""MJPEG + vision controls for the companion app (optional ``VisionPipeline`` from sirena_ui)."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from sirena_ui.workers.vision_types import KIND_OBJECT

from nina.link_daemon.announcement_sentence import build_sentence
from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer

log = logging.getLogger("nina.link_daemon.vision_http")

_pipeline = None
_pipeline_err: Optional[str] = None
_pl_lock = threading.Lock()

_face_enabled = False
_object_enabled = False
_opts_lock = threading.Lock()

# Face enrollment (async thread + polling status for companion app).
_enroll_lock = threading.Lock()
_enroll_in_progress = False
_enroll_status: Dict[str, Any] = {
    "in_progress": False,
    "samples": 0,
    "target": 8,
    "last": None,
}

# Object announcement (gTTS + local play), same UX as Sirena UI "Play objects".
_announce_lock = threading.Lock()
_announce_last_at = 0.0
_announce_last_sentence: Optional[str] = None
_announce_last_error: Optional[str] = None
_ANNOUNCE_COOLDOWN_SEC = 1.5
_ANNOUNCE_CACHE = Path(__file__).resolve().parent.parent / "data" / "announcements"


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


def _enroll_progress_cb(collected: int, target: int) -> None:
    with _enroll_lock:
        _enroll_status["samples"] = int(collected)
        _enroll_status["target"] = int(target)


def start_enroll_face(name: str, target_samples: int = 8) -> Dict[str, Any]:
    """Queue face enrollment (``target_samples`` frames, default 8) on a background thread."""
    global _enroll_in_progress
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "Name cannot be empty."}
    ts = max(1, min(32, int(target_samples)))

    with _enroll_lock:
        if _enroll_in_progress:
            return {"ok": False, "error": "Enrollment already in progress."}
        _enroll_in_progress = True
        _enroll_status["in_progress"] = True
        _enroll_status["samples"] = 0
        _enroll_status["target"] = ts
        _enroll_status["last"] = None

    def run() -> None:
        global _enroll_in_progress
        last: Dict[str, Any]
        try:
            set_vision_options(face=True)
            p = get_pipeline()
            st = p.open()
            if not st.camera_open:
                last = {
                    "ok": False,
                    "samples": 0,
                    "attempts": 0,
                    "message": st.message or "Camera not open.",
                }
            else:
                r = p.enroll_face(
                    name,
                    target_samples=ts,
                    progress_cb=_enroll_progress_cb,
                )
                last = {
                    "ok": bool(r.ok),
                    "samples": int(r.samples),
                    "attempts": int(r.attempts),
                    "message": r.message,
                }
        except Exception as exc:
            log.exception("enroll_face")
            last = {
                "ok": False,
                "samples": 0,
                "attempts": 0,
                "message": str(exc),
            }
        with _enroll_lock:
            _enroll_status["last"] = last
            _enroll_status["in_progress"] = False
            _enroll_status["samples"] = 0
            _enroll_in_progress = False

    threading.Thread(target=run, daemon=True, name="vision-enroll").start()
    return {"ok": True, "queued": True, "target_samples": ts}


def enroll_status_snapshot() -> Dict[str, Any]:
    with _enroll_lock:
        return {
            "in_progress": bool(_enroll_in_progress),
            "samples": int(_enroll_status.get("samples", 0)),
            "target": int(_enroll_status.get("target", 8)),
            "last": _enroll_status.get("last"),
        }


def start_announce_objects() -> Dict[str, Any]:
    """Build a sentence from current object labels, gTTS to MP3, play on the Jetson (async)."""
    global _announce_last_at, _announce_last_sentence, _announce_last_error
    try:
        dets = last_detections_json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    labels = [str(d["label"]) for d in dets if d.get("kind") == KIND_OBJECT]
    sentence = build_sentence(labels)

    now = time.time()
    with _announce_lock:
        if (
            _announce_last_sentence == sentence
            and now - _announce_last_at < _ANNOUNCE_COOLDOWN_SEC
        ):
            return {
                "ok": True,
                "queued": False,
                "skipped": True,
                "sentence": sentence,
            }
        _announce_last_sentence = sentence
        _announce_last_at = now
        _announce_last_error = None

    _ANNOUNCE_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(sentence.encode("utf-8")).hexdigest()[:16]
    out_path = _ANNOUNCE_CACHE / f"ann_{key}.mp3"

    def run() -> None:
        global _announce_last_error
        try:
            if not out_path.exists():
                AudioGenerator.generate(sentence, out_path)
            player = AudioPlayer()
            if not player.is_supported:
                msg = "No audio player on robot (e.g. install mpg123)."
                _announce_last_error = msg
                log.warning(msg)
                return
            player.play(out_path)
        except AudioGeneratorError as exc:
            _announce_last_error = str(exc)
            log.warning("vision announce TTS: %s", exc)
        except Exception as exc:
            _announce_last_error = str(exc)
            log.exception("vision announce")

    threading.Thread(target=run, daemon=True, name="vision-announce").start()
    return {"ok": True, "queued": True, "sentence": sentence}


def announce_error_snapshot() -> Dict[str, Any]:
    with _announce_lock:
        err = _announce_last_error
    return {"error": err}
