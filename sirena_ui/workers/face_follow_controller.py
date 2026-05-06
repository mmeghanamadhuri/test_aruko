"""Vision-guided follow: keep a chosen face centred near a **configured standoff**
size (not whatever size the face had when we first locked).

Uses `DriveController.drive_wheels` at the timer rate from
``NINA_FOLLOW_TICK_MS`` (default 50 ms ≈ 20 Hz) so motion stays smooth.
Distance is inferred from bbox area vs ``NINA_FOLLOW_TARGET_BBOX_AREA``
or ``NINA_FOLLOW_TARGET_FACE_FRAC`` × frame area (see below).
``NINA_FOLLOW_CLOSE_RATIO`` (default 1.25) is the single **too-close** edge:
above it the robot reverses; at or below it (and at least ``_area_far``)
a centred face yields hold instead of approach.
Requires face detection; recognition matches a
named enrollee when ``target_name`` is set, otherwise tracks the largest
face box.

``NINA_FOLLOW_NO_BACK_INITIAL_TICKS`` suppresses reverse briefly after
Start so a large first bbox (subject already farther away) does not
immediately command backup. ``NINA_FOLLOW_AREA_FAR`` / ``NINA_FOLLOW_ANG_DEAD_CLOSE``
make hold engage sooner so the robot stops when visually close.

**Trajectory:** horizontal face offset ``err_x`` (normalized to about ±1) steers
the robot every tick: forward speed scales down when ``|err_x|`` is large
(``NINA_FOLLOW_ERR_FWD_SCALE_MIN`` / ``NINA_FOLLOW_ERR_FWD_SCALE_POWER``) so it
slows while correcting; yaw scales up with ``NINA_FOLLOW_YAW_ERR_BOOST`` at large
errors. Right-wheel PWM commands are offset so ``RIGHT_WHEEL_EXTRA_RUN_PP`` in
``DriveController`` does not cancel steering toward a face on the right.
Near standoff, ``NINA_FOLLOW_HOLD_CREEP_PCT`` + ``NINA_FOLLOW_HOLD_YAW_GAIN``
keep smooth arc corrections instead of short in-place nudges.

Several consecutive no-face ticks (after a lock) start a stepped in-place
scan: turn ~``NINA_FOLLOW_SEARCH_STEP_DEG`` (default 30°), pause
``NINA_FOLLOW_SEARCH_LOOK_TICKS`` control ticks to look for a face, repeat
until roughly 360° (``ceil(360/step_deg)`` steps), then stop follow if the
target never reappears. Single-frame YuNet blips do not reset the lost
counter, so the scan runs when the subject leaves the frame.
"""

from __future__ import annotations

import logging
import math
import os
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from sirena_ui.workers.drive_controller import (
    RIGHT_WHEEL_EXTRA_RUN_PP,
    DriveController,
)
from sirena_ui.workers.vision_types import KIND_FACE, Detection

log = logging.getLogger("sirena_ui.face_follow")

# Follow speeds — `drive_wheels` passes these through to PWM (can be < MIN_SPEED_PCT).
# Defaults balanced for responsive approach while staying within safe duty on
# slick floors; tune with NINA_FOLLOW_* env vars (see NINA_APP.md).
_SPEED_APPROACH_PCT = int(os.environ.get("NINA_FOLLOW_APPROACH_PCT", "11"))
_SPEED_CRUISE_PCT = int(os.environ.get("NINA_FOLLOW_CRUISE_PCT", "9"))
_SPEED_BACK_PCT = int(os.environ.get("NINA_FOLLOW_BACK_PCT", "9"))

# Lost-target search: stepped in-place rotation (see _handle_lost).
_SEARCH_SPEED_PCT = int(os.environ.get("NINA_FOLLOW_SEARCH_PCT", "5"))
_SEARCH_STEP_DEG = max(1, int(os.environ.get("NINA_FOLLOW_SEARCH_STEP_DEG", "30")))
_SEARCH_STEP_MS = max(50, int(os.environ.get("NINA_FOLLOW_SEARCH_STEP_MS", "900")))
_SEARCH_LOOK_TICKS = max(0, int(os.environ.get("NINA_FOLLOW_SEARCH_LOOK_TICKS", "4")))
_SEARCH_STEP_COUNT = max(1, int(math.ceil(360.0 / float(_SEARCH_STEP_DEG))))
# Lateral steering while approaching: normalized err_x [-1,1] -> differential PWM.
_YAW_GAIN = float(os.environ.get("NINA_FOLLOW_YAW_GAIN", "5.5"))
# Extra yaw multiplier at large |err_x| (yaw *= 1 + boost * |err_x|).
try:
    _YAW_ERR_BOOST = float(os.environ.get("NINA_FOLLOW_YAW_ERR_BOOST", "0.85"))
except ValueError:
    _YAW_ERR_BOOST = 0.85
_YAW_ERR_BOOST = max(0.0, min(3.0, _YAW_ERR_BOOST))
# Forward speed multiplier at |err_x|==1 vs 0 (slow while turning toward face).
try:
    _ERR_FWD_SCALE_MIN = float(os.environ.get("NINA_FOLLOW_ERR_FWD_SCALE_MIN", "0.22"))
except ValueError:
    _ERR_FWD_SCALE_MIN = 0.22
_ERR_FWD_SCALE_MIN = max(0.08, min(0.95, _ERR_FWD_SCALE_MIN))
try:
    _ERR_FWD_SCALE_POWER = float(
        os.environ.get("NINA_FOLLOW_ERR_FWD_SCALE_POWER", "1.0")
    )
except ValueError:
    _ERR_FWD_SCALE_POWER = 1.0
_ERR_FWD_SCALE_POWER = max(0.5, min(3.0, _ERR_FWD_SCALE_POWER))
# Near standoff: creep forward while centreing (continuous arc, not pulse nudges).
_hc_raw = (os.environ.get("NINA_FOLLOW_HOLD_CREEP_PCT") or "").strip()
if _hc_raw:
    _HOLD_CREEP_PCT = int(_hc_raw)
else:
    _HOLD_CREEP_PCT = int(os.environ.get("NINA_FOLLOW_NUDGE_PCT", "9"))
_HOLD_CREEP_PCT = max(1, min(100, _HOLD_CREEP_PCT))
_HOLD_YAW_GAIN = float(os.environ.get("NINA_FOLLOW_HOLD_YAW_GAIN", "5.5"))
# Require this many consecutive no-face ticks (after a lock) before 360° search.
# Single-frame YuNet blips must not reset this counter (see _face_present_streak).
_LOST_ENTER_SEARCH_TICKS = max(1, int(os.environ.get("NINA_FOLLOW_LOST_TICKS", "4")))
# Face must be seen this many ticks in a row before we trust it (clear lost streak / exit search).
_FACE_CONFIRM_TICKS = max(1, int(os.environ.get("NINA_FOLLOW_CONFIRM_TICKS", "2")))
# Face area ratio (bbox_area / standoff) above which we reverse; same band limit
# for centred hold (no separate "early stop" closer than reverse). Tunable.
try:
    _FOLLOW_CLOSE_RATIO = float(os.environ.get("NINA_FOLLOW_CLOSE_RATIO", "1.25"))
except ValueError:
    _FOLLOW_CLOSE_RATIO = 1.25
_FOLLOW_CLOSE_RATIO = max(1.01, min(2.5, _FOLLOW_CLOSE_RATIO))
# Below this ratio we command forward approach; raised toward standoff = hold sooner.
try:
    _FOLLOW_AREA_FAR = float(os.environ.get("NINA_FOLLOW_AREA_FAR", "0.88"))
except ValueError:
    _FOLLOW_AREA_FAR = 0.88
_FOLLOW_AREA_FAR = max(0.55, min(0.98, _FOLLOW_AREA_FAR))
# After follow starts, suppress reverse this many control ticks (~50 ms each) so a
# large first-frame box does not immediately back up when the subject moved away.
try:
    _FOLLOW_NO_BACK_INITIAL_TICKS = max(
        0, int(os.environ.get("NINA_FOLLOW_NO_BACK_INITIAL_TICKS", "24"))
    )
except ValueError:
    _FOLLOW_NO_BACK_INITIAL_TICKS = 24
try:
    _FOLLOW_ANG_DEAD_CLOSE = float(
        os.environ.get("NINA_FOLLOW_ANG_DEAD_CLOSE", "0.14")
    )
except ValueError:
    _FOLLOW_ANG_DEAD_CLOSE = 0.14
# Control-loop period (ms). Lower = snappier first lock after detections appear.
_FOLLOW_TICK_MS = max(16, int(os.environ.get("NINA_FOLLOW_TICK_MS", "50")))

# Desired standoff: face bbox area (px²) the controller tries to hold, independent
# of size at first lock. Set NINA_FOLLOW_TARGET_BBOX_AREA for a fixed px², or use
# NINA_FOLLOW_TARGET_FACE_FRAC (fraction of frame width×height, default 0.035).
_MIN_TARGET_AREA_PX = 400
_DEFAULT_FACE_FRAC = 0.035


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


def _follow_steering_command(
    base_forward: float,
    err_x: float,
    *,
    yaw_gain: float,
    max_wheel: int,
) -> Tuple[str, int, str, int]:
    """Map lateral face error to differential drive for one follow tick.

    *base_forward* is the nominal forward command before error shaping. As
    ``|err_x|`` increases, forward speed scales down (see module
    ``_ERR_FWD_SCALE_*``) so the robot slows while converging on bearing; at
    ``err_x ≈ 0`` it returns to full *base_forward*. If a forward arc would
    require reversing a wheel, we command a short in-place turn instead (same
    handedness as before: ``err_x > 0`` → face is right → turn right).

    ``DriveController`` adds ``RIGHT_WHEEL_EXTRA_RUN_PP`` to the **right** duty
    on every forward-forward command (straight-line hardware trim). We subtract
    that here on the right **command** so the delivered PWM matches the
    differential we compute (otherwise corrections when the face is to the
    right are largely cancelled).
    """
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
    # Integer PWM often wipes out small yaws; nudge a minimum diff when off-centre.
    if err_mag >= 0.08 and abs(ls_i - rs_i) < 2:
        if err_clamped > 0:
            ls_i = min(max_w, ls_i + 1)
            rs_i = max(1, rs_i - 1)
        elif err_clamped < 0:
            ls_i = max(1, ls_i - 1)
            rs_i = min(max_w, rs_i + 1)

    r_cap = max(1, max_w - RIGHT_WHEEL_EXTRA_RUN_PP)
    rs_cmd = max(1, min(r_cap, rs_i - RIGHT_WHEEL_EXTRA_RUN_PP))
    return ("forward", ls_i, "forward", rs_cmd)


class FaceFollowController(QObject):
    """Start/stop person follow; ingests ``Detection`` lists from VisionWorker."""

    status_message = pyqtSignal(str)
    #: Emitted once per “lock session” when a target is acquired (greeting).
    face_latched = pyqtSignal(str)

    def __init__(self, drive: DriveController, parent=None) -> None:
        super().__init__(parent)
        self._drive = drive
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(_FOLLOW_TICK_MS)
        self._search_step_timer = QTimer(self)
        self._search_step_timer.setSingleShot(True)
        self._search_step_timer.timeout.connect(self._on_search_step_done)

        self._active = False
        self._target_name: Optional[str] = None
        self._latched: bool = False
        self._latest: List[Detection] = []
        self._frame_wh: Tuple[int, int] = (640, 480)

        self._searching = False
        self._search_in_turn = False
        self._search_look_remaining = 0
        self._search_steps_done = 0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0

        # ratio = face bbox area / standoff. _area_far.._FOLLOW_CLOSE_RATIO: hold / creep
        # steer when off-axis; ratio > _FOLLOW_CLOSE_RATIO: reverse (too close).
        self._area_far = _FOLLOW_AREA_FAR
        self._area_blend_far = 0.82
        self._area_close_back = _FOLLOW_CLOSE_RATIO
        self._ang_dead = 0.09
        self._no_back_ticks_remaining = 0

    def set_frame_size(self, w: int, h: int) -> None:
        self._frame_wh = (max(1, int(w)), max(1, int(h)))

    def _target_standoff_area_px(self) -> float:
        """Ideal face bbox area (px²) for approach / back thresholds."""
        raw = (os.environ.get("NINA_FOLLOW_TARGET_BBOX_AREA") or "").strip()
        if raw:
            try:
                v = int(raw)
                if v >= _MIN_TARGET_AREA_PX:
                    return float(v)
            except ValueError:
                pass
        fw, fh = self._frame_wh
        try:
            frac = float(os.environ.get("NINA_FOLLOW_TARGET_FACE_FRAC", str(_DEFAULT_FACE_FRAC)))
        except ValueError:
            frac = _DEFAULT_FACE_FRAC
        frac = max(1e-6, min(0.5, frac))
        return max(float(_MIN_TARGET_AREA_PX), frac * float(fw * fh))

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
        try:
            self._drive.stop(drain=True)
        except Exception:
            pass
        self._target_name = (target_name or "").strip() or None
        self._latched = False
        self._latest = []
        self._searching = False
        self._search_in_turn = False
        self._search_look_remaining = 0
        self._search_steps_done = 0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0
        self._no_back_ticks_remaining = _FOLLOW_NO_BACK_INITIAL_TICKS
        self._active = True
        self._search_step_timer.stop()
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
        self._search_in_turn = False
        self._search_look_remaining = 0
        self._search_steps_done = 0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0
        self._no_back_ticks_remaining = 0
        self._timer.stop()
        self._search_step_timer.stop()
        self._target_name = None
        self._latched = False
        self._latest = []
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
            self._search_in_turn = False
            self._search_look_remaining = 0
            self._search_steps_done = 0
            self._had_lock = False
            self._lost_streak = 0
            self._face_present_streak = 0
            self._no_back_ticks_remaining = 0
            self._timer.stop()
            self._search_step_timer.stop()
            self.status_message.emit("Follow: stopped (brake on)")
            return

        if self._no_back_ticks_remaining > 0:
            self._no_back_ticks_remaining -= 1

        faces = [d for d in self._latest if d.kind == KIND_FACE]
        chosen = self._pick_face(faces)
        if chosen is not None:
            self._handle_track(chosen)
            return

        self._handle_lost()

    def _drive_stop_safe(self, *, drain: bool = True) -> None:
        try:
            self._drive.stop(drain=drain)
        except Exception:
            pass

    def _handle_lost(self) -> None:
        self._face_present_streak = 0

        # Before we have ever locked a face, hold still — do not 360° scan.
        if not self._had_lock:
            self._drive_stop_safe()
            self._searching = False
            self._search_in_turn = False
            self._search_look_remaining = 0
            self._search_steps_done = 0
            self._search_step_timer.stop()
            self._lost_streak = 0
            return

        self._lost_streak += 1
        if self._lost_streak < _LOST_ENTER_SEARCH_TICKS:
            # Brief dropout / motion blur — wait before declaring lost.
            self._drive_stop_safe()
            return

        if not self._searching:
            self._searching = True
            self._search_in_turn = False
            self._search_look_remaining = 0
            self._search_steps_done = 0
            self._search_step_timer.stop()
            self._latched = False
            self._drive_stop_safe()
            self.status_message.emit("Follow: lost — stepped 360° scan…")
            return

        if self._search_in_turn:
            sp = max(1, min(100, _SEARCH_SPEED_PCT))
            try:
                # Same handedness as in-place follow correction (face right → turn right).
                self._drive.drive_wheels("forward", sp, "back", sp)
            except Exception as exc:
                log.debug("search step turn: %s", exc)
            return

        if self._search_look_remaining > 0:
            self._search_look_remaining -= 1
            self._drive_stop_safe()
            return

        if self._search_steps_done >= _SEARCH_STEP_COUNT:
            self._finish_search_no_target()
            return

        self._start_search_turn()

    def _handle_track(self, chosen: Detection) -> None:
        self._face_present_streak += 1
        # Intermittent detections while the subject is gone used to reset
        # _lost_streak every frame and prevented 360° search from ever arming.
        if self._face_present_streak >= _FACE_CONFIRM_TICKS:
            self._lost_streak = 0

        self._had_lock = True

        if self._searching:
            if self._face_present_streak < _FACE_CONFIRM_TICKS:
                if self._search_in_turn:
                    sp = max(1, min(100, _SEARCH_SPEED_PCT))
                    try:
                        self._drive.drive_wheels("forward", sp, "back", sp)
                    except Exception as exc:
                        log.debug("search step (blip guard): %s", exc)
                else:
                    self._drive_stop_safe()
                return
            self._search_step_timer.stop()
            self._searching = False
            self._search_in_turn = False
            self._search_look_remaining = 0
            self._search_steps_done = 0
            self._latched = False
            self.status_message.emit("Follow: target reacquired")

        x1, y1, x2, y2 = chosen.bbox
        fw, _fh = self._frame_wh
        cx = 0.5 * (x1 + x2)
        err_x = (cx - 0.5 * fw) / max(fw * 0.5, 1.0)
        err_x = max(-1.0, min(1.0, err_x))

        area = float(max(1, _bbox_area(chosen)))
        standoff = self._target_standoff_area_px()
        latched_greet: Optional[str] = None
        if not self._latched:
            self._latched = True
            label = chosen.identity or chosen.label or "face"
            tgt = int(standoff)
            self.status_message.emit(
                f"Follow: locked {label} ({int(area)} px² · standoff target {tgt} px²)"
            )
            # Defer face_latched until after drive commands: the greeting slot
            # can block the GUI thread (mute preroll + mpg123), which prevents
            # QTimer follow ticks and delays this tick's drive_wheels.
            latched_greet = _greeting_name(chosen)

        ratio = area / standoff

        max_sp = _SPEED_APPROACH_PCT

        try:
            eff_dead = self._ang_dead
            if ratio >= self._area_far:
                eff_dead = max(self._ang_dead, _FOLLOW_ANG_DEAD_CLOSE)

            if ratio > self._area_close_back:
                if self._no_back_ticks_remaining > 0:
                    try:
                        self._drive.stop(drain=True)
                    except Exception:
                        pass
                    return
                try:
                    self._drive.drive_wheels(
                        "back", _SPEED_BACK_PCT, "back", _SPEED_BACK_PCT
                    )
                except Exception as exc:
                    log.debug("drive_wheels back: %s", exc)
                return

            if ratio < self._area_far:
                if (
                    ratio >= self._area_blend_far
                    and abs(err_x)
                    <= max(self._ang_dead, _FOLLOW_ANG_DEAD_CLOSE)
                ):
                    try:
                        self._drive.stop(drain=True)
                    except Exception:
                        pass
                    return
                if ratio <= self._area_blend_far:
                    cruise_sp = float(_SPEED_APPROACH_PCT)
                else:
                    t = (ratio - self._area_blend_far) / max(
                        1e-6, self._area_far - self._area_blend_far
                    )
                    t = max(0.0, min(1.0, t))
                    cruise_sp = float(
                        _SPEED_APPROACH_PCT
                        + (_SPEED_CRUISE_PCT - _SPEED_APPROACH_PCT) * t
                    )
                ld, ls_i, rd, rs_i = _follow_steering_command(
                    cruise_sp,
                    err_x,
                    yaw_gain=_YAW_GAIN,
                    max_wheel=max_sp,
                )
                try:
                    self._drive.drive_wheels(ld, ls_i, rd, rs_i)
                except Exception as exc:
                    log.debug("drive_wheels follow: %s", exc)
                return

            if abs(err_x) <= eff_dead:
                try:
                    self._drive.stop(drain=True)
                except Exception:
                    pass
                return
            # Near standoff: keep steering every tick (arc / slow pivot), not pulse nudges.
            ld, ls_i, rd, rs_i = _follow_steering_command(
                float(_HOLD_CREEP_PCT),
                err_x,
                yaw_gain=_HOLD_YAW_GAIN,
                max_wheel=max_sp,
            )
            try:
                self._drive.drive_wheels(ld, ls_i, rd, rs_i)
            except Exception as exc:
                log.debug("drive_wheels hold steer: %s", exc)
        finally:
            if latched_greet is not None:
                self.face_latched.emit(latched_greet)

    def _start_search_turn(self) -> None:
        if not self._active or not self._searching:
            return
        self._search_in_turn = True
        sp = max(1, min(100, _SEARCH_SPEED_PCT))
        try:
            self._drive.drive_wheels("forward", sp, "back", sp)
        except Exception as exc:
            log.debug("search turn start: %s", exc)
        self._search_step_timer.start(_SEARCH_STEP_MS)

    def _on_search_step_done(self) -> None:
        if not self._active or not self._searching:
            return
        self._search_in_turn = False
        self._drive_stop_safe()
        self._search_steps_done += 1
        self._search_look_remaining = _SEARCH_LOOK_TICKS

    def _finish_search_no_target(self) -> None:
        self._active = False
        self._searching = False
        self._search_in_turn = False
        self._search_look_remaining = 0
        self._search_steps_done = 0
        self._had_lock = False
        self._lost_streak = 0
        self._face_present_streak = 0
        self._no_back_ticks_remaining = 0
        self._timer.stop()
        self._search_step_timer.stop()
        self._latest = []
        self._target_name = None
        self._latched = False
        self._drive_stop_safe()
        self.status_message.emit(
            "Follow: lost after full scan — tap Start follow to retry"
        )
