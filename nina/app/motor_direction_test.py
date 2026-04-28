"""
Standalone diagnostic: drive each wheel forward, then backward, so you
can see (and probe with a multimeter) whether the JYQD ZF/DIR input is
actually responding to the BCM logic levels.

Use this when "all keyboard / D-pad keys spin the wheels in the same
direction" - the symptom of the direction pin not toggling. The script
mirrors the proven Sirena RPi reference build's `forward_forever` /
`backward_forever` sequence, so the only differences from the GUI's
Drive screen are: it picks one wheel at a time, and it prints what
voltage the operator should see on each pin.

This tool is **local mode only** - it drives the Jetson GPIOs directly.
If you're running with `NINA_NAV_MODE=remote` (motor control is
offloaded to a Raspberry Pi running `pi_motor_bridge`), use
`python3 -m nina.app.nav_bridge_test` instead.

Sequence per phase (matches the RPi `control_speed` flow exactly):

    1. stop()                          # PWM=0, EL stays HIGH
    2. sleep settle (default 0.1 s)
    3. control_speed(side, en=1, speed=N, dir=fwd|back)
    4. sleep --duration

There is no kick-start, no EL low->high re-edge, and no "Signal pin
gating" - none of which the working RPi build uses.

Pin defaults (mostly mirror the RPi reference; L-EL, L-DIR and R-DIR
are remapped because the matching pads are unusable as plain GPIO on
the Orin Nano carrier - see `nina.controllers.navigation_manager`
notes A, B and C):

    L-EL=BCM24 (pin 18)     R-EL=BCM10 (pin 19)
    L-DIR=BCM 6 (pin 31)    R-DIR=BCM23 (pin 16)
    L-PWM=BCM12 (pin 32)    R-PWM=BCM13 (pin 33)

Usage:
    python3 -m nina.app.motor_direction_test            # both wheels
    python3 -m nina.app.motor_direction_test --side left
    python3 -m nina.app.motor_direction_test --speed 30 --duration 4

Pass NINA_NAV_INVERT_LEFT=1 or NINA_NAV_INVERT_RIGHT=1 if a wheel spins
the *opposite* direction to what's logged.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from nina.config.settings import load_settings
from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)


log = logging.getLogger("nina.motor_direction_test")


def _build_nav() -> NavigationManager:
    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root).navigation
    if settings.mode != "local":
        raise SystemExit(
            "motor_direction_test only works in local mode (it probes\n"
            "the Jetson GPIOs directly). NINA_NAV_MODE is currently\n"
            f"'{settings.mode}'. For the remote (Pi bridge) path use:\n"
            "    python3 -m nina.app.nav_bridge_test\n"
        )
    cfg = NavigationConfig(
        pins=DEFAULT_PINS,
        backend_name=settings.backend_name,
        pwm_frequency_hz=settings.pwm_frequency_hz,
        default_speed_percent=settings.default_speed_percent,
        turn_duration_sec=settings.turn_duration_sec,
        invert_left_dir=settings.invert_left_dir,
        invert_right_dir=settings.invert_right_dir,
    )
    return NavigationManager(cfg)


def _expected_level(side: str, direction: str, nav: NavigationManager) -> int:
    """Mirror NavigationManager._control_speed() so we can print the
    BCM logic level the diagnostic *expects* the wire to be holding."""
    forward = direction == NavigationManager.DIR_FORWARD
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
    """Spin the chosen wheel forward for `duration`, park, then backward.

    The other wheel is parked (PWM=0) but EL stays HIGH (RPi-style soft
    stop). With per-side hardware PWM the other wheel won't move on its
    own; if it does, that JYQD's L-PWM/R-PWM screw is mis-wired (probably
    crossed onto the same pin as the wheel under test) - check the harness.
    """
    other = (
        NavigationManager.SIDE_RIGHT
        if side == NavigationManager.SIDE_LEFT
        else NavigationManager.SIDE_LEFT
    )
    pins = nav.config.pins
    if side == NavigationManager.SIDE_LEFT:
        dir_pin, en_pin, pwm_pin = pins.l_dir, pins.l_en, pins.pwm_l
    else:
        dir_pin, en_pin, pwm_pin = pins.r_dir, pins.r_en, pins.pwm_r

    print(
        f"\n=== {side.upper()} wheel test "
        f"(EN=BCM{en_pin}, DIR=BCM{dir_pin}, PWM=BCM{pwm_pin}) ==="
    )

    for label, direction in (
        ("FORWARD", NavigationManager.DIR_FORWARD),
        ("BACKWARD", NavigationManager.DIR_BACKWARD),
    ):
        expected = _expected_level(side, direction, nav)
        print(
            f"  -> {label} for {duration:.1f}s at {speed}% duty "
            f"(expect BCM{dir_pin} = {'HIGH' if expected else 'LOW'})"
        )
        # Park the other wheel (PWM=0, EL stays HIGH - same as stop()).
        nav._control_speed(other, True, 0, NavigationManager.DIR_FORWARD)  # noqa: SLF001
        # Park the wheel under test before changing direction. JYQD samples
        # DIR continuously so this isn't strictly required, but it gives
        # us a clean visual "stop... go the other way" cadence.
        nav._control_speed(side, True, 0, direction)  # noqa: SLF001
        time.sleep(nav.config.settle_delay_sec)
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
        "Polarity: LEFT  forward = HIGH on BCM "
        f"{nav.config.pins.l_dir} (invert={nav.config.invert_left_dir})"
    )
    print(
        "          RIGHT forward = LOW  on BCM "
        f"{nav.config.pins.r_dir} (invert={nav.config.invert_right_dir})"
    )

    try:
        nav.initialize()
    except Exception as exc:
        print(f"[FATAL] initialize() failed: {exc}")
        return 2

    try:
        if args.side in ("left", "both"):
            _exercise_side(nav, NavigationManager.SIDE_LEFT, args.speed, args.duration)
        if args.side in ("right", "both"):
            _exercise_side(nav, NavigationManager.SIDE_RIGHT, args.speed, args.duration)

        print(
            "\nFinished. If a wheel STILL spins the same way for both phases:\n"
            "  1. With a multimeter on the JYQD's ZF terminal, check that\n"
            "     the voltage actually toggles between phases (~3.3 V vs 0 V).\n"
            f"     LEFT  ZF should toggle on BCM {nav.config.pins.l_dir} (pin 22).\n"
            f"     RIGHT ZF should toggle on BCM {nav.config.pins.r_dir} (pin 15).\n"
            "  2. If the JYQD ZF pad doesn't track the Jetson pin, the\n"
            "     wire is broken or running through a level shifter that\n"
            "     is mangling the signal - fix the harness.\n"
            "  3. If the Jetson PIN itself doesn't toggle, run\n"
            "     'sudo python3 -m nina.app.pin_probe --pin <bcm>' to\n"
            "     re-vet that the pin is GPIO-capable in the current\n"
            "     jetson-io.py header config.\n"
            "  4. If only the polarity is wrong (e.g. FORWARD spins backward),\n"
            "     export NINA_NAV_INVERT_LEFT=1 / NINA_NAV_INVERT_RIGHT=1."
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
