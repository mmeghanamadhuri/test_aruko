#!/usr/bin/env python3
"""
Sirena Nina motor bridge daemon (Raspberry Pi side).

Architecture:

    Jetson Orin Nano  ----- USB-to-TTL adapter -----> Raspberry Pi UART
       (brain)          ASCII commands @ 115200 8N1     (motor controller)
                                                                v
                                                          navigation_bldc.py
                                                                v
                                                          2x JYQD_V7.3E2

The Jetson runs the GUI / vision / autonomy / sensors and sends motor
commands here. This daemon is the *only* thing on the Pi that touches
GPIO; it owns pigpio.

Wire protocol
-------------
Line-based ASCII, terminated with `\\n`, 115200 8N1, no flow control.

Commands (Jetson -> Pi):

    PING                              -> PONG
    SET <ldir> <lspeed> <rdir> <rspeed>
                                      -> OK | ERR <msg>
        ldir / rdir : F (forward) | B (backward)
        lspeed / rspeed : 0..100 (percent PWM duty)
    STOP                              -> OK            (PWM=0, EL HIGH)
    ESTOP                             -> OK            (PWM=0, EL LOW)
    LED <CONNECTED|ERROR|WAITING|OFF> -> OK | ERR <msg>

Async events (Pi -> Jetson, unsolicited):

    READY                             on bridge boot, after GPIO init
    EVT WATCHDOG                      when no command received for
                                      `watchdog_timeout_sec` and motors
                                      were forced to stop

Examples (raw bytes, with explicit `\\n`):

    SET F 30 F 30\\n      -> both wheels forward at 30% duty
    SET F 25 B 25\\n      -> in-place right turn
    STOP\\n               -> coast to a stop, chip stays armed
    ESTOP\\n              -> drop EL LOW on both wheels (no torque)
    PING\\n               -> PONG\\n

Watchdog
--------
If no command arrives for `watchdog_timeout_sec` (default 1.5 s while
the wheels are moving), the bridge calls `soft_stop()` so the bot can't
run away when the Jetson loses serial / is rebooted / panics. The
watchdog only fires when the wheels are actually commanded to non-zero
PWM, so an idle bot that's just listening doesn't generate spurious
WATCHDOG events.

JYQD startup kick
-----------------
The JYQD_V7.3E2 BLDC drivers won't reliably start commutating from a
stopped rotor with a single (EL HIGH, PWM=N) command - they need an
explicit EL falling-then-rising edge plus a PWM 0->N rising edge to
pick up commutation cleanly. Reverse cold-starts are even harder:
hub motors with non-canonical hall-sensor wiring (very common with
cheap parts) stall completely if you ask the chip to commutate
reverse from a stopped rotor. The bridge handles both cases
transparently via `nav.warm_reverse_and_set()`: pure-forward kicks
get the standard ~200 ms warm-up / disable / re-enable pulse, and
reverse kicks get an additional ~200 ms forward "puff" first so the
rotor is already moving when the chip re-arms in reverse. Steady-
state SETs that just nudge the speed in the same direction call
`nav.set_wheels()` and stream
through with no kick, so the GUI slider stays smooth.

Run
---
    sudo python3 motor_bridge.py                 # default port /dev/serial0 @ 115200
    sudo python3 motor_bridge.py --port /dev/ttyAMA0
    sudo python3 motor_bridge.py --baud 230400 --watchdog 2.0

`sudo` is required because pigpio needs root for GPIO access on the Pi.
For unattended boot use the systemd unit (see install_service.sh).
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import navigation_bldc as nav

try:
    import serial  # pyserial
except ImportError:
    print("[FATAL] pyserial not installed. Run: sudo apt install -y python3-serial")
    sys.exit(1)


log = logging.getLogger("nina.pi.bridge")


def ensure_pigpiod() -> bool:
    """Verify pigpiod is running. Do NOT spawn a new daemon out-of-band.

    On a proper install the systemd unit shipped in
    `motor-bridge.service` declares `Wants=pigpiod.service`, so pigpiod
    is already up by the time the bridge starts. The previous version
    of this function would `os.system("pigpiod")` (or
    `os.system("sudo pigpiod")`!) when it found nothing running, which
    could orphan a daemon process if the bridge crashed before
    `nav.emergency_stop()` and would hang forever if `sudo` prompted
    for a password on a headless boot.

    Returns True if pigpiod is reachable, False otherwise. The caller
    is expected to print actionable next steps and exit on False.
    """
    if shutil.which("pgrep") is None:
        log.warning("`pgrep` not on PATH; skipping pigpiod liveness check")
        return True
    try:
        rc = subprocess.call(
            ["pgrep", "-x", "pigpiod"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log.warning("pgrep for pigpiod failed (%s); will let pigpio.pi() decide", exc)
        return True
    if rc == 0:
        log.info("pigpiod is running")
        return True
    log.error("pigpiod is NOT running")
    return False


class MotorBridge:
    """ASCII command dispatcher + watchdog."""

    def __init__(self, port: str, baud: int, watchdog_timeout_sec: float) -> None:
        self.port_path = port
        self.baud = baud
        self.watchdog_timeout_sec = watchdog_timeout_sec

        self._ser: "serial.Serial | None" = None
        self._lock = threading.Lock()
        self._running = True
        self._wheels_active = False  # True while non-zero PWM is commanded
        # Last commanded direction per side ("front"/"back"). Used to
        # decide whether the next SET needs the JYQD startup kick - a
        # direction change always requires a kick because the chip has
        # to re-arm in the new commutation order.
        self._last_ldir: "str | None" = None
        self._last_rdir: "str | None" = None
        self._last_cmd_time = time.time()
        self._watchdog_already_tripped = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not ensure_pigpiod():
            print(
                "[FATAL] pigpiod is not running on this Pi.\n"
                "  Start it once:           sudo systemctl start pigpiod\n"
                "  Enable on boot:          sudo systemctl enable pigpiod\n"
                "  Or, manually:            sudo pigpiod\n"
                "If pigpiod is missing entirely (Bookworm dropped it from\n"
                "apt), build it from source - see pi_motor_bridge/README.md\n"
                "section 0.2.d 'pigpio (build from source on Bookworm)'."
            )
            sys.exit(1)

        if not nav.setup_gpio():
            print("[FATAL] GPIO setup failed (pigpio not reachable?)")
            sys.exit(1)

        nav.notifier("WAITING")

        try:
            self._ser = serial.Serial(self.port_path, self.baud, timeout=0.1)
        except Exception as exc:
            print(f"[FATAL] Cannot open {self.port_path}: {exc}")
            nav.emergency_stop()
            sys.exit(1)

        # Drain anything left in the OS buffer from previous runs.
        time.sleep(0.2)
        self._ser.reset_input_buffer()

        print(f"[BRIDGE] Listening on {self.port_path} @ {self.baud} 8N1")
        self._send_line("READY")

        signal.signal(signal.SIGINT, self._signal_shutdown)
        signal.signal(signal.SIGTERM, self._signal_shutdown)

        self._start_watchdog_thread()

        self._read_loop()

    def _start_watchdog_thread(self) -> bool:
        """Spawn the watchdog thread if the watchdog is enabled.

        Setting `--watchdog` (or `NINA_BRIDGE_WATCHDOG_SEC`) to 0 (or
        any non-positive value) is the supported way to DISABLE the
        bridge-side watchdog entirely. Useful when debugging erratic
        stops / direction glitches that may be triggered by the
        watchdog kicking in faster than the Jetson's heartbeat. With
        the watchdog off, the wheels only stop when the Jetson
        explicitly says STOP / ESTOP / commands a direction change
        with zero PWM - so the operator must be ready to hit ESTOP
        from the GUI if a heartbeat goes silent.

        Returns True if the watchdog thread was started, False if
        it was disabled.
        """
        if self.watchdog_timeout_sec <= 0:
            print("[BRIDGE] Watchdog: DISABLED (watchdog_timeout_sec<=0)")
            return False

        print(f"[BRIDGE] Watchdog timeout: {self.watchdog_timeout_sec}s")
        wd = threading.Thread(
            target=self._watchdog_loop, name="watchdog", daemon=True
        )
        wd.start()
        return True

    def shutdown(self) -> None:
        self._running = False
        try:
            nav.emergency_stop()
        except Exception:
            pass
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        print("[BRIDGE] Shutdown complete")

    def _signal_shutdown(self, *_: object) -> None:
        print("[BRIDGE] Signal received, shutting down...")
        self.shutdown()
        sys.exit(0)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Pull bytes off the serial port, split on newline, dispatch."""
        assert self._ser is not None
        buffer = b""
        while self._running:
            try:
                chunk = self._ser.read(256)
            except Exception as exc:
                print(f"[BRIDGE] Serial read failed: {exc}")
                time.sleep(0.2)
                continue

            if chunk:
                buffer += chunk
                while b"\n" in buffer:
                    raw, _, buffer = buffer.partition(b"\n")
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    response = self._dispatch(line)
                    if response is not None:
                        self._send_line(response)

    def _watchdog_loop(self) -> None:
        """Stop the wheels if the Jetson stops talking to us."""
        while self._running:
            time.sleep(0.1)
            if not self._wheels_active:
                self._watchdog_already_tripped = False
                continue
            if time.time() - self._last_cmd_time > self.watchdog_timeout_sec:
                if not self._watchdog_already_tripped:
                    print(
                        f"[BRIDGE] Watchdog: no command for "
                        f"{self.watchdog_timeout_sec}s while moving - stopping"
                    )
                    try:
                        with self._lock:
                            nav.soft_stop()
                            self._wheels_active = False
                            self._watchdog_already_tripped = True
                        self._send_line("EVT WATCHDOG")
                    except Exception as exc:
                        print(f"[BRIDGE] Watchdog stop failed: {exc}")

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, line: str) -> "str | None":
        # Bump the watchdog on *any* well-formed line.
        self._last_cmd_time = time.time()
        self._watchdog_already_tripped = False

        parts = line.split()
        if not parts:
            return None
        cmd = parts[0].upper()

        try:
            if cmd == "PING":
                return "PONG"

            if cmd == "SET":
                if len(parts) != 5:
                    return "ERR usage: SET <ldir> <lspeed> <rdir> <rspeed>"
                ldir = self._parse_dir(parts[1])
                rdir = self._parse_dir(parts[3])
                if ldir is None or rdir is None:
                    return "ERR direction must be F or B"
                try:
                    lspeed = max(0, min(100, int(parts[2])))
                    rspeed = max(0, min(100, int(parts[4])))
                except ValueError:
                    return "ERR speed must be int 0..100"

                target_active = (lspeed > 0) or (rspeed > 0)
                # The JYQDs need an explicit EL/PWM edge sequence to
                # start commutating. Kick when:
                #   - we're transitioning out of a stopped state, OR
                #   - either wheel's direction changed since the last SET.
                # A steady-state SET that's just nudging the speed
                # streams through nav.set_wheels() with no kick so the
                # GUI slider still feels responsive.
                needs_kick = target_active and (
                    not self._wheels_active
                    or ldir != self._last_ldir
                    or rdir != self._last_rdir
                )

                with self._lock:
                    if needs_kick:
                        log.debug(
                            "SET kick: active=%s last=(%s,%s) -> (%s,%s)",
                            self._wheels_active,
                            self._last_ldir,
                            self._last_rdir,
                            ldir,
                            rdir,
                        )
                        # warm_reverse_and_set is a strict superset of
                        # kick_and_set: pure-forward kicks pass through
                        # at zero extra cost, while any wheel commanded
                        # to reverse gets a forward "puff" first to give
                        # the rotor momentum the JYQD's fallback
                        # commutation table can latch onto. Without it,
                        # cheap hub motors with non-canonical hall
                        # wiring stall on cold-start reverse no matter
                        # how high the PWM duty.
                        nav.warm_reverse_and_set(lspeed, ldir, rspeed, rdir)
                    else:
                        nav.set_wheels(lspeed, ldir, rspeed, rdir)
                    self._wheels_active = target_active
                    self._last_ldir = ldir
                    self._last_rdir = rdir
                return "OK"

            if cmd == "STOP":
                with self._lock:
                    nav.soft_stop()
                    self._wheels_active = False
                    # Direction is intentionally NOT cleared - the next
                    # SET in the same direction still needs a kick (the
                    # rotor is stopped) but it's still useful to know
                    # which way we last commanded each wheel for logs.
                return "OK"

            if cmd == "ESTOP":
                with self._lock:
                    nav.disable_drivers()
                    self._wheels_active = False
                    # ESTOP drops EL LOW, so any next SET must kick
                    # regardless of direction. Force a re-kick by
                    # invalidating the cached direction.
                    self._last_ldir = None
                    self._last_rdir = None
                return "OK"

            if cmd == "LED":
                if len(parts) != 2:
                    return "ERR usage: LED <CONNECTED|ERROR|WAITING|OFF>"
                mode = parts[1].upper()
                if mode not in ("CONNECTED", "ERROR", "WAITING", "OFF"):
                    return f"ERR unknown LED mode '{mode}'"
                nav.notifier(mode)
                return "OK"

            return f"ERR unknown command '{cmd}'"

        except Exception as exc:
            return f"ERR {exc}"

    @staticmethod
    def _parse_dir(token: str) -> "str | None":
        t = token.upper()
        if t == "F":
            return "front"
        if t == "B":
            return "back"
        return None

    # ------------------------------------------------------------------
    # Serial helpers
    # ------------------------------------------------------------------

    def _send_line(self, line: str) -> None:
        if self._ser is None:
            return
        try:
            self._ser.write((line + "\n").encode("utf-8"))
            self._ser.flush()
        except Exception as exc:
            print(f"[BRIDGE] Serial write failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sirena Nina motor bridge (Pi side)")
    parser.add_argument(
        "--port",
        default=os.environ.get("NINA_BRIDGE_PORT", "/dev/serial0"),
        help="Serial device (default: /dev/serial0; try /dev/ttyAMA0 if that's symlinked)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.environ.get("NINA_BRIDGE_BAUD", "115200")),
        help="Baud rate (default: 115200)",
    )
    parser.add_argument(
        "--watchdog",
        type=float,
        default=float(os.environ.get("NINA_BRIDGE_WATCHDOG_SEC", "1.5")),
        help=(
            "Stop wheels if no command for this many seconds while moving "
            "(default: 1.5). Set to 0 (or any non-positive number) to "
            "DISABLE the watchdog entirely - in that mode the bridge will "
            "only stop on explicit STOP / ESTOP from the Jetson. Same as "
            "NINA_BRIDGE_WATCHDOG_SEC=0."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Echo every dispatched command to stdout",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("--------------------------------------------------")
    print("Sirena Nina - Motor Bridge (Raspberry Pi side)")
    print("--------------------------------------------------")

    bridge = MotorBridge(
        port=args.port,
        baud=args.baud,
        watchdog_timeout_sec=args.watchdog,
    )
    try:
        bridge.start()
    except KeyboardInterrupt:
        print("\n[BRIDGE] Interrupted")
    finally:
        bridge.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
