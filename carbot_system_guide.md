# Carbot System Guide
### Jetson Orin NX — Vision-Guided Robotic Arm

---

## 1. Hardware Overview

| Component | Details |
|---|---|
| **SBC** | NVIDIA Jetson Orin NX, Tanna TechBiz Eagle 201 carrier |
| **OS / JetPack** | Ubuntu 22.04 · JetPack 6 (L4T R36.5.0) |
| **CUDA** | CUDA 12.6, installed at `/usr/local/cuda-12.6/` |
| **Servos** | 7× Dynamixel MX-28 on `/dev/ttyUSB0` @ 222,222 baud |
| **Servo IDs** | 1–5 → absolute (arm joints); 6–7 → relative (pan/tilt camera head) |
| **Linear Actuator** | GPIO pin IN3=35, IN4=37 (Jetson.GPIO) |
| **Camera** | USB camera, default `/dev/video0` (index 0) |

---

## 2. Repository Structure

```
feature-carbot/
├── best.pt                  # Trained YOLO model weights
├── carbot.sh                # One-shot launcher script (interactive menu)
├── requirements-vision.txt  # Python deps for vision stack
├── vision/                  # Vision module (Python package)
│   ├── camera.py            # OpenCV camera capture
│   ├── config.py            # Env-driven config (VisionConfig)
│   ├── detector.py          # YOLO / Roboflow detector
│   ├── annotate.py          # Draw bounding boxes on frames
│   ├── window_servo.py      # Main vision-to-motor loop
│   ├── motion_client.py     # TCP client → motion_server
│   ├── mjpeg_server.py      # Browser preview server
│   ├── runner.py            # Standalone inference logger
│   ├── server.py            # TCP server for latest detections
│   ├── types.py             # ButtonDetection, BoundingBox types
│   └── env.example          # All environment variable reference
└── carbot_main/
    ├── motion_server.py     # TCP motor control server (port 5000)
    ├── carbot_record.py     # Servo recorder / motion editor
    ├── actuator.py          # Linear actuator GPIO control
    └── actions/             # Saved motion JSON files
```

---

## 3. Software Library Stack

### Vision Stack

| Library | Purpose | Install |
|---|---|---|
| `ultralytics` | Load & run `best.pt` YOLO model (object detection) | `pip install ultralytics` |
| `opencv-python` / `opencv-python-headless` | Camera capture, frame annotation, JPEG encoding | Included in `requirements-vision.txt` |
| `numpy` | Frame array manipulation | Included |
| `inference` | Roboflow embedded runtime (alternative to ultralytics) | Included in `requirements-vision.txt` |
| `inference-sdk` | Roboflow HTTP client (alternative runtime) | Included |
| `torch` (Jetson build) | PyTorch backend for YOLO inference | See Docker section below |
| `torchvision` | PyTorch image transforms | See Docker section below |

### Motor Control Stack

| Library | Purpose | Install |
|---|---|---|
| `pyserial` | RS-485 serial comms to Dynamixel bus over `/dev/ttyUSB0` | Comes with `carbot_record.py` deps |
| `Jetson.GPIO` | GPIO control for linear actuator | Pre-installed on Jetson |
| `socket` (stdlib) | TCP server (port 5000) for motion RPC | Built-in Python |
| `threading` (stdlib) | Concurrent servo + server handling | Built-in Python |

### Web UI Stack (`carbotUI/`)

| Library | Purpose |
|---|---|
| `FastAPI` + `uvicorn` | REST API + WebSocket server |
| HTML + JS | Browser-based remote control interface |

---

## 4. Why Docker? The GPU Problem

The standard `pip install torch` downloads a **generic x86/aarch64 PyTorch** that cannot communicate with the Jetson's GPU because it requires the NVIDIA JetPack-specific CUDA toolkit and drivers.

The `dustynv/l4t-pytorch` Docker image is pre-built by NVIDIA partner Dustin Franklin. It contains:
- **Jetson-native PyTorch** compiled against JetPack 6 / L4T R36
- **All CUDA shared libraries** pre-linked (`libcublas`, `libcupti`, `libcusparseLt`, etc.)
- **TensorRT** for model export and fast GPU inference
- **OpenCV with CUDA acceleration** compiled against Jetson's iGPU

> [!IMPORTANT]
> Run the vision inference (YOLO) **inside the Docker container** and the motor server (`motion_server.py`) **directly on the host**. They communicate over TCP on `127.0.0.1:5000`.

---

## 5. Docker: Initial Setup

### 5.1 Verify the image is available
```bash
sudo docker images
# Should show:  dustynv/l4t-pytorch:r36.2.0
```

### 5.2 First-time run — start an interactive container
```bash
sudo docker run \
  --runtime nvidia \
  --network host \
  --privileged \
  -it \
  --name carbot_vision \
  -v /home/jnx/Prajwal/feature-carbot:/workspace \
  -v /dev:/dev \
  dustynv/l4t-pytorch:r36.2.0 \
  bash
```

**Flag explanation:**

| Flag | Why |
|---|---|
| `--runtime nvidia` | Activates NVIDIA container runtime so GPU is visible inside |
| `--network host` | Container shares the host network; vision can reach `127.0.0.1:5000` |
| `--privileged` | Required for `/dev/video0` (camera) and `/dev/ttyUSB0` (servos) |
| `-v /home/jnx/Prajwal/feature-carbot:/workspace` | Mounts your code into the container at `/workspace` |
| `-v /dev:/dev` | Passes all device nodes (camera, USB serial) into the container |
| `--name carbot_vision` | Names the container so you can restart it without re-typing flags |

### 5.3 Inside the container — install Python deps once
```bash
# This only needs to be done once (changes persist in named container)
cd /workspace
pip3 install ultralytics --no-deps   # torch is already in the image
pip3 install -r requirements-vision.txt --no-deps
```

> [!TIP]
> Use `--no-deps` so pip does NOT try to replace the image's pre-built Jetson torch with a generic CPU-only version.

### 5.4 Verify GPU is active inside the container
```bash
python3 -c "import torch; print('GPU:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
# Expected: GPU: True
#           Orin (or Xavier NX)
```

---

## 6. Restarting the Container (Subsequent Sessions)

The container is **persistent** — all installed packages survive restarts.

```bash
# Start the stopped container
sudo docker start carbot_vision

# Attach to it (get a shell)
sudo docker exec -it carbot_vision bash

# Or start + attach in one command
sudo docker start -ai carbot_vision
```

---

## 7. Full Startup Sequence

Open **two terminals** on the Jetson. Keep them both running simultaneously.

---

### Terminal 1 — Motor Control Server (runs on HOST, not Docker)

```bash
cd /home/jnx/Prajwal/feature-carbot/carbot_main
python3 motion_server.py
```

**What it does:**
- Opens `/dev/ttyUSB0` at 222,222 baud and pings all 7 servos
- Starts a TCP server on `0.0.0.0:5000`
- Listens for JSON-line commands: `servo_move`, `freeze`, `neutral`, `play`, `actuator`, etc.
- Stays up continuously — all servo motion flows through here

**Expected startup log:**
```
Connected to Dynamixel bus.
LinearActuator initialised on pins 35/37.
Motion Server listening on 0.0.0.0:5000
```

---

### Terminal 2 — Vision + Tracking (runs INSIDE Docker)

```bash
# Step 1: attach to the container
sudo docker exec -it carbot_vision bash

# Step 2: go to the workspace
cd /workspace

# Step 3: run the vision loop
export VISION_RUNTIME=yolo
export VISION_MODEL_PATH=best.pt
export VISION_CONFIDENCE=0.5
export VISION_INFER_INTERVAL_SEC=0.2   # inference every 200ms
python3 -m vision.window_servo --preview
```

**What it does:**
1. Loads `best.pt` via Ultralytics YOLO on the Jetson GPU
2. Opens camera index 0 (`/dev/video0`)
3. Starts MJPEG preview server on `http://<jetson-ip>:8080/`
4. **SEARCH phase:** If no button detected, sweeps pan (servo 6) and tilt (servo 7) in a pattern
5. **TRACK phase:** When a button is detected, sends `servo_move` commands over TCP to Terminal 1 to center the bounding box in frame

**Expected startup log:**
```
Vision runtime=yolo (ultralytics local weights: best.pt)
MJPEG preview http://0.0.0.0:8080/ ...
window_servo motion=127.0.0.1:5000 dry_motion=False preview=True phase=SEARCH
freeze → {'status': 'frozen', 'servo_id': None}
```

---

### Shortcut: The Launcher Script

Instead of manually setting env vars, use `carbot.sh` which wraps everything:

```bash
# Interactive: pick the target button from a menu
./carbot.sh

# Direct: skip the menu and go straight to a button
./carbot.sh front_left_window
./carbot.sh front_right_window
./carbot.sh rear_left_window
./carbot.sh rear_right_window
./carbot.sh door_lock
./carbot.sh window_lock
```

> [!NOTE]
> `carbot.sh` only launches the **vision side**. You must still start `motion_server.py` in Terminal 1 separately first.

---

## 8. Environment Variable Reference

All variables can be set in the shell or put in a `.env` file sourced before starting.

### Vision / Inference
| Variable | Default | Description |
|---|---|---|
| `VISION_RUNTIME` | `embedded` | Set to `yolo` for local `.pt` file |
| `VISION_MODEL_PATH` | `best.pt` | Path to the YOLO weights file |
| `VISION_CONFIDENCE` | `0.4` | Minimum detection confidence (0.0–1.0) |
| `VISION_INFER_INTERVAL_SEC` | `0.5` | Seconds between inference calls (lower = faster but more CPU) |
| `VISION_LABEL_ALLOWLIST` | _(all)_ | Comma-separated class names to track, e.g. `front_left_window` |
| `VISION_MOCK` | `0` | Set `1` to disable inference entirely (wiring tests) |

### Camera
| Variable | Default | Description |
|---|---|---|
| `CARBOT_VISION_CAMERAS` | `0` | USB camera index |
| `CARBOT_VISION_GSTREAMER` | _(unset)_ | Full GStreamer pipeline string (overrides index for CSI cams) |

### Motor Comms
| Variable | Default | Description |
|---|---|---|
| `MOTION_HOST` | `127.0.0.1` | IP of the machine running `motion_server.py` |
| `MOTION_PORT` | `5000` | Port of `motion_server.py` |

### Pan/Tilt Servo Tuning
| Variable | Default | Description |
|---|---|---|
| `VISION_PAN_SERVO` | `6` | Servo ID for horizontal pan |
| `VISION_TILT_SERVO` | `7` | Servo ID for vertical tilt |
| `VISION_KP_X` | `0.35` | Proportional gain for horizontal error |
| `VISION_KP_Y` | `0.35` | Proportional gain for vertical error |
| `VISION_TRACK_SPEED` | `180` | Servo speed during tracking (0–1023) |
| `VISION_SEARCH_SPEED` | `200` | Servo speed during search |
| `VISION_SEARCH_STEP` | `90` | Step size (raw units) for search sweeps |
| `VISION_MAX_DELTA` | `140` | Max servo delta per tracking step |
| `VISION_DEADZONE_PX` | `28` | Pixel radius of center deadzone (no move inside) |
| `VISION_INVERT_PAN` | `0` | Set `1` if pan moves opposite to the error |
| `VISION_INVERT_TILT` | `0` | Set `1` if tilt moves opposite to the error |
| `VISION_LOST_FRAMES` | `10` | Frames without detection before returning to SEARCH |

### Preview Server
| Variable | Default | Description |
|---|---|---|
| `VISION_PREVIEW_HOST` | `0.0.0.0` | MJPEG server bind address |
| `VISION_PREVIEW_PORT` | `8080` | MJPEG server port |
| `VISION_PREVIEW_JPEG_QUALITY` | `75` | JPEG quality (1–95); lower = faster stream |

---

## 9. Optional: TensorRT Export for Maximum FPS

Once GPU is confirmed working inside Docker:

```bash
# Inside Docker container
cd /workspace
yolo export model=best.pt format=engine workspace=4
# Outputs: best.engine

# Then run with the engine file for 3-5× faster inference
export VISION_MODEL_PATH=best.engine
python3 -m vision.window_servo --preview
```

> [!TIP]
> TensorRT compilation takes ~5–10 minutes but only needs to be done once per model. The `.engine` file is saved next to `best.pt`.

---

## 10. Useful Diagnostic Commands

```bash
# Check if GPU is active (run inside Docker)
python3 -c "import torch; print('GPU:', torch.cuda.is_available())"

# Check USB serial port
ls -la /dev/ttyUSB*

# Check camera device
ls -la /dev/video*

# Test camera opens (run inside Docker)
python3 -c "import cv2; cap=cv2.VideoCapture(0); print('Camera OK:', cap.isOpened()); cap.release()"

# Check Docker container status
sudo docker ps -a

# View container logs
sudo docker logs carbot_vision

# Open a second shell in the running container
sudo docker exec -it carbot_vision bash
```
