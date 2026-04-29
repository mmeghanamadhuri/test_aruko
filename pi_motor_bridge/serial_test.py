#!/usr/bin/env python3
"""
Stand-alone serial loopback for verifying the Pi <-> Jetson link or
manually exercising the bridge from the Pi itself.

Two modes:

  loopback : open the serial port and echo bytes back. Run this on the
             Pi, then on the Jetson type bytes into a `screen
             /dev/ttyUSB0 115200` session - whatever you type should
             appear on the Pi terminal. Use this BEFORE motor_bridge.py
             to confirm the cable / adapter / wiring is right.

  client   : interactive client that talks the Nina bridge protocol.
             Type human commands ('forward', 'back', 'left', 'right',
             'stop', 'estop', 'ping', 'set <ldir> <lspeed> <rdir> <rspeed>')
             and they get translated into protocol lines and sent to a
             *running* motor_bridge.py instance. Useful for bench-
             testing motors without needing the Jetson UI up.

Run on the Pi:

    # 1) Verify the cable is good (with motor_bridge.py NOT running):
    python3 serial_test.py loopback --port /dev/serial0

    # 2) Then start the bridge in another terminal and from a 3rd
    #    terminal on the Pi (or from any machine on the same wire):
    python3 serial_test.py client --port /dev/serial0
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("[FATAL] pyserial not installed. Run: sudo apt install -y python3-serial")
    sys.exit(1)


def cmd_loopback(args: argparse.Namespace) -> int:
    print(f"[LOOPBACK] {args.port} @ {args.baud} - echoing every byte. Ctrl-C to exit.")
    with serial.Serial(args.port, args.baud, timeout=0.1) as ser:
        try:
            while True:
                data = ser.read(256)
                if data:
                    sys.stdout.buffer.write(data)
                    sys.stdout.flush()
                    ser.write(data)
        except KeyboardInterrupt:
            print("\n[LOOPBACK] Stopped")
    return 0


def cmd_client(args: argparse.Namespace) -> int:
    speed = args.speed
    print(f"[CLIENT] Connected to {args.port} @ {args.baud}")
    print(f"[CLIENT] Default per-wheel speed: {speed}%")
    print("Commands:")
    print("  w / forward      -> SET F <s> F <s>")
    print("  s / back         -> SET B <s> B <s>")
    print("  a / left         -> SET B <s> F <s>")
    print("  d / right        -> SET F <s> B <s>")
    print("  q / stop         -> STOP")
    print("  e / estop        -> ESTOP")
    print("  p / ping         -> PING")
    print("  speed <n>        -> change default speed (0..100)")
    print("  raw <line>       -> send <line> verbatim")
    print("  set <ld> <ls> <rd> <rs>  -> raw SET")
    print("  exit             -> quit")
    print()

    with serial.Serial(args.port, args.baud, timeout=args.timeout) as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()

        def send(line: str) -> None:
            print(f"  -> {line}")
            ser.write((line + "\n").encode("utf-8"))
            ser.flush()
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                resp = ser.readline().decode("utf-8", errors="ignore").strip()
                if resp:
                    print(f"  <- {resp}")
                    return
            print("  <- (no response)")

        try:
            while True:
                raw = input("nina> ").strip()
                if not raw:
                    continue
                lower = raw.lower()
                parts = lower.split()
                head = parts[0]

                if head in ("exit", "quit", "x"):
                    return 0
                elif head in ("w", "forward", "f"):
                    send(f"SET F {speed} F {speed}")
                elif head in ("s", "back", "backward", "b"):
                    send(f"SET B {speed} B {speed}")
                elif head in ("a", "left", "l"):
                    send(f"SET B {speed} F {speed}")
                elif head in ("d", "right", "r"):
                    send(f"SET F {speed} B {speed}")
                elif head in ("q", "stop"):
                    send("STOP")
                elif head in ("e", "estop"):
                    send("ESTOP")
                elif head in ("p", "ping"):
                    send("PING")
                elif head == "speed" and len(parts) == 2:
                    try:
                        speed = max(0, min(100, int(parts[1])))
                        print(f"  default speed -> {speed}%")
                    except ValueError:
                        print("  speed must be an integer 0..100")
                elif head == "raw" and len(parts) >= 2:
                    send(raw[len("raw"):].strip())
                elif head == "set" and len(parts) == 5:
                    send(raw.upper())
                else:
                    print(f"  ?? unknown: {raw}")
        except (KeyboardInterrupt, EOFError):
            print("\n[CLIENT] Stopped")

    return 0


def _add_common_args(p: argparse.ArgumentParser) -> None:
    """Flags shared by every subcommand.

    Putting them on a shared parent (via `parents=`) lets users type them
    in either order: `serial_test.py loopback --port X` *or*
    `serial_test.py --port X loopback`. argparse normally requires
    parent-parser flags to come before the subcommand, which trips
    everyone up.
    """
    p.add_argument("--port", default="/dev/serial0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--timeout",
        type=float,
        default=0.5,
        help="Per-line response timeout (seconds, client mode)",
    )
    p.add_argument(
        "--speed",
        type=int,
        default=20,
        help="Default per-wheel speed for shorthand commands (client mode)",
    )


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common)

    parser = argparse.ArgumentParser(description=__doc__, parents=[common])

    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("loopback", parents=[common], help="Echo every byte back")
    sub.add_parser("client", parents=[common], help="Interactive bridge client")

    args = parser.parse_args()

    if args.mode == "loopback":
        return cmd_loopback(args)
    if args.mode == "client":
        return cmd_client(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
