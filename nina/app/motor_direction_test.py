"""
Standalone diagnostic: drive each wheel through every direction in turn
so you can see (and probe with a multimeter) whether the JYQD ZF/DIR
input is responding to the BCM 25 / BCM 22 logic levels.

Use this when "all keyboard / D-pad keys spin the wheels in the same
direction" -- the symptom of the direction pin not toggling. The
script:

  1. Initialises Nina's BLDC pinout (BCM 25 = L_DIR, BCM 22 = R_DIR,
     BCM 12 = L_PWM, BCM 13 = R_PWM, BCM 18 = L_EN, BCM 10 = R_EN).
  2. For each wheel independently:
       * Sets DIR HIGH, kicks PWM up to the requested speed for
         3 seconds, stops.
       * Sets DIR LOW, kicks PWM up for 3 seconds, stops.
  3. Logs both the configured polarity and the actual GPIO level it
     wrote so the operator can correlate "I expected forward but it
     went backward" with the env-var invert flags.

Usage:
    python3 -m nina.app.motor_direction_test            # both wheels
    python3 -m nina.app.motor_direction_test --side left
    python3 -m nina.app.motor_direction_test --speed 30 --duration 4

Pass NINA_NAV_INVERT_LEFT=1 or NINA_NAV_INVERT_RIGHT=1 if the wheel
spins the *opposite* direction to what's logged. Pass
NINA_NAV_LOG_DIR=1 to also log every internal DIR-pin write the
NavigationManager performs.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

# Force the per-pin DIR write log lines to INFO before importing the
# NavigationManager - this script IS a diagnostic, so the user shouldn't
# need to remember to export NINA_NAV_LOG_DIR=1 themselves.
os.environ.setdefault("NINA_NAV_LOG_DIR", "1")

from nina.config.settings import build_settings  # noqa: E402
from nina.controllers.navigation_manager import (  # noqa: E402
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)


log = logging.getLogger("nina.motor_direction_test")


def _build_nav() -> NavigationManager:
    settings = build_settings().navigation
    cfg = NavigationConfig(
        pins=DEFAULT_PINS,
        backend_name=settings.backend_name,
        pwm_frequency_hz=settings.pwm_frequency_hz,
        default_speed_percent=settings.default_speed_percent,
        turn_duration_sec=settings.turn_duration_sec,
        min_duty_percent=settings.min_duty_percent,
        max_duty_percent=settings.max_duty_percent,
        kick_start_duty_percent=settings.kick_start_duty_percent,
        kick_start_duration_sec=settings.kick_start_duration_sec,
        invert_left_dir=settings.invert_left_dir,
        invert_right_dir=settings.invert_right_dir,
    )
    return NavigationManager(cfg)


def _expected_level(side: str, direction: str, nav: NavigationManager) -> int:
    """Mirror NavigationManager._set_direction() so we can print the
    BCM logic level the diagnostic *expects* the wire to be holding."""
    forward = (direction == NavigationManager.DIR_FORWARD)
    if side == NavigationManager.SIDE_LEFT:
        level = 1 if forward else 0
        if nav.config.invert_left_dir:
            level = 0 if level else 1
    else:
        level = 0 if forward else 1
        if nav.config.invert_right_dir:
            level = 0 if level else 1
    return level


def _exercise_side(nav: NavigationManager, side: str, speed: int, duration: float) -> None:
    """Spin the chosen wheel forward for `duration` then backward for
    `duration`, with a 1 s park between phases.

    The other wheel is held stopped (PWM 0, EL low) so any motion the
    operator sees is unambiguously coming from the wheel under test.

    Each phase prints the expected BCM logic level for the DIR pin AND
    the kick-start duty being used. This way the operator has a single
    line of "I should be seeing X V on BCM Y; the wheel should spin
    THIS direction now" without needing the NINA_NAV_LOG_DIR env var
    set (which is one fewer thing to forget on Jetson).
    """
    other = (
        NavigationManager.SIDE_RIGHT
        if side == NavigationManager.SIDE_LEFT
        else NavigationManager.SIDE_LEFT
    )
    pins = nav.config.pins
    side_pins = (
        (pins.l_dir, pins.l_en, pins.pwm_l)
        if side == NavigationManager.SIDE_LEFT
        else (pins.r_dir, pins.r_en, pins.pwm_r)
    )
    dir_pin, en_pin, pwm_pin = side_pins

    print(
        f"\n=== {side.upper()} wheel test "
        f"(DIR=BCM{dir_pin}, EN=BCM{en_pin}, PWM=BCM{pwm_pin}) ==="
    )

    for label, direction in (
        ("FORWARD",  NavigationManager.DIR_FORWARD),
        ("BACKWARD", NavigationManager.DIR_BACKWARD),
    ):
        expected = _expected_level(side, direction, nav)
        print(
            f"  -> {label} for {duration:.1f}s at {speed}% duty "
            f"(expect BCM{dir_pin} = {expected})"
        )
        nav._control_speed(other, True, 0, NavigationManager.DIR_FORWARD)  # noqa: SLF001
        nav._set_direction(side, direction)  # noqa: SLF001
        time.sleep(0.05)
        # Brief kick-start to break static friction; same idea as
        # NavigationManager._kick_start but inlined so each phase is
        # self-contained and easy to read in the log.
        kick = max(int(speed), int(nav.config.kick_start_duty_percent))
        kick = max(0, min(100, kick))
        kick_dur = max(0.0, float(nav.config.kick_start_duration_sec))
        if kick_dur > 0 and speed > 0:
            print(f"     kick-start {kick}% for {kick_dur:.2f}s")
            nav._control_speed(side, True, kick, direction)  # noqa: SLF001
            time.sleep(kick_dur)
        nav._control_speed(side, True, speed, direction)  # noqa: SLF001
        time.sleep(duration)
        nav._control_speed(side, True, 0, direction)  # noqa: SLF001
        # 1.5 s park gives the operator a beat to confirm "yes, the
        # wheel just stopped" before the next phase reverses direction.
        time.sleep(1.5)
    print(f"  done. {side.upper()} wheel parked.")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--side",
        choices=("left", "right", "both"),
        default="both",
        help="Which wheel(s) to exercise. Default both.",
    )
    parser.add_argument(
        "--speed",
        type=int,
        default=30,
        help="Duty cycle (0..100) used during each phase. Default 30.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3.0,
        help="Seconds to hold each direction. Default 3.",
    )
    args = parser.parse_args()

    nav = _build_nav()
    print(
        "\n--------------------------------------------------\n"
        "  Nina BLDC direction-pin diagnostic\n"
        "--------------------------------------------------"
    )
    print(
        "Polarity: LEFT forward = HIGH on BCM",
        nav.config.pins.l_dir,
        "(invert=" + str(nav.config.invert_left_dir) + ")",
    )
    print(
        "          RIGHT forward = LOW on BCM",
        nav.config.pins.r_dir,
        "(invert=" + str(nav.config.invert_right_dir) + ")",
    )

    try:
        nav.initialize()
    except Exception as exc:
        print(f"[FATAL] initialize() failed: {exc}")
        return 2

    nav.release_brake()

    try:
        if args.side in ("left", "both"):
            _exercise_side(nav, NavigationManager.SIDE_LEFT, args.speed, args.duration)
        if args.side in ("right", "both"):
            _exercise_side(nav, NavigationManager.SIDE_RIGHT, args.speed, args.duration)

        print(
            "\nFinished. If a wheel went the SAME way for both FORWARD "
            "and BACKWARD phases above, its ZF/DIR pin is not being\n"
            "honoured by the JYQD: re-check the BCM"
            f" {nav.config.pins.l_dir} / BCM {nav.config.pins.r_dir} wires "
            "or flip the polarity with\n"
            "  NINA_NAV_INVERT_LEFT=1   or   NINA_NAV_INVERT_RIGHT=1"
        )
    except KeyboardInterrupt:
        print("\n[INTERRUPT] aborted by user")
    finally:
        try:
            nav.emergency_stop()
        finally:
            nav.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
