"""Vision-guided follow: keep a chosen face centred and near a reference size.

Uses `DriveController.drive_wheels` at ~15 Hz (same family as autonomy) so
motion stays smooth. Requires face detection; recognition matches a
named enrollee when ``target_name`` is set, otherwise tracks the largest
face box.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from sirena_ui.workers.drive_controller import MAX_SPEED_PCT, MIN_SPEED_PCT, DriveController
from sirena_ui.workers.vision_types import KIND_FACE, Detection

log = logging.getLogger("sirena_ui.face_follow")


def _bbox_area(det: Detection) -> int:
    x1, y1, x2, y2 = det.bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


class FaceFollowController(QObject):
    """Start/stop person follow; ingests ``Detection`` lists from VisionWorker."""

    status_message = pyqtSignal(str)

    def __init__(self, drive: DriveController, parent=None) -> None:
        super().__init__(parent)
        self._drive = drive
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(66)

        self._active = False
        self._target_name: Optional[str] = None
        self._ref_area: Optional[float] = None
        self._latest: List[Detection] = []
        self._lost_ticks = 0
        self._frame_wh: Tuple[int, int] = (640, 480)

        # ratio = face_area / reference_area; >1 means closer than at lock.
        # Tight close_stop kept the bot "hunting" with turn nudges in the
        # 0.88–1.08 band; 1.0 treats "at least as close as lock" as range-stop.
        self._area_far = 0.90
        self._area_close_stop = 1.0
        self._area_close_back = 1.18
        self._ang_dead = 0.11
        self._lost_max_ticks = 14

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
        self._lost_ticks = 0
        self._active = True
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
        self._timer.stop()
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
            self._timer.stop()
            self.status_message.emit("Follow: stopped (brake on)")
            return

        faces = [d for d in self._latest if d.kind == KIND_FACE]
        chosen = self._pick_face(faces)
        if chosen is None:
            self._lost_ticks += 1
            # Drain + stop immediately so the ~300ms drive heartbeat cannot
            # keep replaying the last SET while the face is out of frame.
            try:
                self._drive.stop(drain=True)
            except Exception:
                pass
            if self._lost_ticks >= self._lost_max_ticks:
                self._active = False
                self._timer.stop()
                self.status_message.emit(
                    "Follow: lost target — tap Start follow to retry"
                )
            return

        self._lost_ticks = 0
        x1, y1, x2, y2 = chosen.bbox
        fw, _fh = self._frame_wh
        cx = 0.5 * (x1 + x2)
        err_x = (cx - 0.5 * fw) / max(fw * 0.5, 1.0)
        err_x = max(-1.0, min(1.0, err_x))

        area = float(max(1, _bbox_area(chosen)))
        if self._ref_area is None:
            self._ref_area = area
            label = chosen.identity or chosen.label or "face"
            self.status_message.emit(f"Follow: locked {label} ({int(area)} px²)")

        ratio = area / self._ref_area

        base = float(MIN_SPEED_PCT)
        cruise = int(max(MIN_SPEED_PCT, min(MAX_SPEED_PCT, round(base * 0.7))))

        # Distance policy: too far -> forward; cosy band -> hold; closer -> stop; very close -> back
        if ratio > self._area_close_back:
            sp = int(max(MIN_SPEED_PCT, round(base * 0.5)))
            try:
                self._drive.drive_wheels("back", sp, "back", sp)
            except Exception as exc:
                log.debug("drive_wheels back: %s", exc)
            return

        if ratio > self._area_close_stop:
            try:
                self._drive.stop(drain=True)
            except Exception:
                pass
            if abs(err_x) > self._ang_dead:
                self._nudge_turn(err_x, int(round(base * 0.5)))
            return

        if ratio < self._area_far:
            # Approach: forward with heading mix
            yaw = err_x * 8.0
            ls = int(max(MIN_SPEED_PCT, min(MAX_SPEED_PCT, cruise + int(yaw))))
            rs = int(max(MIN_SPEED_PCT, min(MAX_SPEED_PCT, cruise - int(yaw))))
            try:
                self._drive.drive_wheels("forward", ls, "forward", rs)
            except Exception as exc:
                log.debug("drive_wheels forward: %s", exc)
            return

        # Size OK: centre only (close to reference size — hold still)
        if abs(err_x) <= self._ang_dead:
            try:
                self._drive.stop(drain=True)
            except Exception:
                pass
            return
        self._nudge_turn(err_x, int(round(base * 0.55)))

    def _nudge_turn(self, err_x: float, turn_sp: int) -> None:
        turn_sp = max(MIN_SPEED_PCT, min(MAX_SPEED_PCT, turn_sp))
        try:
            if err_x > 0:
                self._drive.drive_wheels("forward", turn_sp, "back", turn_sp)
            else:
                self._drive.drive_wheels("back", turn_sp, "forward", turn_sp)
        except Exception as exc:
            log.debug("nudge_turn: %s", exc)
