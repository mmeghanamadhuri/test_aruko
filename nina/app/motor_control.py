"""
Operator CLI for Nina's BLDC navigation.

Run with the package path so imports resolve correctly:

    python3 -m nina.app.motor_control

Two backends are supported, chosen via `NINA_NAV_MODE`:

  NINA_NAV_MODE=local   (default) - drive the JYQDs directly from the
                          Jetson Orin Nano's GPIOs. `NINA_NAV_BACKEND`
                          picks 'jetson' or 'pigpio' inside this mode.

  NINA_NAV_MODE=remote  - send commands over serial to a Raspberry Pi
                          running `pi_motor_bridge/motor_bridge.py`.
                          Use this when the Pi is wired to the JYQDs.
                          Set `NINA_NAV_REMOTE_PORT` (default
                          /dev/ttyUSB0) and `NINA_NAV_REMOTE_BAUD`
                          (default 115200).

All other tunables (default_speed_percent, invert_*_dir, etc.) come
from `nina.config.settings.load_settings()` so this CLI behaves
identically to the GUI's Drive screen - the same env-var overrides
apply to both.
"""

import logging
from pathlib import Path

from nina.config.settings import load_settings
from nina.controllers.navigation_factory import build_navigation_manager


CONTROLS_HELP = (
    "\nControls:\n"
    "  w : Forward\n"
    "  s : Backward\n"
    "  a : Left Turn\n"
    "  d : Right Turn\n"
    "  b : Engage Brake (ZF)\n"
    "  r : Release Brake (ZF)\n"
    "  space / q : Stop\n"
    "  x : Exit\n"
    "--------------------------------------------------"
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("--------------------------------------------------")
    print("Sirena Technologies - Nina Manual Motor Control")
    print("--------------------------------------------------")

    repo_root = Path(__file__).resolve().parents[2]
    nav_settings = load_settings(repo_root).navigation
    print(f"[INIT] Navigation mode: {nav_settings.mode}")
    if nav_settings.mode == "remote":
        print(
            f"[INIT] Bridge target : {nav_settings.remote_serial_port} "
            f"@ {nav_settings.remote_baudrate}"
        )
    nav = build_navigation_manager(nav_settings)
    try:
        nav.initialize()
    except Exception as exc:
        print(f"[CRITICAL ERROR] {exc}")
        return

    print(CONTROLS_HELP)
    nav.stop()

    try:
        while True:
            try:
                user_input = input("Command >> ").lower().strip()
            except EOFError:
                break

            if user_input == "w":
                print("[ACTION] Moving Forward...")
                nav.forward()
            elif user_input == "s":
                print("[ACTION] Moving Backward...")
                nav.backward()
            elif user_input == "a":
                print("[ACTION] Turning Left...")
                nav.turn_left()
            elif user_input == "d":
                print("[ACTION] Turning Right...")
                nav.turn_right()
            elif user_input == "b":
                print("[ACTION] Engaging Brake...")
                nav.engage_brake()
            elif user_input == "r":
                print("[ACTION] Releasing Brake...")
                nav.release_brake()
            elif user_input in (" ", "q", ""):
                print("[ACTION] Stopping...")
                nav.stop()
            elif user_input == "x":
                print("[EXIT] Exiting program...")
                break
            else:
                print("Unknown command. Use w, a, s, d, space, or x.")

    except KeyboardInterrupt:
        print("\n[INTERRUPT] Program stopped by user.")
    finally:
        print("[CLEANUP] Stopping robot safely...")
        try:
            nav.emergency_stop()
        finally:
            nav.shutdown()
        print("Cleanup completed. Goodbye!")


if __name__ == "__main__":
    main()
