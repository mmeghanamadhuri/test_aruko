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


def ensure_motors_ready(dxl: DynamixelManager) -> None:
    dxl.initialize_bus()
    health = dxl.run_health_check()
    if not health.connected:
        print(
            f"[warn] Motor health check: {health.detected_motors}/"
            f"{health.expected_motors} motors responded. {health.detail} "
            "(continuing; missing motors will simply not move)"
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
        min_duty_percent=settings.navigation.min_duty_percent,
        max_duty_percent=settings.navigation.max_duty_percent,
        kick_start_duty_percent=settings.navigation.kick_start_duty_percent,
        kick_start_duration_sec=settings.navigation.kick_start_duration_sec,
        invert_left_dir=settings.navigation.invert_left_dir,
        invert_right_dir=settings.navigation.invert_right_dir,
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
    run_action = sub.add_parser("run-action", help="Run a named action from the manifest.")
    run_action.add_argument("name", type=str, help="Action name (example: namaste)")
    run_action.add_argument(
        "--no-smooth",
        action="store_true",
        help="Disable interpolated playback (use raw frame-by-frame stepping).",
    )
    run_action.add_argument(
        "--sub-hz",
        type=float,
        default=100.0,
        help="Interpolated update rate during smooth playback in Hz (default: 100).",
    )
    run_action.add_argument(
        "--max-speed",
        type=int,
        default=1023,
        help="Per-motor speed limit during smooth playback (0-1023, 1023 = max).",
    )
    sub.add_parser("list-actions", help="List available action names.")

    record_action = sub.add_parser("record-action", help="Record a new action file from live motors.")
    record_action.add_argument("--name", required=True, type=str, help="Action name")
    record_action.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds")
    record_action.add_argument("--hz", type=float, default=20.0, help="Sampling rate in Hz")
    record_action.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="Seconds between releasing torque and the start of sampling (gives you time to grab the arm).",
    )
    record_action.add_argument(
        "--hold-after",
        action="store_true",
        help="Re-enable torque on every motor after recording so the arm holds its final pose (default: leave released).",
    )
    record_action.add_argument(
        "--register",
        action="store_true",
        help="Register action name in manifest after saving JSON",
    )

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

    nav_test = sub.add_parser(
        "nav-test-pin",
        help="Drive a single GPIO pin or PWM output for diagnostics. Probe with a multimeter.",
    )
    nav_test.add_argument("--pin", required=True, type=int, help="BCM pin number to drive")
    nav_test.add_argument("--mode", choices=("high", "low", "pwm"), default="high",
                          help="Drive HIGH, LOW, or PWM at --duty")
    nav_test.add_argument("--duty", type=float, default=50.0, help="PWM duty 0-100 (only for --mode pwm)")
    nav_test.add_argument("--hold", type=float, default=5.0, help="Seconds to hold the signal")

    nav_dir = sub.add_parser(
        "nav-test-direction",
        help="Run motors alternating forward/back every --interval seconds for diagnosing F/R wiring.",
    )
    nav_dir.add_argument("--side", choices=("left", "right", "both"), default="both",
                         help="Which wheel to test")
    nav_dir.add_argument("--speed", type=int, default=80, help="Speed percent")
    nav_dir.add_argument("--interval", type=float, default=3.0, help="Seconds per direction phase")
    nav_dir.add_argument("--cycles", type=int, default=3, help="Number of fwd/back cycles")

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
            mode = "smooth" if not args.no_smooth else "stepped"
            print(
                f"Playing '{args.name}' ({mode} mode, sub_hz={args.sub_hz}, "
                f"max_speed={args.max_speed})..."
            )
            action_path = action_runner.run_named_action(
                args.name,
                smooth=not args.no_smooth,
                sub_hz=args.sub_hz,
                max_speed=args.max_speed,
            )
            print(f"Action '{args.name}' executed from {action_path}")
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
            ensure_motors_ready(dxl)

            print("Releasing torque on all motors so you can move the arm by hand...")
            dxl.set_torque_all(False)

            countdown = max(0.0, float(args.countdown))
            if countdown > 0:
                whole = int(countdown)
                for remaining in range(whole, 0, -1):
                    print(f"  starting in {remaining}...")
                    time.sleep(1.0)
                fractional = countdown - whole
                if fractional > 0:
                    time.sleep(fractional)

            interval = 1.0 / args.hz
            sample_count = max(1, int(args.seconds * args.hz))
            frames = []
            print(f"Recording '{args.name}' for {args.seconds}s at {args.hz} Hz ({sample_count} samples)...")
            for _ in range(sample_count):
                frames.append(dxl.capture_frame(duration=interval))
                time.sleep(interval)
            print("Recording complete.")

            if args.hold_after:
                print("Re-enabling torque so the arm holds its current pose.")
                dxl.set_torque_all(True)
            else:
                print("Leaving torque released; the arm is free to move. Run `startup` or `run-action <name>` to re-engage torque.")

            out_path = settings.recordings_dir / f"{args.name}.json"
            payload = {
                "robot": "nina",
                "description": f"Recorded action: {args.name}",
                "frame_count": len(frames),
                "frames": frames,
            }
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"Recording saved: {out_path}")

            if args.register:
                action_runner.register_action(args.name, f"recordings/{args.name}.json")
                print(f"Registered action '{args.name}' in manifest.")
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

    if args.command == "nav-test-direction":
        nav = build_navigation(settings)
        nav.initialize()
        pins = nav.config.pins
        try:
            sides_label = args.side
            print(
                f"Direction test on {sides_label} side(s). "
                f"L_ZF/DIR=BCM{pins.l_dir} R_ZF/DIR=BCM{pins.r_dir}. "
                f"Watch the wheel(s) - they should physically reverse between phases."
            )
            for cycle in range(args.cycles):
                for direction in ("forward", "backward"):
                    dir_const = nav.DIR_FORWARD if direction == "forward" else nav.DIR_BACKWARD
                    if args.side in ("left", "both"):
                        zf_level = (1 if direction == "forward" else 0)
                        if nav.config.invert_left_dir:
                            zf_level = 0 if zf_level else 1
                        print(
                            f"[cycle {cycle + 1}/{args.cycles}] LEFT -> {direction} @ {args.speed}% "
                            f"(BCM{pins.l_dir} -> {'HIGH' if zf_level else 'LOW'})"
                        )
                        nav._control_speed(nav.SIDE_LEFT, True, args.speed, dir_const)
                    if args.side in ("right", "both"):
                        zf_level = (1 if direction == "forward" else 0)
                        if nav.config.invert_right_dir:
                            zf_level = 0 if zf_level else 1
                        print(
                            f"[cycle {cycle + 1}/{args.cycles}] RIGHT -> {direction} @ {args.speed}% "
                            f"(BCM{pins.r_dir} -> {'HIGH' if zf_level else 'LOW'})"
                        )
                        nav._control_speed(nav.SIDE_RIGHT, True, args.speed, dir_const)
                    time.sleep(args.interval)
                    nav.stop()
                    time.sleep(0.3)
        finally:
            nav.shutdown()
        print(
            "Direction test done. If the wheel kept spinning the same way:\n"
            "  1. Confirm JYQD ZF input is wired to the BCM pin shown above.\n"
            "  2. Probe that pin with 'nav-test-pin --pin <BCM> --mode high/low' to confirm the level.\n"
            "  3. JYQD ZF threshold is ~3V; Jetson 3.3V should be fine but check with a meter.\n"
            "  4. If the level toggles correctly but the motor doesn't reverse, set\n"
            "     NINA_NAV_INVERT_LEFT=1 / NINA_NAV_INVERT_RIGHT=1 (some motors swing the other way)."
        )
        return

    if args.command == "nav-test-pin":
        from nina.controllers.gpio_backend import create_backend
        backend = create_backend(settings.navigation.backend_name)
        backend.setup()
        try:
            if args.mode == "pwm":
                backend.configure_pwm(args.pin, settings.navigation.pwm_frequency_hz)
                backend.set_duty(args.pin, args.duty)
                print(f"BCM {args.pin} -> PWM @ {args.duty}% duty, {settings.navigation.pwm_frequency_hz} Hz for {args.hold}s")
            else:
                backend.configure_output(args.pin)
                value = 1 if args.mode == "high" else 0
                backend.write(args.pin, value)
                print(f"BCM {args.pin} -> {'HIGH' if value else 'LOW'} for {args.hold}s")
            time.sleep(args.hold)
        finally:
            backend.shutdown()
        return


if __name__ == "__main__":
    main()
