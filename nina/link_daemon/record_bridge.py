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
from pathlib import Path
from typing import Any, Dict, List, Optional

from nina.link_daemon.dynamixel_bundle import bus_lock, get_action_runner_bundle

log = logging.getLogger("nina.link_daemon.record_bridge")

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")

_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "phase": "idle",
    "name": None,
    "error": None,
}


def get_record_status() -> Dict[str, Any]:
    with _state_lock:
        return dict(_state)


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


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
        _state.update(
            {"phase": "queued", "name": raw, "error": None, "last_saved": None}
        )

    def run() -> None:
        try:
            _set_state(phase="starting", name=raw, error=None)
            ar, dxl = get_action_runner_bundle()
            from nina.app.main import ensure_motors_ready

            rd = ar.manifest_path.parent / "recordings"
            rd.mkdir(parents=True, exist_ok=True)

            with bus_lock:
                _set_state(phase="prepare")
                ensure_motors_ready(dxl)
                log.info("record session: releasing torque for %s", raw)
                dxl.set_torque_all(False)

                cd = max(0.0, float(countdown))
                if cd > 0:
                    _set_state(phase="countdown")
                    whole = int(cd)
                    for remaining in range(whole, 0, -1):
                        _set_state(countdown_remaining_sec=remaining)
                        time.sleep(1.0)
                    frac = cd - whole
                    if frac > 0:
                        time.sleep(frac)

                interval = 1.0 / max(0.5, float(hz))
                sample_count = max(1, int(float(seconds) * float(hz)))
                frames = []
                _set_state(phase="recording", samples_total=sample_count, samples_done=0)
                for i in range(sample_count):
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
        except Exception as e:
            log.exception("record session failed")
            _set_state(
                phase="idle",
                name=None,
                error=str(e),
                samples_total=None,
                samples_done=None,
                countdown_remaining_sec=None,
            )

    threading.Thread(target=run, daemon=True, name=f"nina-record-{raw}").start()
    return {"ok": True, "queued": True, "name": raw}
