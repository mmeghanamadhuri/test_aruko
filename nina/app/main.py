import argparse
import json
import time
from pathlib import Path
from typing import List

from nina.config.settings import load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)
from nina.services.startup_service import StartupService


DEFAULT_MOTOR_IDS: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

NEUTRAL_FRAME_DELAY_SEC = 0.2
NEUTRAL_FRAME_DURATION_SEC = 1.5
NEUTRAL_FRAME_SPEED = 600
DEFAULT_RECORD_MOTOR_SPEED = 800


def build_neutral_frame() -> dict:
    """Frame that drives every arm motor to mid-range. Prepended to recordings
    so playback is always safe regardless of current pose."""
    return {
        "delay": NEUTRAL_FRAME_DELAY_SEC,
        "duration": NEUTRAL_FRAME_DURATION_SEC,
        "speed": NEUTRAL_FRAME_SPEED,
        "servos": {
            str(sid): {"type": "absolute", "value": 2048}
            for sid in DEFAULT_MOTOR_IDS
        },
    }


def ensure_motors_ready(dxl: DynamixelManager) -> None:
    dxl.initialize_bus()
    health = dxl.run_health_check()
    if not health.connected:
        raise SystemExit(
            f"Motor health check failed ({health.detected_motors}/{health.expected_motors} motors). {health.detail}"
        )
    dxl.set_torque_all(True)


def build_app():
    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root)

    dxl = DynamixelManager(
        serial_port=settings.serial_port,
        baudrate=settings.baudrate,
        expected_motor_ids=DEFAULT_MOTOR_IDS,
    )
    action_runner = ActionRunner(
        manifest_path=settings.manifest_path,
        actions_dir=settings.actions_dir,
        dxl=dxl,
    )
    startup_service = StartupService(dxl, action_runner, settings.neutral_action_name)
    return settings, dxl, action_runner, startup_service


def build_navigation(settings) -> NavigationManager:
    nav_config = NavigationConfig(
        pins=DEFAULT_PINS,
        backend_name=settings.navigation.backend_name,
        pwm_frequency_hz=settings.navigation.pwm_frequency_hz,
        default_speed_percent=settings.navigation.default_speed_percent,
        turn_duration_sec=settings.navigation.turn_duration_sec,
    )
    return NavigationManager(nav_config)


def run_nav_command(nav: NavigationManager, command: str,
                    speed: int, duration: float, hold: float) -> None:
    nav.initialize()
    try:
        if command == "nav-forward":
            nav.forward(speed_percent=speed)
            time.sleep(hold)
            nav.stop()
        elif command == "nav-back":
            nav.backward(speed_percent=speed)
            time.sleep(hold)
            nav.stop()
        elif command == "nav-left":
            nav.turn_left(speed_percent=speed, duration=duration)
        elif command == "nav-right":
            nav.turn_right(speed_percent=speed, duration=duration)
        elif command == "nav-stop":
            nav.stop()
        elif command == "nav-brake":
            nav.engage_brake()
        elif command == "nav-release":
            nav.release_brake()
        else:
            raise ValueError(f"Unknown nav command: {command}")
    finally:
        nav.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Nina app bootstrap and controls.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("startup", help="Initialize motors, run health checks, and go neutral.")
    health_check = sub.add_parser("health-check", help="Ping every expected motor and print which IDs responded (no torque, no motion).")
    health_check.add_argument("--passes", type=int, default=3, help="Number of ping passes to attempt (default 3)")
    run_action = sub.add_parser("run-action", help="Run a named action from the manifest.")
    run_action.add_argument("name", type=str, help="Action name (example: namaste)")
    run_action.add_argument(
        "--speed-scale",
        type=float,
        default=1.0,
        help="Playback speed multiplier (1.0 = recorded speed, 2.0 = 2x faster)",
    )
    sub.add_parser("list-actions", help="List available action names.")

    record_action = sub.add_parser("record-action", help="Record a new action file from live motors.")
    record_action.add_argument("--name", required=True, type=str, help="Action name")
    record_action.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds")
    record_action.add_argument("--hz", type=float, default=20.0, help="Sampling rate in Hz")
    record_action.add_argument(
        "--motor-speed",
        type=int,
        default=DEFAULT_RECORD_MOTOR_SPEED,
        help="Dynamixel moving speed register value stored per frame (1-1023, ~1023=max)",
    )
    record_action.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="Seconds to wait after releasing torque before sampling starts",
    )
    record_action.add_argument(
        "--hold-after",
        action="store_true",
        help="Re-enable torque after recording so the arm holds the final pose (default off)",
    )
    record_action.add_argument(
        "--register",
        action="store_true",
        help="Register action name in manifest after saving JSON",
    )

    sub.add_parser("release-arm", help="Disable torque on all arm motors (free for manual move).")
    sub.add_parser("hold-arm", help="Enable torque on all arm motors (lock current pose).")

    for nav_cmd, nav_help in (
        ("nav-forward", "Drive forward, then stop after --hold seconds."),
        ("nav-back", "Drive backward, then stop after --hold seconds."),
        ("nav-left", "Turn left for --duration seconds, then stop."),
        ("nav-right", "Turn right for --duration seconds, then stop."),
    ):
        nav_parser = sub.add_parser(nav_cmd, help=nav_help)
        nav_parser.add_argument("--speed", type=int, default=None, help="Speed percent 0-100 (default from env)")
        nav_parser.add_argument("--duration", type=float, default=None, help="Turn duration in seconds")
        nav_parser.add_argument("--hold", type=float, default=1.0, help="Forward/back hold seconds before stop")

    sub.add_parser("nav-stop", help="Immediately stop the BLDC drive motors.")
    sub.add_parser("nav-brake", help="Engage ZF brake on both wheels.")
    sub.add_parser("nav-release", help="Release ZF brake on both wheels.")

    args = parser.parse_args()
    settings, dxl, action_runner, startup_service = build_app()

    if args.command == "startup":
        try:
            result = startup_service.boot()
            if not result.success:
                raise SystemExit(result.message)
            print(result.message)
        finally:
            dxl.close()
        return

    if args.command == "run-action":
        try:
            ensure_motors_ready(dxl)
            speed_scale = max(0.1, float(getattr(args, "speed_scale", 1.0)))
            action_path = action_runner.run_named_action(args.name, speed_scale=speed_scale)
            scale_note = f" at {speed_scale}x" if speed_scale != 1.0 else ""
            print(f"Action '{args.name}' executed from {action_path}{scale_note}")
        finally:
            dxl.close()
        return

    if args.command == "health-check":
        try:
            dxl.initialize_bus()
            health = dxl.run_health_check(passes=args.passes)
            print(
                f"Motors found: {health.detected_motors}/{health.expected_motors}. {health.detail}"
            )
            if not health.connected:
                raise SystemExit(1)
        finally:
            dxl.close()
        return

    if args.command == "list-actions":
        actions = action_runner.list_actions()
        for name, file_path in actions.items():
            print(f"{name}: {file_path}")
        return

    if args.command == "record-action":
        try:
            dxl.initialize_bus()
            health = dxl.run_health_check()
            if not health.connected:
                raise SystemExit(
                    f"Motor health check failed ({health.detected_motors}/{health.expected_motors} motors). {health.detail}"
                )

            print("Driving arm to neutral start pose...")
            dxl.set_torque_all(True)
            try:
                action_runner.run_named_action(settings.neutral_action_name)
            except (ValueError, FileNotFoundError) as exc:
                raise SystemExit(f"Failed to reach neutral start pose: {exc}")
            time.sleep(0.5)

            print("Releasing torque so the arm can be moved by hand...")
            dxl.set_torque_all(False)

            countdown = max(0.0, float(args.countdown))
            if countdown > 0:
                print(f"Get ready. Recording starts in {countdown:.0f}s...")
                whole = int(countdown)
                for remaining in range(whole, 0, -1):
                    print(f"  {remaining}...")
                    time.sleep(1.0)
                leftover = countdown - whole
                if leftover > 0:
                    time.sleep(leftover)

            interval = 1.0 / args.hz
            sample_count = max(1, int(args.seconds * args.hz))
            motor_speed = max(1, min(1023, int(args.motor_speed)))
            captured_frames = []
            print(
                f"Recording '{args.name}' for {args.seconds}s at {args.hz} Hz "
                f"({sample_count} samples, motor speed={motor_speed})..."
            )
            for _ in range(sample_count):
                captured_frames.append(dxl.capture_frame(duration=interval, speed=motor_speed))
                time.sleep(interval)
            print("Recording finished.")

            if args.hold_after:
                print("Re-enabling torque to hold the final pose.")
                dxl.set_torque_all(True)
            else:
                print("Leaving torque disabled. Use 'hold-arm' to lock the arm.")

            frames = [build_neutral_frame()] + captured_frames

            out_path = settings.recordings_dir / f"{args.name}.json"
            payload = {
                "robot": "nina",
                "description": f"Recorded action: {args.name}",
                "frame_count": len(frames),
                "frames": frames,
            }
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"Recording saved: {out_path} ({len(frames)} frames, neutral prepended)")

            if args.register:
                action_runner.register_action(args.name, f"recordings/{args.name}.json")
                print(f"Registered action '{args.name}' in manifest.")
        finally:
            dxl.close()
        return

    if args.command == "release-arm":
        try:
            dxl.initialize_bus()
            dxl.set_torque_all(False)
            print("Torque disabled on all arm motors. Arm is free to move.")
        finally:
            dxl.close()
        return

    if args.command == "hold-arm":
        try:
            dxl.initialize_bus()
            dxl.set_torque_all(True)
            print("Torque enabled on all arm motors. Arm is holding pose.")
        finally:
            dxl.close()
        return

    if args.command in ("nav-forward", "nav-back", "nav-left", "nav-right",
                        "nav-stop", "nav-brake", "nav-release"):
        nav = build_navigation(settings)
        speed = getattr(args, "speed", None)
        duration = getattr(args, "duration", None)
        hold = getattr(args, "hold", 1.0)
        run_nav_command(nav, args.command, speed=speed, duration=duration, hold=hold)
        print(f"Navigation command '{args.command}' completed.")
        return


if __name__ == "__main__":
    main()
