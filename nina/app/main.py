import argparse
import json
import threading
import time
from pathlib import Path
from typing import List

from nina.config.settings import load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.controllers.navigation_factory import build_navigation_manager
from nina.services.audio_player import AudioPlayer
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


def build_navigation(settings):
    """Return a NavigationManager (local) or RemoteNavigationManager (Pi bridge).

    Selection is made by `NavigationSettings.mode`, driven by the
    `NINA_NAV_MODE` env var. See `nina.controllers.navigation_factory`.
    """
    return build_navigation_manager(settings.navigation)


def run_nav_command(nav, command: str,
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
        default=50.0,
        help="Interpolated update rate during smooth playback in Hz (default: 50).",
    )
    run_action.add_argument(
        "--max-speed",
        type=int,
        default=1023,
        help="Per-motor speed limit during smooth playback (0-1023, 1023 = max).",
    )
    run_action.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help=(
            "Playback time multiplier (1.0 = recorded tempo, 0.5 = half speed, "
            "2.0 = double speed). Smoothness is preserved at any value."
        ),
    )
    run_action.add_argument(
        "--no-audio",
        action="store_true",
        help="Skip the audio clip associated with this action (if any).",
    )
    run_action.add_argument(
        "--audio-offset",
        type=float,
        default=None,
        help=(
            "Override the manifest audio_offset for this action (seconds to wait "
            "after motion starts before firing the audio clip)."
        ),
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
    sub.add_parser(
        "nav-bridge-ping",
        help=(
            "Open the configured navigation backend and run a quick "
            "connectivity check. In remote mode this PINGs the Pi "
            "bridge over serial; in local mode it just confirms the "
            "Jetson backend can be initialised."
        ),
    )

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
        audio_timer: "threading.Timer | None" = None
        try:
            ensure_motors_ready(dxl)
            mode = "smooth" if not args.no_smooth else "stepped"
            audio_rel = action_runner.get_action_audio(args.name)
            audio_path = (
                settings.actions_dir / audio_rel
                if (audio_rel and not args.no_audio)
                else None
            )
            audio_offset = (
                args.audio_offset
                if args.audio_offset is not None
                else action_runner.get_action_audio_offset(args.name)
            )
            audio_offset = max(0.0, float(audio_offset))

            audio_note = ""
            if audio_path and audio_path.exists():
                offset_note = f" +{audio_offset:.2f}s" if audio_offset > 0 else ""
                audio_note = f", audio={audio_path.name}{offset_note}"
                player = AudioPlayer()
                if audio_offset > 0:
                    audio_timer = threading.Timer(
                        audio_offset, player.play, args=(audio_path,)
                    )
                    audio_timer.daemon = True
                    audio_timer.start()
                else:
                    player.play(audio_path)
            elif audio_rel and not args.no_audio:
                audio_note = f", audio MISSING ({audio_rel})"
            print(
                f"Playing '{args.name}' ({mode} mode, sub_hz={args.sub_hz}, "
                f"max_speed={args.max_speed}, speed={args.speed}x{audio_note})..."
            )
            action_path = action_runner.run_named_action(
                args.name,
                smooth=not args.no_smooth,
                sub_hz=args.sub_hz,
                max_speed=args.max_speed,
                speed=args.speed,
            )
            print(f"Action '{args.name}' executed from {action_path}")
        except Exception:
            if audio_timer is not None:
                audio_timer.cancel()
            raise
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

    if args.command == "nav-bridge-ping":
        nav = build_navigation(settings)
        mode = settings.navigation.mode
        print(f"[INIT] Navigation mode: {mode}")
        if mode == "remote":
            print(
                f"[INIT] Bridge target : {settings.navigation.remote_serial_port} "
                f"@ {settings.navigation.remote_baudrate}"
            )
        try:
            nav.initialize()
            print("[OK] Navigation backend initialised.")
            if mode == "remote":
                print("[OK] PING -> PONG round-trip succeeded.")
        except Exception as exc:
            print(f"[FAIL] {exc}")
            raise SystemExit(1)
        finally:
            try:
                nav.shutdown()
            except Exception:
                pass
        return

    if args.command == "nav-test-direction":
        if settings.navigation.mode != "local":
            raise SystemExit(
                "nav-test-direction probes Jetson GPIOs directly and only\n"
                "works in local mode. NINA_NAV_MODE is currently\n"
                f"'{settings.navigation.mode}'. For the remote (Pi bridge)\n"
                "path use:  python3 -m nina.app.nav_bridge_test\n"
            )
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
                        # Right wheel polarity is mirrored on the RPi
                        # reference: forward = LOW on R_DIR.
                        zf_level = (0 if direction == "forward" else 1)
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
        if settings.navigation.mode != "local":
            raise SystemExit(
                "nav-test-pin drives a Jetson GPIO directly and only works\n"
                "in local mode. NINA_NAV_MODE is currently\n"
                f"'{settings.navigation.mode}'.\n"
            )
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
