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
from nina.services.startup_service import StartupService, health_mode


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


def ensure_motors_ready(dxl: DynamixelManager):
    """Initialize the bus, run a single health check (if not disabled),
    enable torque, and return the HealthReport. Returns None when health
    checks are disabled via NINA_HEALTH_CHECK=off so the caller can skip
    any missing-motor warnings entirely."""
    dxl.initialize_bus()
    health = None
    mode = health_mode()
    if mode != "off":
        health = dxl.run_health_check()
        if not health.connected:
            msg = (
                f"Health check: {health.detected_motors}/{health.expected_motors} motors responded. "
                f"{health.detail}"
            )
            if mode == "strict":
                raise SystemExit(f"Aborting (strict mode). {msg}")
            print(f"[warn] {msg} (continuing; set NINA_HEALTH_CHECK=strict to abort)")
    dxl.set_torque_all(True)
    return health


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
    health_check = sub.add_parser("health-check", help="Ping every expected motor and print which IDs responded (no torque, no motion).")
    health_check.add_argument("--passes", type=int, default=3, help="Number of ping passes to attempt (default 3)")
    sub.add_parser(
        "setup-bus",
        help=(
            "Print instructions (and the exact command) to install the udev "
            "rule that lets Nina lower the FTDI latency_timer to 1 ms "
            "without sudo - the #1 cause of intermittent missing motors."
        ),
    )
    bus_diag = sub.add_parser(
        "bus-diag",
        help=(
            "Per-motor bus reliability test: pings each motor N times "
            "and reports success rate. Pinpoints which motor / connector "
            "is bad when intermittent failures persist after the udev "
            "rule is installed."
        ),
    )
    bus_diag.add_argument("--samples", type=int, default=30,
                          help="Pings per motor (default 30)")
    run_action = sub.add_parser("run-action", help="Run a named action from the manifest.")
    run_action.add_argument("name", type=str, help="Action name (example: namaste)")
    run_action.add_argument(
        "--speed-scale",
        type=float,
        default=1.0,
        help="Playback speed multiplier (1.0 = recorded speed, 2.0 = 2x faster)",
    )
    run_action.add_argument(
        "--strict",
        action="store_true",
        help="Abort if any expected motor is missing from the bus or from the recording.",
    )
    run_action.add_argument(
        "--raw-speed",
        dest="smooth",
        action="store_false",
        help=(
            "Disable adaptive per-motor smoothing. Plays exactly the recorded "
            "speed/duration per frame (will likely feel jerky on dense recordings)."
        ),
    )
    run_action.set_defaults(smooth=True)
    sub.add_parser("list-actions", help="List available action names.")
    sub.add_parser(
        "sync-manifest",
        help="Scan recordings/ and add any unregistered action JSONs to the manifest.",
    )

    record_action = sub.add_parser("record-action", help="Record a new action file from live motors.")
    record_action.add_argument("--name", required=True, type=str, help="Action name")
    record_action.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds")
    record_action.add_argument(
        "--hz",
        type=float,
        default=5.0,
        help=(
            "Sampling rate in Hz. Lower = smoother playback (more bus headroom per frame). "
            "Recommended 4-8 for 11-motor chains. Higher rates can cause jerky playback."
        ),
    )
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
        "--no-register",
        dest="register",
        action="store_false",
        help="Skip auto-registering this action in the manifest (default is to register).",
    )
    record_action.set_defaults(register=True)

    sub.add_parser("release-arm", help="Disable torque on all arm motors (free for manual move).")
    sub.add_parser("hold-arm", help="Enable torque on all arm motors (lock current pose).")

    repair = sub.add_parser(
        "repair-action",
        help="Forward-fill missing motors in an action JSON (fixes recordings with dropped reads).",
    )
    repair.add_argument("input", type=str, help="Path to existing action JSON")
    repair.add_argument("--output", type=str, default=None,
                        help="Output path (default: overwrite input with .bak backup)")

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
        cmd_started = time.time()
        try:
            health = ensure_motors_ready(dxl)

            actions = action_runner.list_actions()
            if args.name not in actions:
                raise SystemExit(f"Unknown action '{args.name}'. Run list-actions to see available names.")
            action_path = settings.actions_dir / actions[args.name]
            if not action_path.exists():
                raise SystemExit(f"Action file not found: {action_path}")

            analysis = dxl.analyze_action_file(action_path)
            print(
                f"Action '{args.name}': {analysis['frame_count']} frames, "
                f"avg {analysis['avg_motors_per_frame']:.1f}/{len(dxl.expected_motor_ids)} motors per frame, "
                f"min {analysis['min_motors_per_frame']}."
            )
            if analysis["motors_missing"]:
                msg = (
                    f"[warn] Recording has no goals for motor IDs {analysis['motors_missing']}; "
                    "those joints will not move during playback. Re-record with the latest forward-fill "
                    "or run 'repair-action' to seed missing motors."
                )
                if args.strict:
                    raise SystemExit(f"Aborting (--strict). {msg}")
                print(msg)

            if args.strict and health is not None and not health.connected:
                raise SystemExit(
                    f"Aborting (--strict). Bus health: {health.detail}"
                )

            speed_scale = max(0.1, float(getattr(args, "speed_scale", 1.0)))
            smooth = bool(getattr(args, "smooth", True))
            dxl.execute_action_file(action_path, speed_scale=speed_scale, smooth=smooth)
            scale_note = f" at {speed_scale}x" if speed_scale != 1.0 else ""
            mode_note = "" if smooth else " (raw speed mode)"
            print(f"Action '{args.name}' executed from {action_path}{scale_note}{mode_note}")
            print(f"[timing] total command wall-clock: {(time.time() - cmd_started):.1f} s")
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

    if args.command == "bus-diag":
        try:
            dxl.initialize_bus()
            samples = max(5, int(args.samples))
            print(f"Pinging each motor {samples} times...")
            report = dxl.bus_reliability_report(samples=samples)
            print()
            print(f"{'ID':>3}  {'OK':>5}  {'Rate':>6}  {'AvgRT':>7}  {'WorstStreak':>11}  Verdict")
            print("-" * 60)
            unhealthy: List[int] = []
            for sid, stats in report.items():
                rate = stats["success_rate"]
                rt = stats["avg_response_ms"]
                rt_str = f"{rt:5.1f}ms" if rt is not None else "  --  "
                if rate >= 0.95:
                    verdict = "OK"
                elif rate >= 0.7:
                    verdict = "FLAKY"
                    unhealthy.append(sid)
                else:
                    verdict = "BAD"
                    unhealthy.append(sid)
                print(
                    f"{sid:>3}  {stats['successes']:>2}/{samples:<2}  "
                    f"{rate*100:5.1f}%  {rt_str:>7}  "
                    f"{stats['longest_failure_streak']:>11}  {verdict}"
                )
            print()
            if not unhealthy:
                print("All motors healthy on the bus.")
                return
            ids_str = ", ".join(str(s) for s in unhealthy)
            print(f"Unreliable motor IDs: {ids_str}")
            print()
            print("Hardware troubleshooting checklist (in order of likelihood):")
            print(
                "  1. RE-SEAT the daisy-chain connectors at and just before "
                "the lowest unreliable ID. A loose 3-pin Molex on one motor "
                "corrupts every motor downstream of it."
            )
            print(
                "  2. Check the 12V / 14.8V power rail at the LAST motor "
                "in the chain with a multimeter under load. If it's >0.4V "
                "below the supply, the chain is power-starved - shorter "
                "wires or a beefier PSU."
            )
            print(
                "  3. If unreliable IDs are clustered at the END of the "
                "chain (highest IDs), add a 220 ohm termination resistor "
                "between Data and GND on the last motor's free port."
            )
            print(
                "  4. Verify all motors are at the same baudrate and have "
                "unique IDs (a duplicate ID will collide every ping)."
            )
            return
        finally:
            dxl.close()

    if args.command == "setup-bus":
        repo_root = Path(__file__).resolve().parents[2]
        script = repo_root / "scripts" / "install-ftdi-udev.sh"
        print("Random missing motors on every health check is almost always")
        print("the FTDI latency_timer being stuck at the kernel default of 16 ms.")
        print("Without root we cannot lower it, so each Nina command silently")
        print("warns and proceeds with a slow bus.")
        print()
        print("To fix it permanently (one-shot), run:")
        print()
        print(f"    sudo bash {script}")
        print()
        print("Then unplug and replug the FTDI dongle (or reboot) and verify:")
        print()
        print("    cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer")
        print("    # should print: 1")
        print()
        print("After that no Nina command needs sudo for bus reliability.")
        return

    if args.command == "list-actions":
        actions = action_runner.list_actions()
        for name, file_path in actions.items():
            print(f"{name}: {file_path}")
        registered_files = {Path(p).name for p in actions.values()}
        unregistered = sorted(
            f for f in settings.recordings_dir.glob("*.json")
            if f.name not in registered_files
        )
        if unregistered:
            print()
            print("Unregistered recordings (run 'sync-manifest' to add):")
            for f in unregistered:
                print(f"  {f.stem}: recordings/{f.name}")
        return

    if args.command == "sync-manifest":
        actions = action_runner.list_actions()
        registered_files = {Path(p).name for p in actions.values()}
        added = []
        for f in sorted(settings.recordings_dir.glob("*.json")):
            if f.name in registered_files:
                continue
            name = f.stem
            if name in actions:
                print(f"[skip] '{name}' already registered to a different file ({actions[name]}).")
                continue
            action_runner.register_action(name, f"recordings/{f.name}")
            added.append(name)
        if added:
            print(f"Added {len(added)} action(s) to manifest: {', '.join(added)}")
        else:
            print("Manifest already up to date.")
        return

    if args.command == "record-action":
        try:
            dxl.initialize_bus()
            mode = health_mode()
            if mode != "off":
                health = dxl.run_health_check()
                if not health.connected:
                    msg = (
                        f"Health check: {health.detected_motors}/{health.expected_motors} motors responded. "
                        f"{health.detail}"
                    )
                    if mode == "strict":
                        raise SystemExit(f"Aborting (strict mode). {msg}")
                    print(f"[warn] {msg} (continuing; set NINA_HEALTH_CHECK=strict to abort)")

            print("Driving arm to neutral start pose...")
            dxl.set_torque_all(True)
            try:
                action_runner.run_named_action(settings.neutral_action_name)
            except (ValueError, FileNotFoundError) as exc:
                raise SystemExit(f"Failed to reach neutral start pose: {exc}")
            time.sleep(0.5)

            print("Releasing torque so the arm can be moved by hand...")
            stuck = dxl.set_torque_all(False)
            if stuck:
                print(
                    f"[warn] Could not release torque on motor IDs {stuck}. "
                    "These joints will stay rigid during recording. "
                    "Check the bus / power and re-run, or use 'release-arm' to retry."
                )
            else:
                print("All motors released. Arm should move freely now.")

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

            print("Priming capture (seeding last-known positions)...")
            seed = dxl.prime_capture(max_attempts=4)
            seeded = sum(1 for v in seed.values() if v is not None)
            print(f"Seeded {seeded}/{len(seed)} motors. Missing: {[s for s, v in seed.items() if v is None] or 'none'}")

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
            stats = dxl.capture_stats()
            miss_pct = 100.0 * stats["missed_reads"] / max(1, stats["total_reads"])
            print(
                f"Recording finished. Read miss rate: {stats['missed_reads']}/{stats['total_reads']} "
                f"({miss_pct:.1f}%). Tracked motors: {stats['tracked_motors']}."
            )

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
            stuck = dxl.set_torque_all(False)
            if stuck:
                print(
                    f"[warn] Could not release torque on motor IDs {stuck}. "
                    "Bus may be flaky - re-run 'release-arm' or check wiring/power."
                )
                raise SystemExit(1)
            print("Torque disabled on all arm motors. Arm is free to move.")
        finally:
            dxl.close()
        return

    if args.command == "hold-arm":
        try:
            dxl.initialize_bus()
            stuck = dxl.set_torque_all(True)
            if stuck:
                print(
                    f"[warn] Could not enable torque on motor IDs {stuck}. "
                    "Those joints will not hold position."
                )
                raise SystemExit(1)
            print("Torque enabled on all arm motors. Arm is holding pose.")
        finally:
            dxl.close()
        return

    if args.command == "repair-action":
        in_path = Path(args.input)
        if not in_path.exists():
            raise SystemExit(f"Input not found: {in_path}")
        payload = json.loads(in_path.read_text(encoding="utf-8"))
        frames = payload.get("frames", [])
        if not frames:
            raise SystemExit("No frames in input action file.")

        last_known: dict = {}
        first_frame_servos = frames[0].get("servos", {}) or {}
        for sid, spec in first_frame_servos.items():
            if isinstance(spec, dict) and "value" in spec:
                last_known[sid] = spec
        for sid in DEFAULT_MOTOR_IDS:
            last_known.setdefault(str(sid), {"type": "absolute", "value": 2048})

        filled_count = 0
        total_slots = 0
        for frame in frames:
            servos = frame.setdefault("servos", {})
            for sid in DEFAULT_MOTOR_IDS:
                key = str(sid)
                total_slots += 1
                if key in servos and isinstance(servos[key], dict) and "value" in servos[key]:
                    last_known[key] = servos[key]
                else:
                    servos[key] = dict(last_known[key])
                    filled_count += 1

        payload["frame_count"] = len(frames)
        out_path = Path(args.output) if args.output else in_path
        if out_path == in_path:
            backup = in_path.with_suffix(in_path.suffix + ".bak")
            backup.write_text(in_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Backup written: {backup}")
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        miss_pct = 100.0 * filled_count / max(1, total_slots)
        print(
            f"Repaired {out_path}: filled {filled_count}/{total_slots} missing motor entries ({miss_pct:.1f}%)."
        )
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
