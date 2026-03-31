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
├── assets/     # Button training images
├── config/     # Configuration files
├── docs/       # Hardware and architecture docs
└── src/        # Source code
    ├── bldc-motor/
    ├── vision/
    └── robotic-arm/
```

## Requirements

- Python 3.8+
- OpenCV, NumPy, PySerial
- Roboflow Inference SDK
