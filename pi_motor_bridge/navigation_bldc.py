"""
Raspberry Pi BLDC navigation module for the Sirena Nina bot.

This is the proven, known-working Pi reference build (originally from
`/Downloads/navigation_bldc.py` in the user's RPi prototype). It runs on
the *Raspberry Pi* sitting next to the Jetson Orin Nano. The Jetson is
the brain (vision, autonomy, GUI, sensors); this Pi is the dedicated
motor controller. The Jetson sends short ASCII commands over a serial
link and `motor_bridge.py` dispatches into the helpers in this file.

Hardware: two JYQD_V7.3E2 BLDC drivers (one per wheel), powered from a
24 V battery (driver power) and 5 V from either the Pi or Jetson (logic
power). The Pi drives EL / DIR / PWM directly via pigpio - the Pi
GPIOs have enough drive strength to satisfy the JYQD opto-isolated
inputs cleanly, which is the whole reason we offloaded motor control
off the Jetson Orin Nano.

Pin map (Raspberry Pi 40-pin header, BCM numbering):

    Function       BCM    Physical pin    Notes
    L-EL           18     12              digital out
    L-DIR (Z/F)    25     22              digital out
    L-PWM (VR)     12     32              hardware PWM0
    R-EL           10     19              digital out
    R-DIR (Z/F)    22     15              digital out
    R-PWM (VR)     13     33              hardware PWM1
    Status RED     21     40              digital out (active-low)
    Status GREEN   20     38              digital out (active-low)
    Status BLUE    16     36              digital out (active-low)
    E-stop 1       17     11              digital in
    E-stop 2        5     29              digital in
    5 V to JYQDs    -      2 or 4         power (logic only)
    GND for L-JYQD  -     39              power
    GND for R-JYQD  -     34              power
    24 V to JYQDs   -      -              external battery to VCC screws

Direction polarity (matches the original prototype):
    Left  forward  =>  L_DIR HIGH
    Right forward  =>  R_DIR LOW    (right side is mirrored)

The polarity inversion lives in `control_speed()` below. If a wheel
spins the wrong way after a wiring change, flip the side's mapping
there - or, better, set NINA_NAV_INVERT_LEFT / NINA_NAV_INVERT_RIGHT
on the Jetson side so this file stays as-is.

Differences from the very first prototype version:
  * `set_wheels(...)` convenience helper added so the bridge can update
    both wheels in a single call. The original two-call sequence still
    works exactly the same.
  * `soft_stop()` added: PWM=0 on both sides, EL stays HIGH. The bridge
    uses this for the regular `STOP` command so the chip stays armed
    and the next direction change is instant. (`stop()` in the original
    is identical and is kept as an alias.)
  * `disable_drivers()` added: used by the bridge for `ESTOP` - sets
    PWM=0, drops EL LOW on both sides, but does NOT call pigpio.stop()
    (so the daemon stays alive and the bridge can re-arm without a
    full teardown).
  * pigpio connection lives in module state (`object_pi`) exactly like
    the original, so `motor_bridge.py` and the original `motor_control.py`
    can share this module unchanged.
"""

from __future__ import annotations

import logging
import time

import pigpio

# Module logger only - we deliberately do NOT call `logging.basicConfig`
# at import time. Doing so would hijack the root logger for any process
# that imports this module (e.g. `motor_bridge.py --verbose`'s argparse-
# driven log setup, or the unit tests). Callers that want the legacy
# `/tmp/ila_bot.log` file behaviour can opt in by calling
# `enable_legacy_file_log()` after configuring their own root logger.
log = logging.getLogger("nina.pi.bldc")


def enable_legacy_file_log(path: str = "/tmp/ila_bot.log") -> None:
    """Opt-in: tee this module's log records to the legacy file path.

    Kept so the original RPi prototype's `/tmp/ila_bot.log` workflow
    still works for anyone debugging directly on the Pi. Adds a
    handler to *this module's* logger only, so it never touches the
    root logger or other modules' configuration.
    """
    handler = logging.FileHandler(path)
    handler.setFormatter(
        logging.Formatter("%(asctime)-15s %(levelname)s %(message)s")
    )
    log.addHandler(handler)
    log.setLevel(logging.INFO)

RED = 21
GREEN = 20
BLUE = 16

ESP1 = 17
ESP2 = 5

R_EN = 10
R_DIR = 22
L_EN = 18
L_DIR = 25

PWM_R = 13
PWM_L = 12

PWM_FREQ_HZ = 2000

object_pi: "pigpio.pi | None" = None


def setup_gpio() -> bool:
    """Connect to pigpiod and configure all motor / LED / E-stop pins.

    Idempotent; safe to call repeatedly (will reuse an already-open
    pigpio handle). Returns True on success, False if the daemon
    isn't reachable.
    """
    global object_pi

    if object_pi is not None and object_pi.connected:
        return True

    print("[GPIO] Connecting to pigpio...")
    object_pi = pigpio.pi()

    if not object_pi.connected:
        print("[ERROR] pigpio connection failed (is pigpiod running?)")
        return False

    object_pi.set_mode(ESP1, pigpio.INPUT)
    object_pi.set_mode(ESP2, pigpio.INPUT)

    object_pi.set_mode(RED, pigpio.OUTPUT)
    object_pi.set_mode(GREEN, pigpio.OUTPUT)
    object_pi.set_mode(BLUE, pigpio.OUTPUT)

    object_pi.set_mode(R_EN, pigpio.OUTPUT)
    object_pi.set_mode(R_DIR, pigpio.OUTPUT)
    object_pi.set_mode(L_EN, pigpio.OUTPUT)
    object_pi.set_mode(L_DIR, pigpio.OUTPUT)

    # Park in a known-safe state: both EL HIGH (chip armed), both DIR
    # forward, both PWM 0. Identical to what stop() would leave us in.
    object_pi.write(L_EN, 1)
    object_pi.write(R_EN, 1)
    object_pi.write(L_DIR, 1)
    object_pi.write(R_DIR, 0)
    object_pi.hardware_PWM(PWM_L, PWM_FREQ_HZ, 0)
    object_pi.hardware_PWM(PWM_R, PWM_FREQ_HZ, 0)

    print("[GPIO] Setup complete")
    return True


def control_speed(side: str, enable: str, speed: int, direction: str) -> None:
    """Set a single wheel.

    side       : 'left' | 'right'
    enable     : 'enable' (EL HIGH, chip armed) | 'disable' (EL LOW, chip off)
    speed      : 0..100 (percent of PWM duty cycle)
    direction  : 'front' | 'back'  (logical wheel direction)

    Right-side polarity is mirrored here so callers can think in plain
    'front'/'back' terms without worrying about how the right motor is
    wired relative to the left one.
    """
    if object_pi is None:
        print("[ERROR] pigpio not initialized!")
        return

    speed = max(0, min(100, int(speed)))
    duty = int(speed * 10000)  # pigpio hardware_PWM range is 0..1_000_000

    if side == "left":
        object_pi.write(L_EN, 1 if enable == "enable" else 0)
        object_pi.write(L_DIR, 1 if direction == "front" else 0)
        object_pi.hardware_PWM(PWM_L, PWM_FREQ_HZ, duty)

    elif side == "right":
        object_pi.write(R_EN, 1 if enable == "enable" else 0)
        # Right side is mirrored - "front" wants R_DIR LOW.
        object_pi.write(R_DIR, 0 if direction == "front" else 1)
        object_pi.hardware_PWM(PWM_R, PWM_FREQ_HZ, duty)

    else:
        print(f"[ERROR] control_speed: unknown side '{side}'")


def set_wheels(
    left_speed: int,
    left_direction: str,
    right_speed: int,
    right_direction: str,
) -> None:
    """Update both wheels in one call. Both EL stay HIGH (chip armed)."""
    control_speed("left", "enable", left_speed, left_direction)
    control_speed("right", "enable", right_speed, right_direction)


def soft_stop() -> None:
    """PWM=0 on both wheels, EL HIGH. Chip stays armed.

    This is what we want for a normal `STOP` from the Jetson - the
    next SET command can change direction instantly without an arm
    delay.
    """
    control_speed("left", "enable", 0, "front")
    control_speed("right", "enable", 0, "front")


# Original prototype name; kept for backwards compat with motor_control.py.
def stop() -> None:
    soft_stop()
    time.sleep(0.1)
    log.info("Stopped")


def disable_drivers() -> None:
    """PWM=0 + EL LOW on both wheels. Chip is fully off (no torque).

    Used by the bridge's `ESTOP` command. Unlike `emergency_stop()`
    below this does NOT tear down the pigpio connection, so the
    bridge can re-arm with the next SET command.
    """
    control_speed("left", "disable", 0, "front")
    control_speed("right", "disable", 0, "front")


def forward_forever() -> None:
    """Both wheels forward at the prototype default of 15%."""
    print("[MOVE] Forward")
    stop()
    time.sleep(0.1)

    control_speed("left", "enable", 15, "front")
    control_speed("right", "enable", 15, "front")

    time.sleep(1)


def backward_forever() -> None:
    """Both wheels backward at the prototype default of 15%."""
    print("[MOVE] Backward")
    stop()
    time.sleep(0.1)

    control_speed("left", "enable", 15, "back")
    control_speed("right", "enable", 15, "back")

    time.sleep(1)


def turn_left() -> None:
    """In-place left spin (L back, R front) for ~2.3 s, then stop."""
    print("[MOVE] Turn Left")
    stop()
    time.sleep(0.1)

    control_speed("left", "enable", 15, "back")
    control_speed("right", "enable", 15, "front")

    time.sleep(2.3)
    stop()


def turn_right() -> None:
    """In-place right spin (L front, R back) for ~2.3 s, then stop."""
    print("[MOVE] Turn Right")
    stop()
    time.sleep(0.1)

    control_speed("left", "enable", 15, "front")
    control_speed("right", "enable", 15, "back")

    time.sleep(2.3)
    stop()


def emergency_stop() -> None:
    """Hard stop AND tear down pigpio. Use only at process exit."""
    global object_pi

    print("[EMERGENCY] STOP!")

    try:
        stop()
        disable_drivers()

        if object_pi:
            object_pi.write(RED, 1)
            object_pi.write(GREEN, 1)
            object_pi.write(BLUE, 1)

    except Exception as e:
        print("[ERROR]", e)

    finally:
        try:
            if object_pi:
                object_pi.stop()
        except Exception:
            pass
        object_pi = None

    print("[EMERGENCY] Safe shutdown complete")


def notifier(mode: str) -> None:
    """Status LED. mode in {'CONNECTED', 'ERROR', 'WAITING', 'OFF'}."""
    if object_pi is None:
        return

    object_pi.write(RED, 1)
    object_pi.write(GREEN, 1)
    object_pi.write(BLUE, 1)

    if mode == "CONNECTED":
        object_pi.write(GREEN, 0)
    elif mode == "ERROR":
        object_pi.write(RED, 0)
    elif mode == "WAITING":
        object_pi.write(BLUE, 0)
    elif mode == "OFF":
        pass  # already off (set HIGH above)
