"""Optional motion recording via nina-link (same Dynamixel stack as ``record-action``).

Enable with ``NINA_LINK_ENABLE_RECORD_BRIDGE=1``. Do not run while Sirena UI or another
process holds the serial bus.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

from nina.link_daemon.dynamixel_bundle import (
    bus_lock,
    get_action_runner_bundle,
    release_bundle_serial,
)

log = logging.getLogger("nina.link_daemon.record_bridge")

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class RecordingCancelled(Exception):
    """Operator requested stop via ``POST /v1/actions/record/stop``."""


_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "phase": "idle",
    "name": None,
    "error": None,
}

_cancel_event = threading.Event()


def get_record_status() -> Dict[str, Any]:
    with _state_lock:
        return dict(_state)


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def request_cancel_record() -> Dict[str, Any]:
    """Signal the active session (if any) to stop. Best-effort torque restore runs in the worker."""
    with _state_lock:
        if _state.get("phase") == "idle":
            return {"ok": False, "error": "Not recording."}
    _cancel_event.set()
    return {"ok": True}


def _restore_torque_safe(dxl: Optional[Any]) -> None:
    if dxl is None:
        return
    try:
        with bus_lock:
            dxl.set_torque_all(True)
    except Exception:
        log.exception("record cancel: could not re-enable torque")


def queue_record_session(
    *,
    name: str,
    seconds: float,
    hz: float,
    countdown: float,
    hold_after: bool,
    register_manifest: bool,
) -> Dict[str, Any]:
    raw = name.strip()
    if not _NAME_RE.match(raw):
        return {
            "ok": False,
            "error": "invalid name (use letters, digits, _ - ; must start with a letter)",
        }
    with _state_lock:
        if _state.get("phase") != "idle":
            return {"ok": False, "error": "recording busy", "status": dict(_state)}
        _cancel_event.clear()
        _state.update(
            {"phase": "queued", "name": raw, "error": None, "last_saved": None}
        )

    def run() -> None:
        dxl_for_cancel: Optional[Any] = None
        try:
            if _cancel_event.is_set():
                raise RecordingCancelled()
            _set_state(phase="starting", name=raw, error=None)
            ar, dxl, _settings = get_action_runner_bundle()
            dxl_for_cancel = dxl
            from nina.app.main import ensure_motors_ready

            rd = ar.manifest_path.parent / "recordings"
            rd.mkdir(parents=True, exist_ok=True)

            with bus_lock:
                if _cancel_event.is_set():
                    raise RecordingCancelled()
                _set_state(phase="prepare")
                ensure_motors_ready(dxl)
                log.info("record session: releasing torque for %s", raw)
                dxl.set_torque_all(False)

                cd = max(0.0, float(countdown))
                if cd > 0:
                    _set_state(phase="countdown")
                    whole = int(cd)
                    for remaining in range(whole, 0, -1):
                        if _cancel_event.is_set():
                            raise RecordingCancelled()
                        _set_state(countdown_remaining_sec=remaining)
                        time.sleep(1.0)
                    frac = cd - whole
                    if frac > 0:
                        if _cancel_event.is_set():
                            raise RecordingCancelled()
                        time.sleep(frac)

                interval = 1.0 / max(0.5, float(hz))
                sample_count = max(1, int(float(seconds) * float(hz)))
                frames: List[Any] = []
                _set_state(phase="recording", samples_total=sample_count, samples_done=0)
                for i in range(sample_count):
                    if _cancel_event.is_set():
                        raise RecordingCancelled()
                    frames.append(dxl.capture_frame(duration=interval))
                    _set_state(samples_done=i + 1)
                    time.sleep(interval)

                _set_state(phase="saving")
                out_path = rd / f"{raw}.json"
                payload = {
                    "robot": "nina",
                    "description": f"Recorded action: {raw}",
                    "frame_count": len(frames),
                    "frames": frames,
                }
                out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

                if hold_after:
                    dxl.set_torque_all(True)
                else:
                    log.info("record session: leaving torque released for %s", raw)

                if register_manifest:
                    ar.register_action(raw, f"recordings/{raw}.json")

            _set_state(
                phase="idle",
                name=None,
                error=None,
                last_saved=str(out_path),
                registered=bool(register_manifest),
                samples_total=None,
                samples_done=None,
                countdown_remaining_sec=None,
            )
        except RecordingCancelled:
            log.info("record session cancelled by operator")
            _restore_torque_safe(dxl_for_cancel)
            _set_state(
                phase="idle",
                name=None,
                error="Recording cancelled (no file saved).",
                samples_total=None,
                samples_done=None,
                countdown_remaining_sec=None,
                last_saved=None,
            )
        except Exception as e:
            log.exception("record session failed")
            _restore_torque_safe(dxl_for_cancel)
            _set_state(
                phase="idle",
                name=None,
                error=str(e),
                samples_total=None,
                samples_done=None,
                countdown_remaining_sec=None,
            )
        finally:
            release_bundle_serial()

    threading.Thread(target=run, daemon=True, name=f"nina-record-{raw}").start()
    return {"ok": True, "queued": True, "name": raw}
