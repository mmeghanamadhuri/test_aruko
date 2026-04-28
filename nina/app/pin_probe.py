"""
Bare-metal GPIO toggle for multimeter probing.

When the motor-direction test reports "wheel doesn't reverse" and the
JYQD ZF screw terminal is stuck at an indeterminate voltage, you need
to isolate which segment of the chain is broken:

    Jetson SoC --> 40-pin header pin --> wire --> [level shifter?]
                                                   --> JYQD screw --> chip

This tool drives ONE BCM pin in a slow, steady square wave (5 s HIGH,
5 s LOW, repeat). No PWM, no Signal-gate gymnastics, no other pins
touched. The operator probes the multimeter at one point at a time and
correlates the printed "now HIGH/now LOW" lines with the reading.

Usage:
    # Probe LEFT direction pin (default L_DIR = BCM 22 = physical pin 15)
    python3 -m nina.app.pin_probe --pin 22

    # Probe RIGHT direction pin (default R_DIR = BCM 12 = physical pin 32)
    python3 -m nina.app.pin_probe --pin 12

    # Faster cadence for scope work
    python3 -m nina.app.pin_probe --pin 22 --period 0.5

Stop with Ctrl-C; the pin is left LOW on exit.

What to do with the readings:

    A) Probe AT THE JETSON HEADER (push the probe directly onto pin 15
       or pin 32) while the tool is running.
       - If you see ~3.3 V during HIGH and ~0 V during LOW: the Jetson
         is fine. Move probe to (B).
       - If you see a constant voltage that doesn't toggle: the Jetson
         pin isn't actually being driven (pinmux issue, dead pin, or
         the pin is locked to an alt-function like PWM/UART/I2C).

    B) Probe AT THE JYQD ZF SCREW TERMINAL while the tool is running.
       - If you see ~3.3 V during HIGH and ~0 V during LOW: the wire
         is good and the JYQD is reading clean levels - the issue is
         elsewhere (likely the F/R-vs-edge thing, see motor_direction_test).
       - If you see a constant voltage (e.g. 0.5 V, 1.1 V): the signal
         is being killed between Jetson pin and JYQD screw. Most likely
         a passive level shifter / divider. Bypass it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time


log = logging.getLogger("nina.pin_probe")


_PHYSICAL_BY_BCM = {
    # Subset of the 40-pin header most likely to be probed; only the
    # pins we use for navigation. Add more if the operator picks an
    # exotic pin.
    4: 7, 5: 29, 6: 31, 7: 26, 8: 24, 9: 21, 10: 19, 11: 23,
    12: 32, 13: 33, 14: 8, 15: 10, 16: 36, 17: 11, 18: 12,
    19: 35, 20: 38, 21: 40, 22: 15, 23: 16, 24: 18, 25: 22,
    26: 37, 27: 13,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pin",
        type=int,
        required=True,
        help="BCM pin number to toggle (e.g. 22 for left ZF, 12 for right ZF).",
    )
    parser.add_argument(
        "--period",
        type=float,
        default=5.0,
        help="Seconds to hold each level. Default 5.0.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="Number of HIGH+LOW cycles to run. 0 = run forever (Ctrl-C to stop).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pin = args.pin
    physical = _PHYSICAL_BY_BCM.get(pin, "?")
    print(
        "\n--------------------------------------------------\n"
        f"  Pin probe: BCM {pin}  (physical pin {physical})\n"
        f"  Period: {args.period:.2f}s  Cycles: "
        f"{'infinite' if args.cycles == 0 else args.cycles}\n"
        "--------------------------------------------------\n"
    )
    print("Put a multimeter (DC volts, 20 V range) on the probe point.")
    print("You should see ~3.3 V during HIGH and ~0 V during LOW.")
    print("If the voltage doesn't change, the pin isn't being driven.\n")

    # IMPORTANT: must match the model used by gpio_backend.py
    # (JETSON_ORIN_NANO). Jetson.GPIO maps BCM numbers to physical pins
    # via a per-model SoC pad table; the wrong model silently routes
    # writes to a different pad, so the physical pin appears "stuck"
    # while the tool prints clean HIGH/LOW transitions. Override with
    # NINA_JETSON_MODEL=JETSON_NANO only on the older T210 dev kit.
    try:
        os.environ.setdefault(
            "JETSON_MODEL_NAME",
            os.environ.get("NINA_JETSON_MODEL", "JETSON_ORIN_NANO"),
        )
        import Jetson.GPIO as GPIO  # type: ignore
    except Exception as exc:
        print(f"[FATAL] Jetson.GPIO import failed: {exc}", file=sys.stderr)
        return 2

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    except Exception as exc:
        print(f"[FATAL] GPIO setup failed for BCM {pin}: {exc}", file=sys.stderr)
        return 2

    cycle = 0
    try:
        while True:
            GPIO.output(pin, GPIO.HIGH)
            print(f"[cycle {cycle + 1:>3}] BCM {pin} = HIGH (~3.3 V)")
            time.sleep(args.period)
            GPIO.output(pin, GPIO.LOW)
            print(f"[cycle {cycle + 1:>3}] BCM {pin} = LOW  (~0.0 V)")
            time.sleep(args.period)
            cycle += 1
            if args.cycles and cycle >= args.cycles:
                break
    except KeyboardInterrupt:
        print("\n[INTERRUPT] aborted by operator")
    finally:
        try:
            GPIO.output(pin, GPIO.LOW)
        except Exception:
            pass
        try:
            GPIO.cleanup(pin)
        except Exception:
            pass
        print("Pin parked LOW. Done.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
