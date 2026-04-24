import argparse
import json
import time
from pathlib import Path
from typing import List

from nina.config.settings import load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.services.startup_service import StartupService


DEFAULT_MOTOR_IDS: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Nina app bootstrap and controls.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("startup", help="Initialize motors, run health checks, and go neutral.")
    run_action = sub.add_parser("run-action", help="Run a named action from the manifest.")
    run_action.add_argument("name", type=str, help="Action name (example: namaste)")
    sub.add_parser("list-actions", help="List available action names.")

    record_action = sub.add_parser("record-action", help="Record a new action file from live motors.")
    record_action.add_argument("--name", required=True, type=str, help="Action name")
    record_action.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds")
    record_action.add_argument("--hz", type=float, default=20.0, help="Sampling rate in Hz")
    record_action.add_argument(
        "--register",
        action="store_true",
        help="Register action name in manifest after saving JSON",
    )

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
            action_path = action_runner.run_named_action(args.name)
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

            interval = 1.0 / args.hz
            sample_count = max(1, int(args.seconds * args.hz))
            frames = []
            print(f"Recording '{args.name}' for {args.seconds}s at {args.hz} Hz ({sample_count} samples)...")
            for _ in range(sample_count):
                frames.append(dxl.capture_frame(duration=interval))
                time.sleep(interval)

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


if __name__ == "__main__":
    main()
