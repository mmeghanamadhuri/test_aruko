"""Vision-guided follow: keep a chosen face centred and near a reference size.

Uses `DriveController.drive_wheels` at ~15 Hz (same family as autonomy) so
motion stays smooth. Requires face detection; recognition matches a
named enrollee when ``target_name`` is set, otherwise tracks the largest
face box.

Several consecutive no-face ticks (after a lock) start a slow in-place
scan (~360°). Single-frame YuNet blips no longer reset the lost counter,
so the scan actually runs when the subject leaves the frame.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.vision_types import KIND_FACE, Detection

log = logging.getLogger("sirena_ui.face_follow")

# Follow speeds — `drive_wheels` passes these through to PWM (can be < MIN_SPEED_PCT).
_SPEED_APPROACH_PCT = 12
_SPEED_CRUISE_PCT = 10
_SPEED_BACK_PCT = 10
_SPEED_NUDGE_PCT = 10

_SEARCH_SPEED_PCT = int(os.environ.get("NINA_FOLLOW_SEARCH_PCT", "10"))
_SEARCH_SPIN_SEC = float(os.environ.get("NINA_FOLLOW_SEARCH_SPIN_SEC", "9.0"))
# Require this many consecutive no-face ticks (after a lock) before 360° search.
# Single-frame YuNet blips must not reset this counter (see _face_present_streak).
_LOST_ENTER_SEARCH_TICKS = max(1, int(os.environ.get("NINA_FOLLOW_LOST_TICKS", "4")))
# Face must be seen this many ticks in a row before we trust it (clear lost streak / exit search).
_FACE_CONFIRM_TICKS = max(1, int(os.environ.get("NINA_FOLLOW_CONFIRM_TICKS", "2")))


def _bbox_area(det: Detection) -> int:
    x1, y1, x2, y2 = det.bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


def _greeting_name(chosen: Detection) -> str:
    """Name passed to FaceGreeter (\"Hello <name>\")."""
    if chosen.identity and str(chosen.identity).strip():
        return str(chosen.identity).strip()
    lab = (chosen.label or "").strip()
    if lab and lab.casefold() not in ("face", "person"):
        return lab
    return "friend"


class FaceFollowController(QObject):
    """Start/stop person follow; ingests ``Detection`` lists from VisionWorker."""

    status_message = pyqtSignal(str)
    #: Emitted when follow locks a target (new ``_ref_area``). Payload is the
    #: greeting name for ``FaceGreeter`` (enrolled name, label, or ``friend``).
    face_latched = pyqtSignal(str)

    def __init__(self, drive: DriveController, parent=None) -> None:
        super().__init__(parent)
        self._drive = drive
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(66)
        self._nudge_pulse_timer = QTimer(self)
        self._nudge_pulse_timer.setSingleShot(True)
        self._nudge_pulse_timer.timeout.connect(self._finish_nudge_pulse)

        self._active = False
        self._target_name: Optional[str] = None
        self._ref_area: Optional[float] = None
        self._latest: List[Detection] = []
        self._frame_wh: Tuple[int, int] = (640, 480)

        self._searching = False
        self._search_elapsed_s = 0.0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0

        # ratio = face_area / reference_area (ref = bbox area at lock).
        self._area_far = 0.92
        self._area_blend_far = 0.82
        self._area_close_stop = 1.06
        self._area_close_back = 1.25
        self._ang_dead = 0.09

    def set_frame_size(self, w: int, h: int) -> None:
        self._frame_wh = (max(1, int(w)), max(1, int(h)))

    def ingest_detections(self, dets: List[Detection]) -> None:
        self._latest = list(dets)

    def start(self, target_name: Optional[str]) -> bool:
        """Begin following. ``target_name`` None/'' = largest face; else identity."""
        if target_name is not None and not isinstance(target_name, str):
            target_name = str(target_name)
        st = self._drive.state()
        if st.get("brake"):
            self.status_message.emit("Follow: engage brake off on Drive first")
            return False
        self._target_name = (target_name or "").strip() or None
        self._ref_area = None
        self._searching = False
        self._search_elapsed_s = 0.0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0
        self._active = True
        self._nudge_pulse_timer.stop()
        self._timer.start()
        who = self._target_name or "largest face"
        self.status_message.emit(f"Follow: seeking {who}…")
        try:
            self._drive.ensure_hardware()
        except Exception as exc:
            log.debug("ensure_hardware: %s", exc)
        return True

    def stop(self) -> None:
        self._active = False
        self._searching = False
        self._search_elapsed_s = 0.0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0
        self._timer.stop()
        self._nudge_pulse_timer.stop()
        self._target_name = None
        self._ref_area = None
        try:
            self._drive.stop(drain=True)
        except Exception as exc:
            log.debug("drive.stop: %s", exc)
        self.status_message.emit("Follow: off")

    def is_active(self) -> bool:
        return bool(self._active)

    def _pick_face(self, faces: List[Detection]) -> Optional[Detection]:
        if not faces:
            return None
        if self._target_name is None:
            return max(faces, key=_bbox_area)
        tn = self._target_name.casefold()
        matches = [
            f
            for f in faces
            if f.identity and f.identity.strip().casefold() == tn
        ]
        return matches[0] if matches else None

    def _tick(self) -> None:
        if not self._active:
            return
        if self._drive.state().get("brake"):
            try:
                self._drive.stop(drain=True)
            except Exception:
                pass
            self._active = False
            self._searching = False
            self._had_lock = False
            self._lost_streak = 0
            self._face_present_streak = 0
            self._timer.stop()
            self._nudge_pulse_timer.stop()
            self.status_message.emit("Follow: stopped (brake on)")
            return

        faces = [d for d in self._latest if d.kind == KIND_FACE]
        chosen = self._pick_face(faces)
        if chosen is not None:
            self._handle_track(chosen)
            return

        self._handle_lost()

    def _handle_lost(self) -> None:
        self._face_present_streak = 0
        self._nudge_pulse_timer.stop()
        try:
            self._drive.stop(drain=True)
        except Exception:
            pass

        # Before we have ever locked a face, hold still — do not 360° scan.
        if not self._had_lock:
            self._searching = False
            self._search_elapsed_s = 0.0
            self._lost_streak = 0
            return

        self._lost_streak += 1
        if self._lost_streak < _LOST_ENTER_SEARCH_TICKS:
            # Brief dropout / motion blur — wait before declaring lost.
            return

        if not self._searching:
            self._searching = True
            self._search_elapsed_s = 0.0
            self._ref_area = None
            self.status_message.emit("Follow: lost — scanning 360°…")
            return

        dt_s = self._timer.interval() / 1000.0
        self._search_elapsed_s += dt_s
        if self._search_elapsed_s >= _SEARCH_SPIN_SEC:
            self._active = False
            self._searching = False
            self._had_lock = False
            self._lost_streak = 0
            self._face_present_streak = 0
            self._timer.stop()
            self.status_message.emit(
                "Follow: lost after full scan — tap Start follow to retry"
            )
            return

        sp = max(1, min(100, _SEARCH_SPEED_PCT))
        try:
            # Consistent in-place turn (same handedness as _nudge_turn err_x > 0).
            self._drive.drive_wheels("forward", sp, "back", sp)
        except Exception as exc:
            log.debug("search spin: %s", exc)

    def _handle_track(self, chosen: Detection) -> None:
        lost_before = self._lost_streak
        self._face_present_streak += 1
        # Intermittent detections while the subject is gone used to reset
        # _lost_streak every frame and prevented 360° search from ever arming.
        if self._face_present_streak >= _FACE_CONFIRM_TICKS:
            self._lost_streak = 0

        if self._face_present_streak == 1 and (
            self._searching or lost_before >= 2
        ):
            # Distance reference is stale after real absence; skip on 1-frame blips.
            self._ref_area = None

        self._had_lock = True

        if self._searching:
            if self._face_present_streak < _FACE_CONFIRM_TICKS:
                # One-frame blip — keep scanning instead of stopping the spin.
                sp = max(1, min(100, _SEARCH_SPEED_PCT))
                try:
                    self._drive.drive_wheels("forward", sp, "back", sp)
                except Exception as exc:
                    log.debug("search spin (blip guard): %s", exc)
                return
            self._searching = False
            self._search_elapsed_s = 0.0
            self._ref_area = None
            self.status_message.emit("Follow: target reacquired")

        self._nudge_pulse_timer.stop()
        x1, y1, x2, y2 = chosen.bbox
        fw, _fh = self._frame_wh
        cx = 0.5 * (x1 + x2)
        err_x = (cx - 0.5 * fw) / max(fw * 0.5, 1.0)
        err_x = max(-1.0, min(1.0, err_x))

        area = float(max(1, _bbox_area(chosen)))
        if self._ref_area is None:
            self._ref_area = float(max(1, area))
            label = chosen.identity or chosen.label or "face"
            self.status_message.emit(f"Follow: locked {label} ({int(area)} px²)")
            self.face_latched.emit(_greeting_name(chosen))

        ratio = area / self._ref_area

        min_sp = _SPEED_CRUISE_PCT
        max_sp = _SPEED_APPROACH_PCT

        if ratio > self._area_close_back:
            try:
                self._drive.drive_wheels(
                    "back", _SPEED_BACK_PCT, "back", _SPEED_BACK_PCT
                )
            except Exception as exc:
                log.debug("drive_wheels back: %s", exc)
            return

        if ratio > self._area_close_stop:
            if abs(err_x) <= self._ang_dead:
                try:
                    self._drive.stop(drain=True)
                except Exception:
                    pass
                return
            self._nudge_turn(err_x, _SPEED_NUDGE_PCT)
            return

        if ratio < self._area_far:
            if ratio <= self._area_blend_far:
                cruise_sp = float(_SPEED_APPROACH_PCT)
            else:
                t = (ratio - self._area_blend_far) / max(
                    1e-6, self._area_far - self._area_blend_far
                )
                t = max(0.0, min(1.0, t))
                cruise_sp = _SPEED_APPROACH_PCT + (
                    _SPEED_CRUISE_PCT - _SPEED_APPROACH_PCT
                ) * t
            cruise = int(round(cruise_sp))
            # Blend toward centre without commanding a near–in-place spin.
            yaw = err_x * 7.0
            ls = int(max(min_sp, min(max_sp, cruise + int(yaw))))
            rs = int(max(min_sp, min(max_sp, cruise - int(yaw))))
            try:
                self._drive.drive_wheels("forward", ls, "forward", rs)
            except Exception as exc:
                log.debug("drive_wheels forward: %s", exc)
            return

        if abs(err_x) <= self._ang_dead:
            try:
                self._drive.stop(drain=True)
            except Exception:
                pass
            return
        self._nudge_turn(err_x, _SPEED_NUDGE_PCT)

    def _finish_nudge_pulse(self) -> None:
        if not self._active:
            return
        try:
            self._drive.stop(drain=True)
        except Exception:
            pass

    def _nudge_turn(self, err_x: float, turn_sp: int) -> None:
        if self._nudge_pulse_timer.isActive():
            return
        turn_sp = max(_SPEED_CRUISE_PCT, min(_SPEED_APPROACH_PCT, int(turn_sp)))
        try:
            if err_x > 0:
                self._drive.drive_wheels("forward", turn_sp, "back", turn_sp)
            else:
                self._drive.drive_wheels("back", turn_sp, "forward", turn_sp)
        except Exception as exc:
            log.debug("nudge_turn: %s", exc)
            return
        self._nudge_pulse_timer.start(200)
