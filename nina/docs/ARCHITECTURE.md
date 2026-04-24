# Nina Robot Working Structure

This structure supports staged development for Nina (5ft wheeled bot with arms and display).

## Stage 1 (now): Arm bring-up and motion actions

- Boot sequence:
  1. Initialize Dynamixel bus
  2. Verify motor health
  3. Enable torque
  4. Move to neutral
- Runtime actions:
  - Run named action files (example: `namaste`)
  - Record new actions and store as JSON

## Stage 2: Robot capabilities

- Audio and speakers
  - text-to-speech events
  - sound cues for startup and action completion
- Navigation stack
  - lidar + camera fusion hooks
  - obstacle events and local autonomy
- Locomotion
  - BLDC motor controller service
  - velocity and pose commands
- Sensors and HMI
  - touch + IR event services
  - touchscreen app shell

## Suggested module expansion

- `nina/controllers/audio_manager.py`
- `nina/controllers/navigation_manager.py`
- `nina/controllers/bldc_manager.py`
- `nina/services/sensor_hub.py`
- `nina/services/ui_gateway.py`
- `nina/app/runtime.py` for long-running orchestrator loop
