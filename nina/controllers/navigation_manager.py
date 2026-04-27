"""
NavigationManager for Nina (5ft wheeled bot).

Drives 2x JYQD_V7.3E2 BLDC drivers (one per wheel) directly from the Jetson
Nano. The pinout below matches Nina's hand-verified baseline harness; both
JYQD VR pins land on the same Jetson hardware-PWM channel (BCM 13 / pin
33), so this build runs in *shared-PWM* mode: one duty cycle controls
both wheels at the same speed; differential motion (in-place pivot) comes
from flipping the per-side Z/F pin, not from running the wheels at
different speeds. This is intentional, and well-suited to the manual /
"forward / backward / pivot-left / pivot-right" command vocabulary used
on the bot today.

  Left wheel : L_EN=BCM18 L_DIR=BCM25 L_SIGNAL=BCM24 L_PWM=BCM13
  Right wheel: R_EN=BCM10 R_DIR=BCM23 R_SIGNAL=BCM27 R_PWM=BCM13 (shared)
  Status LED : RED=BCM21  GREEN=BCM20 BLUE=BCM16
  E-stop     : ESTOP1=BCM17 ESTOP2=BCM5

Physical pins on the Jetson 40-pin header (cross-reference for the harness):

           Function        BCM   Physical pin
  Left   EL              18         12
         Z/F             25         22
         Signal          24         18
         VR (PWM)        13         33    (shared with right)
  Right  EL              10         19
         Z/F             23         16
         Signal          27         13
         VR (PWM)        13         33    (shared with left)
  Power  5 V supply       -          2 or 4
         GND              -          30, 34, 39 (use one per JYQD; the
                                                 third anchors the Jetson)

Why ZF moved off pins 15 / 32: BCM 22 (pin 15) defaults to LCD_TE and
BCM 12 (pin 32) defaults to LCD_BL_PWM in the stock Jetson Nano device
tree. `Jetson.GPIO.setup(pin, OUT)` silently succeeds on these pins but
the writes don't leave the SoC - the pad stays at ~0.5 V regardless of
what we drive. BCM 25 (pin 22) and BCM 23 (pin 16) are unconditionally
GPIO on every L4T release, so they are the safe choice for ZF. Probe
with `python3 -m nina.app.pin_probe --pin <bcm>` if you ever need to
re-vet a different pin.

JYQD_V7.3E2 "set" header layout (top -> bottom on the silkscreen):
  5V, EL, Signal, Z/F, VR, GND.

This build runs the JYQD in *VR-with-Signal-gate* mode (the same mode the
Pi build uses):

- EL     -> per-side digital enable. LOW = brake / freewheel; HIGH = run.
- Z/F    -> per-side direction. HIGH/LOW selects rotation.
- VR     -> shared analog speed input. We feed it Jetson hardware PWM on
            BCM 13 and the JYQD's input filter averages it into a
            quasi-DC speed reference (0% duty = stop, 100% duty = max speed).
- Signal -> per-side digital run-gate. **Must be HIGH for the chip to
            commutate** even when EL=HIGH and VR has a voltage on it. With
            this pin floating the JYQD ignores Z/F changes and runs in a
            single "limp" direction.

**DO NOT use a level shifter between the Jetson and the JYQD "set"
header.** The JYQD V7.3E2 inputs (EL, Signal, Z/F) are opto-isolated and
trigger reliably from 3.3V GPIOs - the same RPi wiring this driver was
copied from runs 3.3V direct, no shifter. The cheap red 4-channel
passive resistor-divider boards silently mangle these signals into
intermediate voltages (~1-2V at the JYQD screw, with the Jetson side
toggling cleanly at 0/3.3V), and the failure mode looks like "wheels
won't reverse" or "one wheel never spins" - the chip never sees a clean
edge. If you suspect a wiring issue, probe at the **JYQD screw** with
`pin_probe --pin <bcm>` running and compare to the Jetson header pin: if
the JYQD-side reading isn't a clean 0V/3.3V swing matching the header,
there is a shifter / broken wire / bad connection in between - fix the
harness, do not patch it in software.

Direction polarity (matches the Pi):
  Left  forward => L_DIR HIGH
  Right forward => R_DIR LOW   (right side is mirrored, so opposite level)

Implication of the shared PWM channel:
  set_wheels(left_speed=A, right_speed=B) with A != B will NOT produce
  per-wheel speeds; the second set_duty() write wins and both wheels run
  at B. Autonomy controllers that want true differential speed need to
  re-pin to dedicated L_PWM / R_PWM hardware-PWM channels (BCM 12 and
  BCM 13) - search this module for "shared-PWM" before changing.

Hardware PWM on Jetson Nano is only available on:
- BCM 12 (physical pin 32) -> PWM0
- BCM 13 (physical pin 33) -> PWM2

Enable PWM once via: sudo /opt/nvidia/jetson-io/jetson-io.py
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from nina.controllers.gpio_backend import GpioBackend, create_backend


log = logging.getLogger("nina.navigation")


@dataclass(frozen=True)
class NavigationPins:
    """All BCM pin numbers for navigation hardware.

    l_dir / r_dir map to the JYQD's ZF pin (direction). The legacy l_zf /
    r_zf fields are kept so older configs / env overrides keep working,
    but they're aliases for the same physical pin.

    l_signal / r_signal map to the JYQD's "Signal" run-gate pin (the chip
    needs them HIGH to commutate, see the module docstring).
    """
    l_en: int
    l_dir: int
    l_signal: int
    pwm_l: int
    r_en: int
    r_dir: int
    r_signal: int
    pwm_r: int
    led_red: int
    led_green: int
    led_blue: int
    estop_1: int
    estop_2: int

    @property
    def l_zf(self) -> int:
        return self.l_dir

    @property
    def r_zf(self) -> int:
        return self.r_dir


@dataclass(frozen=True)
class NavigationConfig:
    pins: NavigationPins
    backend_name: str = "jetson"
    pwm_frequency_hz: int = 2000
    default_speed_percent: int = 15
    turn_duration_sec: float = 2.3
    settle_delay_sec: float = 0.1
    # Deadband compensation: linearly remaps the user-facing 0..100 speed
    # range to [min_duty_percent, max_duty_percent] on the actual PWM output.
    # On a 3.3V Jetson driving a 5V JYQD VR input, the motor often needs
    # ~70-80% real duty before it starts spinning; setting min_duty_percent
    # to ~70 lets `--speed 5` actually move the wheel.
    min_duty_percent: float = 0.0
    max_duty_percent: float = 100.0
    # Kick-start: BLDC motors need a brief high-duty pulse to break static
    # friction and let the controller sense rotor position. Drives the PWM
    # at kick_start_duty_percent for kick_start_duration_sec, then drops
    # to the requested speed. Set duration to 0 to disable.
    kick_start_duty_percent: float = 100.0
    kick_start_duration_sec: float = 0.25
    # Direction polarity per side. JYQD ZF=HIGH usually means one direction
    # and ZF=LOW the other, but which is "forward" depends on motor wiring.
    # Flip these if a side spins the opposite of expected.
    invert_left_dir: bool = False
    invert_right_dir: bool = False
    # Time the JYQD needs in EL=LOW after stop() before the next EL rising
    # edge will reliably re-sample the ZF/DIR pin. The V7.3E2 in
    # VR-with-Signal-gate mode behaves this way; bumping the rotor takes
    # ~150-200 ms even after PWM is zero. Override via NINA_NAV_DIR_SETTLE
    # if your specific motors / firmware need more time.
    dir_change_settle_sec: float = 0.20


# Default Nina pinout (BCM numbering). These match the hand-verified baseline
# harness on the bot; both VR (PWM) wires land on the same Jetson hardware-PWM
# channel (BCM 13 / pin 33). See module docstring for the shared-PWM
# implications. PWM pins MUST be BCM 12 or BCM 13 to use hardware PWM on
# Jetson Nano.
DEFAULT_PINS = NavigationPins(
    l_en=int(os.environ.get("NINA_NAV_L_EN", "18")),
    l_dir=int(os.environ.get("NINA_NAV_L_DIR", os.environ.get("NINA_NAV_L_ZF", "25"))),
    l_signal=int(os.environ.get("NINA_NAV_L_SIGNAL", "24")),
    pwm_l=int(os.environ.get("NINA_NAV_L_PWM", "13")),
    r_en=int(os.environ.get("NINA_NAV_R_EN", "10")),
    r_dir=int(os.environ.get("NINA_NAV_R_DIR", os.environ.get("NINA_NAV_R_ZF", "23"))),
    r_signal=int(os.environ.get("NINA_NAV_R_SIGNAL", "27")),
    pwm_r=int(os.environ.get("NINA_NAV_R_PWM", "13")),
    led_red=21,
    led_green=20,
    led_blue=16,
    estop_1=17,
    estop_2=5,
)


class NavigationManager:
    """
    BLDC navigation controller for two-wheel differential drive using JYQD_V7.3E2 drivers.
    """

    SIDE_LEFT = "left"
    SIDE_RIGHT = "right"
    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"

    def __init__(self, config: Optional[NavigationConfig] = None,
                 backend: Optional[GpioBackend] = None) -> None:
        self.config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._backend: GpioBackend = backend or create_backend(self.config.backend_name)
        self._is_initialized = False
        # Tracks the last direction we wrote to each side so set_wheels()
        # (autonomy 5-20 Hz hot path) can force a clean EL low->high
        # transition only when direction actually changes, instead of on
        # every tick.
        self._last_dir: Dict[str, Optional[str]] = {
            self.SIDE_LEFT: None,
            self.SIDE_RIGHT: None,
        }

    def initialize(self) -> None:
        if self._is_initialized:
            return

        self._backend.setup()
        pins = self.config.pins

        for pin in (pins.estop_1, pins.estop_2):
            try:
                self._backend.configure_output(pin)
            except Exception:
                log.debug("E-stop pin %s left as input", pin)

        for pin in (pins.led_red, pins.led_green, pins.led_blue,
                    pins.l_en, pins.l_dir, pins.l_signal,
                    pins.r_en, pins.r_dir, pins.r_signal):
            self._backend.configure_output(pin)

        self._backend.configure_pwm(pins.pwm_l, self.config.pwm_frequency_hz)
        self._backend.configure_pwm(pins.pwm_r, self.config.pwm_frequency_hz)

        # Park everything safely braked: direction default (forward) latched,
        # both EL and Signal LOW so the JYQD treats the chip as
        # disabled+gated-off until something explicitly calls release_brake()
        # or _control_speed(..., enable=True).
        self._backend.write(pins.l_dir, 0)
        self._backend.write(pins.r_dir, 0)
        self._backend.write(pins.l_en, 0)
        self._backend.write(pins.r_en, 0)
        self._backend.write(pins.l_signal, 0)
        self._backend.write(pins.r_signal, 0)

        self._is_initialized = True
        pwm_mode = "SHARED" if pins.pwm_l == pins.pwm_r else "INDEPENDENT"
        log.info(
            "NavigationManager initialized backend=%s pwm_mode=%s pins: "
            "L_EN=BCM%d L_ZF/DIR=BCM%d L_SIGNAL=BCM%d L_PWM=BCM%d "
            "R_EN=BCM%d R_ZF/DIR=BCM%d R_SIGNAL=BCM%d R_PWM=BCM%d "
            "invert_left=%s invert_right=%s",
            self._backend.name, pwm_mode,
            pins.l_en, pins.l_dir, pins.l_signal, pins.pwm_l,
            pins.r_en, pins.r_dir, pins.r_signal, pins.pwm_r,
            self.config.invert_left_dir, self.config.invert_right_dir,
        )
        if pwm_mode == "SHARED":
            log.info(
                "NavigationManager: shared-PWM mode (L_PWM == R_PWM == BCM%d). "
                "Both wheels run at the same duty; differential motion comes "
                "from per-side ZF/Signal toggling, not per-wheel speed.",
                pins.pwm_l,
            )

    def shutdown(self) -> None:
        if not self._is_initialized:
            return
        try:
            self.stop()
            self._set_enable(self.SIDE_LEFT, False)
            self._set_enable(self.SIDE_RIGHT, False)
        finally:
            try:
                self._backend.shutdown()
            except Exception:
                log.warning("backend shutdown raised; ignoring")
            self._is_initialized = False
            log.info("NavigationManager shutdown")

    def forward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_FORWARD, speed)
        log.info(
            "forward speed=%s%% (L_DIR=BCM%d R_DIR=BCM%d)",
            speed, self.config.pins.l_dir, self.config.pins.r_dir,
        )

    def backward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._command_both(self.DIR_BACKWARD, speed)
        log.info(
            "backward speed=%s%% (L_DIR=BCM%d R_DIR=BCM%d)",
            speed, self.config.pins.l_dir, self.config.pins.r_dir,
        )

    def turn_left(self, speed_percent: Optional[int] = None,
                  duration: Optional[float] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(left_dir=self.DIR_BACKWARD, right_dir=self.DIR_FORWARD,
                         speed=speed, duration=duration)
        log.info(
            "turn_left speed=%s%% (L=backward R=forward)", speed,
        )

    def turn_right(self, speed_percent: Optional[int] = None,
                   duration: Optional[float] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(left_dir=self.DIR_FORWARD, right_dir=self.DIR_BACKWARD,
                         speed=speed, duration=duration)
        log.info(
            "turn_right speed=%s%% (L=forward R=backward)", speed,
        )

    def drive_continuous(self, left_dir: str, right_dir: str,
                         speed_percent: Optional[int] = None) -> None:
        """Start (or update) per-wheel motion that does NOT auto-stop.

        Includes the same settle + kick-start as forward()/backward() so
        a BLDC at rest catches reliably, but unlike turn_left/turn_right
        this method returns as soon as steady-state PWM is set and
        leaves the wheels running until stop() is called. Used by the
        GUI's held D-pad buttons so left/right turns last as long as
        the operator holds the key down.
        """
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        speed = self._resolve_speed(speed_percent)
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._set_direction(self.SIDE_LEFT, left_dir)
        self._set_direction(self.SIDE_RIGHT, right_dir)
        time.sleep(0.02)  # let JYQD latch direction before EL/PWM ramps
        self._kick_start(left_dir=left_dir, right_dir=right_dir,
                         target_speed=speed)
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
        log.info(
            "drive_continuous L=%s R=%s speed=%s%% (L_DIR=BCM%d R_DIR=BCM%d)",
            left_dir, right_dir, speed,
            self.config.pins.l_dir, self.config.pins.r_dir,
        )

    def stop(self) -> None:
        """Hard-stop both wheels: EL=Signal=LOW, PWM=0.

        We deliberately drive EL *low* (not just PWM=0) because the
        JYQD V7.3E2 in VR-with-Signal-gate mode latches the ZF/DIR pin
        on the EL rising edge - any caller that wants to change direction
        after stop() needs a clean EL low->high transition so the chip
        re-samples DIR. A 'soft stop' that only zeroes PWM would silently
        keep the previous direction and was the root cause of "all keys
        spin the wheels the same way" in early-2026.

        Also tracks the per-side direction so set_wheels() (the autonomy
        hot path) knows the chip is fully reset and the next call needs
        a fresh EL rising edge.
        """
        self._control_speed(self.SIDE_LEFT, False, 0, self.DIR_FORWARD)
        self._control_speed(self.SIDE_RIGHT, False, 0, self.DIR_FORWARD)
        self._last_dir[self.SIDE_LEFT] = None
        self._last_dir[self.SIDE_RIGHT] = None
        time.sleep(max(self.config.settle_delay_sec,
                       self.config.dir_change_settle_sec))
        log.info("stop (EL=Signal=LOW, dir latches cleared)")

    def set_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Apply per-wheel direction + speed without any settle / kick-start
        / timed-turn behaviour. Returns immediately so a closed-loop
        autonomy controller can call this at 5-20 Hz without each call
        blocking on internal sleeps.

        Speeds are 0..100 (deadband-corrected just like `forward()` /
        `backward()`). Pass speed=0 to coast that wheel.

        Note: in shared-PWM mode (L_PWM == R_PWM, see module docstring),
        passing left_speed != right_speed will silently end up with both
        wheels at right_speed because the right write overwrites the
        shared duty channel. We emit a one-shot warning the first time
        this happens so the caller knows they need a per-wheel-PWM
        rewire to get true differential speed.

        On a per-side direction change we also force a brief EL/Signal
        low->high transition so the JYQD V7.3E2 re-samples the ZF/DIR
        pin (the chip latches direction at the EL rising edge in
        VR-with-Signal-gate mode). Same direction across consecutive
        calls hits the fast path and just nudges PWM duty.
        """
        pins = self.config.pins
        if (
            pins.pwm_l == pins.pwm_r
            and left_speed != right_speed
            and not getattr(self, "_warned_shared_pwm_asymmetric", False)
        ):
            log.warning(
                "set_wheels(left_speed=%s, right_speed=%s) but L_PWM == R_PWM "
                "== BCM%d; both wheels will run at right_speed. Re-pin to "
                "BCM 12 + BCM 13 for true differential speed.",
                left_speed, right_speed, pins.pwm_l,
            )
            self._warned_shared_pwm_asymmetric = True
        self._apply_wheel(self.SIDE_LEFT, left_dir, left_speed)
        self._apply_wheel(self.SIDE_RIGHT, right_dir, right_speed)

    def _apply_wheel(self, side: str, direction: str, speed: int) -> None:
        """Set one wheel's direction + speed. If the direction differs
        from the last command for this side, drop EL=Signal=LOW first,
        let the JYQD settle, then bring it back high so the chip
        re-samples the ZF/DIR pin. Otherwise just write the new duty.
        """
        prev = self._last_dir.get(side)
        if prev is not None and prev != direction:
            self._control_speed(side, False, 0, prev)
            time.sleep(self.config.dir_change_settle_sec)
        self._control_speed(side, True, speed, direction)

    def emergency_stop(self) -> None:
        log.warning("EMERGENCY STOP requested")
        try:
            self.stop()
            self.engage_brake()
            self._set_enable(self.SIDE_LEFT, False)
            self._set_enable(self.SIDE_RIGHT, False)
            self._set_status_led(red=True, green=True, blue=True)
        except Exception as exc:
            log.exception("emergency_stop failed: %s", exc)

    def engage_brake(self) -> None:
        """Coast-stop both wheels by disabling EL on each driver. JYQD_V7.3E2
        has no software brake unless its BRK pin is wired separately."""
        self._control_speed(self.SIDE_LEFT, False, 0, self.DIR_FORWARD)
        self._control_speed(self.SIDE_RIGHT, False, 0, self.DIR_FORWARD)
        log.info("brake (EL disable) engaged - motors will coast to stop")

    def release_brake(self) -> None:
        """Logical "brake off" - kept as a no-op stub so the GUI's
        Release-Brake button still has something to call.

        On the JYQD V7.3E2 in VR-with-Signal-gate mode, direction is
        latched on the EL rising edge. If we pre-emptively raised EL
        here (the way an earlier revision did) the next forward/backward
        command would change ZF *after* the chip had already locked the
        previous direction, and the motors would refuse to reverse.
        Instead we leave EL/Signal LOW; the next forward()/backward()/
        turn_*() call will raise EL atomically with the correct ZF
        level set, which is what the chip actually needs.

        We do clear the per-side direction latch so the very next
        motion command always looks like a fresh first-start to
        _apply_wheel().
        """
        self._last_dir[self.SIDE_LEFT] = None
        self._last_dir[self.SIDE_RIGHT] = None
        log.info("brake released (EL stays LOW until next motion command)")

    def set_status(self, mode: str) -> None:
        if not self._is_initialized:
            return
        self._set_status_led(red=False, green=False, blue=False)
        if mode == "CONNECTED":
            self._set_status_led(green=True)
        elif mode == "ERROR":
            self._set_status_led(red=True)
        elif mode == "WAITING":
            self._set_status_led(blue=True)

    def _command_both(self, direction: str, speed: int) -> None:
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._set_direction(self.SIDE_LEFT, direction)
        self._set_direction(self.SIDE_RIGHT, direction)
        time.sleep(0.02)  # let JYQD latch direction before EL/PWM ramps
        self._kick_start(left_dir=direction, right_dir=direction, target_speed=speed)
        self._control_speed(self.SIDE_LEFT, True, speed, direction)
        self._control_speed(self.SIDE_RIGHT, True, speed, direction)

    def _timed_turn(self, left_dir: str, right_dir: str,
                    speed: int, duration: Optional[float]) -> None:
        self.stop()
        time.sleep(self.config.settle_delay_sec)
        self._set_direction(self.SIDE_LEFT, left_dir)
        self._set_direction(self.SIDE_RIGHT, right_dir)
        time.sleep(0.02)
        self._kick_start(left_dir=left_dir, right_dir=right_dir, target_speed=speed)
        self._control_speed(self.SIDE_LEFT, True, speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, speed, right_dir)
        time.sleep(duration if duration is not None else self.config.turn_duration_sec)
        self.stop()

    def _kick_start(self, left_dir: str, right_dir: str, target_speed: int) -> None:
        """Brief high-duty pulse to overcome static friction and rotor sensing."""
        if target_speed <= 0:
            return
        kick_dur = float(self.config.kick_start_duration_sec)
        if kick_dur <= 0:
            return
        kick_speed = max(target_speed, int(self.config.kick_start_duty_percent))
        kick_speed = max(0, min(100, kick_speed))
        self._control_speed(self.SIDE_LEFT, True, kick_speed, left_dir)
        self._control_speed(self.SIDE_RIGHT, True, kick_speed, right_dir)
        time.sleep(kick_dur)
        log.info("kick-start %d%% for %.2fs", kick_speed, kick_dur)

    def _control_speed(self, side: str, enable: bool,
                       speed_percent: int, direction: str) -> None:
        self._require_initialized()
        if side not in (self.SIDE_LEFT, self.SIDE_RIGHT):
            raise ValueError(f"Invalid side '{side}'")
        if direction not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid direction '{direction}'")

        speed_percent = max(0, min(100, int(speed_percent)))
        duty_percent = self._apply_deadband(speed_percent)
        pins = self.config.pins

        # The JYQD's Signal run-gate has to be HIGH for the chip to commutate;
        # we mirror EL so any caller that toggles "enable" gets both pins
        # updated atomically. Driving Signal LOW alongside EL=LOW is what
        # actually stops the motor (EL alone leaves the chip half-running on
        # this V7.3E2 rev when fed via VR mode).
        self._set_direction(side, direction)
        gate_level = 1 if enable else 0
        if side == self.SIDE_LEFT:
            self._backend.write(pins.l_en, gate_level)
            self._backend.write(pins.l_signal, gate_level)
            self._backend.set_duty(pins.pwm_l, duty_percent)
        else:
            self._backend.write(pins.r_en, gate_level)
            self._backend.write(pins.r_signal, gate_level)
            self._backend.set_duty(pins.pwm_r, duty_percent)
        # Track the last direction we *committed* (i.e. EL went HIGH),
        # so set_wheels()/_apply_wheel() can detect transitions. EL=LOW
        # writes (enable=False) clear the latch.
        self._last_dir[side] = direction if enable else None

    def _set_direction(self, side: str, direction: str) -> None:
        """Write the direction pin for the given side. Defaults match the
        proven Pi setup: LEFT forward = HIGH, RIGHT forward = LOW (the
        right wheel is mirrored, so opposite logic level). Per-side
        invert_*_dir flags flip these for unusual motor mountings.

        Emits a DEBUG-level log line (lifted to INFO when env var
        ``NINA_NAV_LOG_DIR=1`` is set) so a user can confirm whether the
        ZF/DIR pin actually toggles between forward and backward
        commands. Useful when "all keys spin the wheels the same way"
        because the JYQD ZF input isn't seeing any logic-level change.
        """
        pins = self.config.pins
        forward = (direction == self.DIR_FORWARD)
        if side == self.SIDE_LEFT:
            level = 1 if forward else 0
            if self.config.invert_left_dir:
                level = 0 if level else 1
            self._backend.write(pins.l_dir, level)
            pin = pins.l_dir
        else:
            level = 0 if forward else 1
            if self.config.invert_right_dir:
                level = 0 if level else 1
            self._backend.write(pins.r_dir, level)
            pin = pins.r_dir
        if os.environ.get("NINA_NAV_LOG_DIR", "").strip() in ("1", "true", "yes"):
            log.info(
                "DIR %s -> %s (BCM%d=%d, invert_%s=%s)",
                side, direction, pin, level, side,
                self.config.invert_left_dir if side == self.SIDE_LEFT
                else self.config.invert_right_dir,
            )
        else:
            log.debug(
                "DIR %s -> %s (BCM%d=%d)",
                side, direction, pin, level,
            )

    def _apply_deadband(self, speed_percent: int) -> float:
        """Map user-facing 0..100 speed to actual PWM duty using deadband config."""
        if speed_percent <= 0:
            return 0.0
        lo = max(0.0, min(100.0, float(self.config.min_duty_percent)))
        hi = max(0.0, min(100.0, float(self.config.max_duty_percent)))
        if hi <= lo:
            return hi
        return lo + (speed_percent / 100.0) * (hi - lo)

    def _set_enable(self, side: str, enable: bool) -> None:
        """Drive both EL and Signal for the requested side. We keep these
        two in lock-step everywhere because the JYQD V7.3E2 only commutates
        when EL=HIGH AND Signal=HIGH; toggling just one leaves the chip in
        a half-state that ignores Z/F changes.

        Driving EL low also clears the per-side direction latch so the
        next set_wheels()/_apply_wheel() call knows it has to provide a
        fresh EL rising edge for the JYQD to re-sample DIR.
        """
        pins = self.config.pins
        en_pin, sig_pin = (
            (pins.l_en, pins.l_signal)
            if side == self.SIDE_LEFT
            else (pins.r_en, pins.r_signal)
        )
        level = 1 if enable else 0
        self._backend.write(en_pin, level)
        self._backend.write(sig_pin, level)
        if not enable:
            self._last_dir[side] = None

    def _set_status_led(self, red: bool = False,
                        green: bool = False, blue: bool = False) -> None:
        pins = self.config.pins
        self._backend.write(pins.led_red, 0 if red else 1)
        self._backend.write(pins.led_green, 0 if green else 1)
        self._backend.write(pins.led_blue, 0 if blue else 1)

    def _resolve_speed(self, requested: Optional[int]) -> int:
        if requested is None:
            return self.config.default_speed_percent
        return max(0, min(100, int(requested)))

    def _require_initialized(self) -> None:
        if not self._is_initialized:
            raise RuntimeError("NavigationManager is not initialized. Call initialize() first.")
