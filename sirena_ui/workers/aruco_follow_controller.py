"""RGB ArUco approach: centre marker in view, drive to a target pixel area.

Stops when the marker bbox covers at least ``NINA_ARUCO_STOP_AREA_FRAC`` of
the frame (default 2%) so a visually large marker ends follow immediately.

Uses the same differential steering helper as person follow. Optional
``ObstacleField`` from lidar + depth (via :meth:`NinaService.fuse_obstacle_for_follow`)
slows or stops forward motion when the forward sector is tight.

Lost marker for ``NINA_ARUCO_LOST_SEC`` (default 5 s): in-place search by
turning right then left ``NINA_ARUCO_SEARCH_STEP_DEG`` (default 60°) per
cycle, repeating until the marker is visible again.

On arrival (marker large enough and centred), stops and requests the
caller to speak *I reached my destination* (Vision screen uses
:class:`ObjectAnnouncer`).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from nina.navigation.obstacle_field import ObstacleField
from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.vision_types import KIND_ARUCO, Detection

log = logging.getLogger("sirena_ui.aruco_follow")

_TICK_MS = max(16, int(os.environ.get("NINA_ARUCO_TICK_MS", "50")))
_SPEED_APPROACH_PCT = max(1, min(100, int(os.environ.get("NINA_ARUCO_APPROACH_PCT", "11"))))
_SPEED_CRUISE_PCT = max(1, min(100, int(os.environ.get("NINA_ARUCO_CRUISE_PCT", "9"))))
_SPEED_BACK_PCT = max(1, min(100, int(os.environ.get("NINA_ARUCO_BACK_PCT", "9"))))
_SEARCH_SPEED_PCT = max(1, min(100, int(os.environ.get("NINA_ARUCO_SEARCH_PCT", "28"))))
try:
    _LOST_SEC = float(os.environ.get("NINA_ARUCO_LOST_SEC", "5"))
except ValueError:
    _LOST_SEC = 5.0
_LOST_SEC = max(0.5, min(60.0, _LOST_SEC))
_SEARCH_STEP_DEG = max(5, int(os.environ.get("NINA_ARUCO_SEARCH_STEP_DEG", "60")))
_SEARCH_STEP_MS = max(50, int(os.environ.get("NINA_ARUCO_SEARCH_STEP_MS", "1400")))
_YAW_GAIN = float(os.environ.get("NINA_ARUCO_YAW_GAIN", "5.5"))
try:
    _YAW_ERR_BOOST = float(os.environ.get("NINA_ARUCO_YAW_ERR_BOOST", "0.85"))
except ValueError:
    _YAW_ERR_BOOST = 0.85
_YAW_ERR_BOOST = max(0.0, min(3.0, _YAW_ERR_BOOST))
try:
    _ERR_FWD_SCALE_MIN = float(os.environ.get("NINA_ARUCO_ERR_FWD_SCALE_MIN", "0.22"))
except ValueError:
    _ERR_FWD_SCALE_MIN = 0.22
_ERR_FWD_SCALE_MIN = max(0.08, min(0.95, _ERR_FWD_SCALE_MIN))
try:
    _ERR_FWD_SCALE_POWER = float(os.environ.get("NINA_ARUCO_ERR_FWD_SCALE_POWER", "1.0"))
except ValueError:
    _ERR_FWD_SCALE_POWER = 1.0
_ERR_FWD_SCALE_POWER = max(0.5, min(3.0, _ERR_FWD_SCALE_POWER))
_DEFAULT_MARKER_FRAC = 0.012
# When bbox area / full frame area reaches this, treat as "close enough" and stop
# (marker looks large in the image). Independent of fine centering.
try:
    _STOP_AREA_FRAC = float(os.environ.get("NINA_ARUCO_STOP_AREA_FRAC", "0.02"))
except ValueError:
    _STOP_AREA_FRAC = 0.02
_STOP_AREA_FRAC = max(0.004, min(0.45, _STOP_AREA_FRAC))
try:
    _FOLLOW_CLOSE_RATIO = float(os.environ.get("NINA_ARUCO_CLOSE_RATIO", "1.25"))
except ValueError:
    _FOLLOW_CLOSE_RATIO = 1.25
_FOLLOW_CLOSE_RATIO = max(1.01, min(2.5, _FOLLOW_CLOSE_RATIO))
try:
    _FOLLOW_AREA_FAR = float(os.environ.get("NINA_ARUCO_AREA_FAR", "0.88"))
except ValueError:
    _FOLLOW_AREA_FAR = 0.88
_FOLLOW_AREA_FAR = max(0.55, min(0.98, _FOLLOW_AREA_FAR))
_ANG_DEAD = float(os.environ.get("NINA_ARUCO_ANG_DEAD", "0.09"))
try:
    _FOLLOW_ANG_DEAD_CLOSE = float(os.environ.get("NINA_ARUCO_ANG_DEAD_CLOSE", "0.14"))
except ValueError:
    _FOLLOW_ANG_DEAD_CLOSE = 0.14


def _bbox_area(det: Detection) -> int:
    x1, y1, x2, y2 = det.bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


def _follow_steering_aruco(
    base_forward: float,
    err_x: float,
    *,
    yaw_gain: float,
    max_wheel: int,
) -> Tuple[str, int, str, int]:
    """Same mapping as face follow but with ArUco env-tuned err shaping."""
    max_w = max(1, min(100, int(max_wheel)))
    err_clamped = max(-1.0, min(1.0, err_x))
    err_mag = abs(err_clamped)
    fwd_scale = _ERR_FWD_SCALE_MIN + (1.0 - _ERR_FWD_SCALE_MIN) * (
        (1.0 - err_mag) ** _ERR_FWD_SCALE_POWER
    )
    base = float(base_forward) * fwd_scale
    yaw_mult = 1.0 + _YAW_ERR_BOOST * err_mag
    yaw = float(err_clamped) * float(yaw_gain) * yaw_mult
    ls = base + yaw
    rs = base - yaw

    if ls <= -0.05 or rs <= -0.05:
        sp = int(max(1.0, min(float(max_w), round(max(abs(ls), abs(rs))))))
        if err_clamped > 0:
            return ("forward", sp, "back", sp)
        return ("back", sp, "forward", sp)

    ls_i = max(1, min(max_w, int(round(ls))))
    rs_i = max(1, min(max_w, int(round(rs))))
    if err_mag >= 0.08 and abs(ls_i - rs_i) < 2:
        if err_clamped > 0:
            ls_i = min(max_w, ls_i + 1)
            rs_i = max(1, rs_i - 1)
        elif err_clamped < 0:
            ls_i = max(1, ls_i - 1)
            rs_i = min(max_w, rs_i + 1)

    from sirena_ui.workers.face_follow_controller import RIGHT_WHEEL_EXTRA_RUN_PP

    r_cap = max(1, max_w - RIGHT_WHEEL_EXTRA_RUN_PP)
    rs_cmd = max(1, min(r_cap, rs_i - RIGHT_WHEEL_EXTRA_RUN_PP))
    return ("forward", ls_i, "forward", rs_cmd)


class ArucoFollowController(QObject):
    """Start/stop ArUco approach; ingests ``Detection`` lists from VisionWorker."""

    status_message = pyqtSignal(str)
    arrived = pyqtSignal()

    def __init__(
        self,
        drive: DriveController,
        *,
        obstacle_fn: Optional[Callable[[], ObstacleField]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._drive = drive
        self._obstacle_fn = obstacle_fn
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(_TICK_MS)
        self._search_step_timer = QTimer(self)
        self._search_step_timer.setSingleShot(True)
        self._search_step_timer.timeout.connect(self._on_search_step_done)

        self._active = False
        self._latest: List[Detection] = []
        self._frame_wh: Tuple[int, int] = (640, 480)

        self._searching = False
        self._search_phase = 0  # 0=turn right, 1=pause, 2=turn left, 3=pause
        self._lost_since: Optional[float] = None

    def set_frame_size(self, w: int, h: int) -> None:
        self._frame_wh = (max(1, int(w)), max(1, int(h)))

    def _target_standoff_area_px(self) -> float:
        raw = (os.environ.get("NINA_ARUCO_TARGET_AREA_PX") or "").strip()
        if raw:
            try:
                v = int(raw)
                if v >= 100:
                    return float(v)
            except ValueError:
                pass
        fw, fh = self._frame_wh
        try:
            frac = float(
                os.environ.get(
                    "NINA_ARUCO_TARGET_FRAC",
                    str(_DEFAULT_MARKER_FRAC),
                )
            )
        except ValueError:
            frac = _DEFAULT_MARKER_FRAC
        frac = max(1e-6, min(0.25, frac))
        return max(400.0, frac * float(fw * fh))

    def ingest_detections(self, dets: List[Detection]) -> None:
        self._latest = list(dets)

    def start(self) -> bool:
        st = self._drive.state()
        if st.get("brake"):
            self.status_message.emit("ArUco: engage brake off on Drive first")
            return False
        try:
            self._drive.stop(drain=True)
        except Exception:
            pass
        self._latest = []
        self._searching = False
        self._search_phase = 0
        self._lost_since = None
        self._active = True
        self._search_step_timer.stop()
        self._timer.start()
        self.status_message.emit("ArUco: seeking marker…")
        try:
            self._drive.ensure_hardware()
        except Exception as exc:
            log.debug("ensure_hardware: %s", exc)
        return True

    def stop(self) -> None:
        self._active = False
        self._searching = False
        self._search_phase = 0
        self._lost_since = None
        self._timer.stop()
        self._search_step_timer.stop()
        self._latest = []
        try:
            self._drive.stop(drain=True)
        except Exception as exc:
            log.debug("drive.stop: %s", exc)
        self.status_message.emit("ArUco: off")

    def is_active(self) -> bool:
        return bool(self._active)

    def _pick_marker(self, markers: List[Detection]) -> Optional[Detection]:
        if not markers:
            return None
        return max(markers, key=_bbox_area)

    def _obstacle(self) -> Optional[ObstacleField]:
        if self._obstacle_fn is None:
            return None
        try:
            return self._obstacle_fn()
        except Exception as exc:
            log.debug("obstacle_fn: %s", exc)
            return None

    def _apply_forward_obstacle(
        self,
        ld: str,
        ls_i: int,
        rd: str,
        rs_i: int,
        *,
        fwd_clear_mm: int,
        estop_mm: int,
    ) -> Tuple[str, int, str, int]:
        """Scale or zero forward differential when forward sector is tight."""
        obs = self._obstacle()
        if obs is None:
            return ld, ls_i, rd, rs_i
        if obs.cliff_alarm:
            return ld, 0, rd, 0
        fm = obs.forward_mm
        if fm is None:
            return ld, ls_i, rd, rs_i
        if fm < estop_mm:
            return "back", _SPEED_BACK_PCT, "back", _SPEED_BACK_PCT
        forward_move = ld == "forward" and rd == "forward" and ls_i > 0 and rs_i > 0
        if not forward_move:
            return ld, ls_i, rd, rs_i
        if fm >= fwd_clear_mm:
            return ld, ls_i, rd, rs_i
        span = max(1, fwd_clear_mm - estop_mm)
        t = (fm - estop_mm) / float(span)
        t = max(0.0, min(1.0, t))
        scale = t * t
        return (
            ld,
            max(1, int(round(ls_i * scale))),
            rd,
            max(1, int(round(rs_i * scale))),
        )

    def _tick(self) -> None:
        if not self._active:
            return
        if self._drive.state().get("brake"):
            self._shutdown_brake()
            return

        markers = [d for d in self._latest if d.kind == KIND_ARUCO]
        chosen = self._pick_marker(markers)

        if chosen is not None:
            self._lost_since = None
            if self._searching:
                self._search_step_timer.stop()
                self._searching = False
                self._search_phase = 0
                self.status_message.emit("ArUco: marker reacquired")
            self._handle_track(chosen)
            return

        self._handle_lost()

    def _shutdown_brake(self) -> None:
        try:
            self._drive.stop(drain=True)
        except Exception:
            pass
        self._active = False
        self._searching = False
        self._timer.stop()
        self._search_step_timer.stop()
        self.status_message.emit("ArUco: stopped (brake on)")

    def _drive_stop_safe(self, *, drain: bool = True) -> None:
        try:
            self._drive.stop(drain=drain)
        except Exception:
            pass

    def _handle_lost(self) -> None:
        now = time.monotonic()
        if self._lost_since is None:
            self._lost_since = now
        if now - self._lost_since < _LOST_SEC:
            self._drive_stop_safe()
            return

        if not self._searching:
            self._searching = True
            self._search_phase = 0
            self._search_step_timer.stop()
            self._drive_stop_safe()
            self.status_message.emit("ArUco: lost — searching (±60°)…")
            self._begin_search_phase()
            return

        if self._search_phase in (0, 2):
            sp = max(1, min(100, _SEARCH_SPEED_PCT))
            try:
                if self._search_phase == 0:
                    self._drive.drive_wheels("forward", sp, "back", sp)
                else:
                    self._drive.drive_wheels("back", sp, "forward", sp)
            except Exception as exc:
                log.debug("search turn: %s", exc)
            return

        self._drive_stop_safe()

    def _begin_search_phase(self) -> None:
        if not self._active or not self._searching:
            return
        if self._search_phase in (0, 2):
            self._search_step_timer.start(_SEARCH_STEP_MS)
        else:
            self._search_step_timer.start(400)

    def _on_search_step_done(self) -> None:
        if not self._active or not self._searching:
            return
        self._drive_stop_safe()
        self._search_phase += 1
        if self._search_phase >= 4:
            self._search_phase = 0
        self._begin_search_phase()

    def _handle_track(self, chosen: Detection) -> None:
        x1, y1, x2, y2 = chosen.bbox
        fw, fh = self._frame_wh
        cx = 0.5 * (x1 + x2)
        err_x = (cx - 0.5 * fw) / max(fw * 0.5, 1.0)
        err_x = max(-1.0, min(1.0, err_x))

        area = float(max(1, _bbox_area(chosen)))
        frame_area = float(max(1, fw * fh))
        area_frac = area / frame_area
        standoff = self._target_standoff_area_px()
        ratio = area / standoff
        max_sp = _SPEED_APPROACH_PCT

        fwd_clear = int(os.environ.get("NINA_AUTO_FWD_CLEAR_MM", "1200"))
        estop = int(os.environ.get("NINA_AUTO_ESTOP_MM", "850"))

        _area_blend_far = 0.82
        _area_close_back = _FOLLOW_CLOSE_RATIO
        eff_dead = _ANG_DEAD
        if ratio >= _FOLLOW_AREA_FAR:
            eff_dead = max(_ANG_DEAD, _FOLLOW_ANG_DEAD_CLOSE)

        try:
            # Marker fills enough of the image → stop follow (user: "looks large").
            if area_frac >= _STOP_AREA_FRAC:
                self._finish_arrived()
                return

            if ratio > _area_close_back:
                try:
                    self._drive.drive_wheels(
                        "back", _SPEED_BACK_PCT, "back", _SPEED_BACK_PCT
                    )
                except Exception as exc:
                    log.debug("drive back: %s", exc)
                return

            if ratio < _FOLLOW_AREA_FAR:
                if (
                    ratio >= _area_blend_far
                    and abs(err_x) <= max(_ANG_DEAD, _FOLLOW_ANG_DEAD_CLOSE)
                ):
                    self._finish_arrived()
                    return
                if ratio <= _area_blend_far:
                    cruise_sp = float(_SPEED_APPROACH_PCT)
                else:
                    t = (ratio - _area_blend_far) / max(
                        1e-6, _FOLLOW_AREA_FAR - _area_blend_far
                    )
                    t = max(0.0, min(1.0, t))
                    cruise_sp = float(
                        _SPEED_APPROACH_PCT
                        + (_SPEED_CRUISE_PCT - _SPEED_APPROACH_PCT) * t
                    )
                ld, ls_i, rd, rs_i = _follow_steering_aruco(
                    cruise_sp,
                    err_x,
                    yaw_gain=_YAW_GAIN,
                    max_wheel=max_sp,
                )
                ld, ls_i, rd, rs_i = self._apply_forward_obstacle(
                    ld, ls_i, rd, rs_i,
                    fwd_clear_mm=fwd_clear,
                    estop_mm=estop,
                )
                if ls_i <= 0 or rs_i <= 0:
                    self._drive_stop_safe()
                    return
                try:
                    self._drive.drive_wheels(ld, ls_i, rd, rs_i)
                except Exception as exc:
                    log.debug("drive approach: %s", exc)
                return

            if abs(err_x) <= eff_dead:
                self._finish_arrived()
                return
            ld, ls_i, rd, rs_i = _follow_steering_aruco(
                float(_SPEED_CRUISE_PCT),
                err_x,
                yaw_gain=_YAW_GAIN,
                max_wheel=max_sp,
            )
            ld, ls_i, rd, rs_i = self._apply_forward_obstacle(
                ld, ls_i, rd, rs_i,
                fwd_clear_mm=fwd_clear,
                estop_mm=estop,
            )
            if ls_i <= 0 or rs_i <= 0:
                self._drive_stop_safe()
                return
            try:
                self._drive.drive_wheels(ld, ls_i, rd, rs_i)
            except Exception as exc:
                log.debug("drive hold steer: %s", exc)
        except Exception as exc:
            log.debug("track: %s", exc)

    def _finish_arrived(self) -> None:
        self._drive_stop_safe()
        self._active = False
        self._searching = False
        self._timer.stop()
        self._search_step_timer.stop()
        self._latest = []
        self.status_message.emit("ArUco: arrived")
        self.arrived.emit()
