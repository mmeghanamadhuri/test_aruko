# Jetson Orin NX Robotics

**Work in progress** - Robotics project on NVIDIA Jetson Orin NX platform.

## Overview

Exploration of vision, motor control, and robotic arm integration:
- Vision: Button detection with dual cameras
- Robotic Arm: 5-DOF arm with MX-28 servos
- Motor Control: BLDC motor control

## Hardware

- NVIDIA Jetson Orin NX with Tanna TechBiz Eagle 201 carrier
- Dual USB cameras
- 5x MX-28 servos
- BLDC motor

## Structure

```
├── autonomous-nav/   # BLDC motor scripts
├── carbot_main/      # Dynamixel + actuator + motion TCP server
├── carbotUI/         # FastAPI web UI (remote control)
├── docs/             # Hardware and architecture docs
├── vision/           # Car window button detection (OpenCV + Roboflow Inference)
└── requirements-vision.txt
```

## Requirements

- Python 3.8+
- OpenCV, NumPy, PySerial
- Roboflow Inference SDK

## Vision (window buttons)

Inference runs **locally** by default (`VISION_RUNTIME=embedded` → Roboflow `inference` package loads the model in-process on the Jetson/GPU). Alternatively set `VISION_RUNTIME=http` and run a local Roboflow Inference Server (`inference server start`, usually `http://127.0.0.1:9001`). Roboflow Cloud is opt-in via `ROBOFLOW_API_URL=https://serverless.roboflow.com`.

**Jetson Nano** — Use a small input size / lightweight model; prefer Roboflow TensorRT or ONNX export if full PyTorch is too slow. USB camera: set `CARBOT_VISION_CAMERAS`. CSI camera: set `CARBOT_VISION_GSTREAMER` (see `vision/env.example`). Prefer `sudo apt install python3-opencv` on Nano when possible.

**Gripper camera — search then track** — With `motion_server.py` running on the robot, `vision.window_servo` nudges pan/tilt (default servos 6–7, relative moves) until a button is detected, then centers the detection using pixel error → `servo_move`. Live view: `--preview` serves MJPEG at `http://<jetson-ip>:8080/`.

```bash
cd Nvidia-jetson-platform
pip install -r requirements-vision.txt
# On Jetson with GPU, prefer Roboflow’s inference-gpu install for your JetPack/CUDA.
export ROBOFLOW_MODEL_ID=workspace/project/version
export ROBOFLOW_API_KEY=...   # required for private models / first-time weight fetch
export PYTHONPATH=.
python -m vision.runner              # log detections
python -m vision.server              # TCP :5001, send {"cmd":"latest"}
python -m vision.window_servo --preview   # search + track + browser preview
python -m vision.window_servo --dry-motion --preview   # tune camera/model only
```

See `vision/env.example` for `VISION_RUNTIME`, GStreamer, visual servo gains, and cloud overrides.
