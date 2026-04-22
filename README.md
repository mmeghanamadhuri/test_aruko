# Jetson Orin NX Robotics (Carbot Sweep)

Robotics on NVIDIA Jetson Orin NX: vision (window buttons), Dynamixel arm, linear actuator, optional BLDC experiments.

## Documentation

**[CARBOT_COMPLETE_GUIDE.md](CARBOT_COMPLETE_GUIDE.md)** — Single reference: dependencies, hardware, configuration, motion TCP protocol, vision state machine, `carbot.sh` workflow, and troubleshooting.

## Quick layout

```
├── autonomous-nav/   # BLDC motor scripts
├── carbot_main/      # Dynamixel + actuator + motion TCP server
├── carbotUI/         # FastAPI web UI (optional)
├── docs/             # PDFs + PNG diagrams (datasheets, BLDC schematic)
├── vision/           # Window button detection + visual servo
├── requirements.txt
├── requirements-vision.txt
├── requirements-ui.txt
└── carbot.sh         # Launcher (env + startup motions + window_servo)
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
export PYTHONPATH=.
```

Terminal 1: `cd carbot_main && python3 motion_server.py`  
Terminal 2: `./carbot.sh` or `python3 -m vision.window_servo --preview` (see the complete guide).

For `VISION_*` and camera variables, see `vision/env.example`.
