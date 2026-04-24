# Nina App Scaffold

This folder contains the new application structure for **Nina**, a wheeled bot with arms, display, and future navigation stack.

## Goals of this scaffold

- Boot-time startup flow for Nina runtime
- Dynamixel initialization health checks
- Default move to neutral pose on startup
- Action execution from named actions (example: `namaste`)
- Record and playback action files for new motions
- Clear extension points for audio, lidar/camera, BLDC, touch, IR, and UI

## Current layout

- `app/main.py`: entrypoint and CLI
- `services/startup_service.py`: boot sequence and health checks
- `controllers/dynamixel_manager.py`: servo init/check/neutral/playback hooks
- `controllers/action_runner.py`: action lookup and execution
- `services/recording_service.py`: record/playback session management
- `actions/manifest.json`: maps action names to JSON files
- `actions/neutral.json`: default boot neutral motion
- `actions/namaste.json`: example named motion
- `config/settings.py`: runtime config and paths
- `systemd/nina-app.service`: startup service template for Jetson

## Quick start

```bash
python -m nina.app.main startup
python -m nina.app.main run-action namaste
python -m nina.app.main record-action --name wave --seconds 5 --register
python -m nina.app.main list-actions
```

## Notes

- `DynamixelManager` now opens the serial bus, pings motors, toggles torque, reads positions, and plays action frames.
- Recording samples live present positions and saves action JSON files under `nina/actions/recordings/`.
- Tune `neutral.json` and `namaste.json` on hardware before using them as final robot motions.
