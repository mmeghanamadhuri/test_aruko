"""
End-to-end smoke test for the Jetson <-> Raspberry Pi motor bridge.

Run this on the Jetson, with the Pi already running
`pi_motor_bridge/motor_bridge.py` and the JYQDs / wheels powered up.
It will:

    1. PING the bridge so we know the link is alive.
    2. Drive both wheels FORWARD for `--duration` s.
    3. STOP, pause briefly.
    4. Drive both wheels BACKWARD for `--duration` s.
    5. STOP.
    6. (unless `--no-turn`) Turn LEFT in place for `--duration` s.
    7. (unless `--no-turn`) Turn RIGHT in place for `--duration` s.
    8. Final STOP, then ESTOP, then close the link.

If anything fails, the script logs the offending command + response
and exits with code 1.

Usage:

    # one-shot (uses NINA_NAV_REMOTE_PORT / BAUD env vars or defaults)
    python3 -m nina.app.nav_bridge_test

    # explicit port / speed / duration:
    python3 -m nina.app.nav_bridge_test --port /dev/ttyUSB0 --speed 25 --duration 3

    # just ping, don't drive:
    python3 -m nina.app.nav_bridge_test --ping-only

    # skip the turn phases (useful for the very first bench test):
    python3 -m nina.app.nav_bridge_test --no-turn

This tool deliberately bypasses `NINA_NAV_MODE` so you can test the
bridge even when the rest of the stack is configured for local mode.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from nina.controllers.remote_navigation_manager import (
    RemoteNavigationConfig,
    RemoteNavigationManager,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        default=os.environ.get("NINA_NAV_REMOTE_PORT", "/dev/ttyUSB0"),
        help="Serial port the bridge is listening on (default /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.environ.get("NINA_NAV_REMOTE_BAUD", "115200")),
        help="Baud rate (default 115200; must match motor_bridge.py)",
    )
    parser.add_argument(
        "--speed",
        type=int,
        default=20,
        help="Per-wheel duty cycle 0..100 (default 20)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3.0,
        help="Seconds to hold each phase (default 3.0)",
    )
    parser.add_argument(
        "--ping-only",
        action="store_true",
        help="Skip motor motion; just verify the link.",
    )
    parser.add_argument(
        "--no-turn",
        action="store_true",
        help="Skip the left/right in-place spin phases.",
    )
    parser.add_argument(
        "--invert-left",
        action="store_true",
        help="Flip left wheel direction (debug aid; same as NINA_NAV_INVERT_LEFT=1)",
    )
    parser.add_argument(
        "--invert-right",
        action="store_true",
        help="Flip right wheel direction (debug aid; same as NINA_NAV_INVERT_RIGHT=1)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("--------------------------------------------------")
    print("Nina motor bridge - Jetson-side end-to-end test")
    print(f"  port: {args.port}  baud: {args.baud}")
    print(f"  speed: {args.speed}%  per-phase duration: {args.duration}s")
    print("--------------------------------------------------")

    cfg = RemoteNavigationConfig(
        serial_port=args.port,
        baudrate=args.baud,
        default_speed_percent=args.speed,
        invert_left_dir=args.invert_left,
        invert_right_dir=args.invert_right,
    )
    nav = RemoteNavigationManager(cfg)

    try:
        nav.initialize()
    except Exception as exc:
        print(f"[FATAL] {exc}")
        print(
            "\nChecks:\n"
            "  1. Is the bridge running on the Pi?\n"
            "       sudo systemctl status motor-bridge\n"
            "       (or)  sudo python3 motor_bridge.py --verbose\n"
            "  2. Does the device exist on the Jetson?\n"
            f"       ls -l {args.port}\n"
            "  3. Are TX/RX/GND wired correctly across the boards?\n"
        )
        return 1

    if args.ping_only:
        print("[TEST] Link OK - bridge replied to PING. (--ping-only set, exiting.)")
        nav.shutdown()
        return 0

    try:
        print(f"[TEST] FORWARD {args.duration}s ...")
        nav.forward(args.speed)
        time.sleep(args.duration)
        nav.stop()
        time.sleep(0.8)

        print(f"[TEST] BACKWARD {args.duration}s ...")
        nav.backward(args.speed)
        time.sleep(args.duration)
        nav.stop()
        time.sleep(0.8)

        if not args.no_turn:
            print(f"[TEST] TURN LEFT {args.duration}s (L=back R=forward) ...")
            nav.turn_left(args.speed, args.duration)
            time.sleep(0.5)

            print(f"[TEST] TURN RIGHT {args.duration}s (L=forward R=back) ...")
            nav.turn_right(args.speed, args.duration)
            time.sleep(0.5)

        print("[TEST] Done. Wheels parked.")
        return 0

    except KeyboardInterrupt:
        print("\n[INTERRUPT] aborted by user")
        return 130
    finally:
        try:
            nav.emergency_stop()
        finally:
            nav.shutdown()


if __name__ == "__main__":
    sys.exit(main())
