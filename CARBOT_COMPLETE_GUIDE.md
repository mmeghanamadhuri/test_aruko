# Carbot Sweep — Complete Guide

This is the **single reference** for the Carbot robotics stack: dependencies, layout, hardware, configuration, runtime behavior, TCP protocol, and the full operator workflow from a cold start through the end of a vision cycle.

---

## 1. What the system does

The robot uses a **gripper-mounted camera** to find labeled car-window controls (a trained detector), then:

1. **SEARCH** — Pan/tilt the camera head (Dynamixel IDs **6** and **7**, relative mode) until a target class appears.
2. **ALIGN** — Center the detection in the image using proportional control on pan/tilt (and optionally wrist/head stabilization), with stability checks before advancing.
3. **APPROACH** — Move the arm joints in coordinated steps (`multi_servo_move`) until the target fills enough of the frame (area fraction) or other stop logic fires.
4. **Post-alignment** (optional) — Lateral/vertical offsets, linear actuator extend/retract, optional `press.json` motion, then exit or loop per `carbot.sh`.

The **vision process** (`vision.window_servo`) talks to the **motion server** over **TCP JSON** (default port **5000**). The motion server owns the **Dynamixel Module U2D2** and optional **linear actuator GPIO**.

---

## 2. Repository layout

| Path | Role |
|------|------|
| `carbot_main/` | `motion_server.py` — TCP control; `carbot_record.py` — bus + playback engine; `actuator.py` — L298N linear actuator; `actions/*.json` — saved motions |
| `vision/` | Detection, camera, MJPEG preview, `window_servo.py` state machine, `motion_client.py` |
| `carbot.sh` | Launcher: exports tuning env vars, runs startup motions, then `python3 -m vision.window_servo` |
| `carbot_main/play_startup_sequence.py` | Plays startup JSON clips via RPC with retries |
| `carbotUI/` | Optional FastAPI remote UI (`requirements-ui.txt`) |
| `autonomous-nav/` | Separate BLDC / navigation experiments (not required for window servo) |
| `requirements.txt` | Full stack: vision + `ultralytics` + `carbot_main` deps |
| `requirements-vision.txt` | Slimmer vision stack (Roboflow `inference`, etc.) |
| `vision/env.example` | Copy/paste reference for all `VISION_*` and camera variables |

Weights: **`best.pt`** (YOLO) at repo root when using `VISION_RUNTIME=yolo` / `carbot.sh` defaults.

---

## 3. Hardware at a glance

| Component | Typical setup |
|-----------|----------------|
| SBC | NVIDIA Jetson Orin NX on **Tanna TechBiz Eagle 201** (or similar) carrier |
| Servos | **7× Dynamixel MX-28**, IDs **1–7**, RS-485 (often `/dev/ttyUSB0`, high baud — see `carbot_main` code / your flash) |
| IDs **1–5** | Arm joints — **absolute** position commands |
| IDs **6–7** | Camera pan/tilt — **relative** deltas from present position |
| Linear actuator | **L298N**: Jetson header pins **35** (IN3), **37** (IN4), **39** (GND); logic in `actuator.py` (`Jetson.GPIO` on Jetson images) |
| Camera | USB (`CARBOT_VISION_CAMERAS`) or GStreamer pipeline (`CARBOT_VISION_GSTREAMER`) |

**Anatomy note (reach direction):** From neutral vs extended poses, **shoulder (2)** often moves **negative** to reach forward while **elbow (3)** moves **positive**. Tune `VISION_APPROACH_SERVOS` / `VISION_APPROACH_DELTAS` in env or `carbot.sh` to match your rig.

---

## 4. Python environment and imports

### 4.1 Create the virtual environment

```bash
cd /path/to/carbot_sweep
sudo apt install -y python3-venv   # if needed
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
export PYTHONPATH=.
```

### 4.2 What gets installed (high level)

- **Vision:** OpenCV, NumPy, Roboflow `inference` / SDK (see `requirements-vision.txt`), optional **`ultralytics`** for local `.pt` when `VISION_RUNTIME=yolo`.
- **Motion:** `pyserial`, `rich`.
- **Jetson GPU:** Generic `pip install torch` may not match JetPack. Install a **Jetson-built PyTorch** wheel for your JetPack first if `ultralytics` fails, or use **`VISION_RUNTIME=embedded`** with Roboflow GPU install per their docs.

### 4.3 Import paths

Run modules **from the repo root** with `PYTHONPATH=.` so `import vision.*` resolves. Examples:

```bash
export PYTHONPATH=.
python3 -m vision.runner
python3 -m vision.window_servo --preview --dry-motion
```

`carbot_main` is typically run as a script path, not always as a package:

```bash
cd carbot_main && python3 motion_server.py
```

---

## 5. Configuration

### 5.1 Layering

1. **Shell exports** — Highest convenience; `carbot.sh` sets many `VISION_*` variables explicitly.
2. **`vision/env.example`** — Documented defaults; copy values into your shell or systemd unit.
3. **CLI flags** — `window_servo` accepts `--motion-host`, `--motion-port`, `--preview`, `--dry-motion`, etc.

### 5.2 Motion connection

| Variable | Default | Meaning |
|----------|---------|---------|
| `MOTION_HOST` | `127.0.0.1` | Host running `motion_server.py` |
| `MOTION_PORT` | `5000` | TCP port |

**Docker:** If vision runs in a container with **bridge** networking, `127.0.0.1` is the container, not the Jetson host. Use **`--network host`**, or set `MOTION_HOST` to the host gateway IP, or `host.docker.internal` where supported. `carbot.sh` comments describe this.

### 5.3 Vision runtime

| `VISION_RUNTIME` | Behavior |
|------------------|----------|
| `embedded` | In-process Roboflow `inference` (default in `env.example`) |
| `http` | Client to local or cloud inference server (`ROBOFLOW_API_URL`) |
| `yolo` | Local Ultralytics weights — **`VISION_MODEL_PATH`** (e.g. `best.pt`) |

`carbot.sh` forces `VISION_RUNTIME=yolo` and passes **`VISION_MODEL_PATH`** (default `best.pt`).

### 5.4 Key tuning variables (non-exhaustive)

See `vision/config.py` (`VisionConfig.from_env`) and `vision/env.example` for the full set. Commonly adjusted:

| Area | Examples |
|------|----------|
| Detector | `VISION_CONFIDENCE`, `VISION_INFER_INTERVAL_SEC`, `VISION_LABEL_ALLOWLIST` |
| Pan/tilt | `VISION_PAN_SERVO`, `VISION_TILT_SERVO`, `VISION_KP_*`, `VISION_TRACK_SPEED`, `VISION_SEARCH_SPEED`, `VISION_MAX_DELTA`, `VISION_DEADZONE_PX`, `VISION_INVERT_PAN`, `VISION_INVERT_TILT` |
| Phases | `VISION_ALIGN_STABLE_FRAMES`, `VISION_REALIGN_PX`, `VISION_APPROACH_*`, search sweep `VISION_SEARCH_SWEEP_SEC`, `VISION_SEARCH_BILATERAL` |
| Post-approach | `VISION_OFFSET_AFTER_APPROACH`, `VISION_OFFSET_V_ACTUATOR`, `VISION_CAMERA_PRESS_OFFSET_*`, `VISION_PRESS_JSON`, `VISION_POST_CYCLE_BACK_JSON` |

**`carbot.sh` top section** mirrors these as bash variables (`KP_X`, `APPROACH_SERVOS`, etc.) and passes them through to the Python process.

---

## 6. End-to-end workflow

### 6.1 One-time / occasional prep

1. Flash Jetson, install drivers, verify **`/dev/ttyUSB*`** (Dynamixel) and **`/dev/video*`** (camera).
2. Create venv, `pip install -r requirements.txt`, set `PYTHONPATH=.`.
3. Place `best.pt` (or configure Roboflow model ID + API key for embedded/http).
4. (Optional) Static Ethernet from a laptop: Jetson e.g. `192.168.99.1`, laptop `192.168.99.2`, subnet `255.255.255.0`; motion server still on port `5000`.

### 6.2 Every session — two logical roles

**A. Motion server (host Jetson, recommended)**

```bash
cd /path/to/carbot_sweep/carbot_main
python3 motion_server.py
```

Expect: serial open, actuator init (if GPIO available), `listening on ...:5000`.

**B. Vision + visual servo**

Either:

```bash
cd /path/to/carbot_sweep
./carbot.sh                    # interactive menu
./carbot.sh front_left_window  # direct target
```

or manually:

```bash
export PYTHONPATH=.
export MOTION_HOST=127.0.0.1
export MOTION_PORT=5000
export VISION_RUNTIME=yolo
export VISION_MODEL_PATH=best.pt
export VISION_LABEL_ALLOWLIST=front_left_window
python3 -m vision.window_servo --preview
```

### 6.3 What `carbot.sh` does (interactive mode)

1. Prints banner.
2. **`play_startup_sequence`** — Runs `carbot_main/play_startup_sequence.py` to play `STARTUP_JSON_FILES` (default `actions/short.json`) via RPC, with waits/retries.
3. **`pick_button`** — Menu of `BUTTONS` names (must match detector class labels).
4. **`launch`** — Sets env (Kp, speeds, approach vector, offsets, etc.) and runs `python3 -u -m vision.window_servo` with optional `--preview`.
5. When vision exits: sleep `POST_CYCLE_DELAY_SEC`, then **`pick_button`** again (startup short is not replayed every loop — see script comments).
6. **`neutral` argument** — Plays `actions/revert_short.json` only.

### 6.4 MJPEG preview

With `--preview` / `PREVIEW=true`, a thread serves JPEG frames (default **`http://<jetson-ip>:8080/`**). **Important:** The first YOLO inference **warms up** the model on the main thread **before** the MJPEG server starts, avoiding a known race between OpenCV JPEG encode and PyTorch lazy init that could abort the process (`corrupted size vs. prev_size`). Expect several seconds before the stream URL is logged.

### 6.5 Dry run (no robot motion)

```bash
python3 -m vision.window_servo --dry-motion --preview
```

Uses camera + detector + UI; does not command servos (still needs motion server only if code paths require RPC — verify logs for your version).

---

## 7. Vision package — modules and roles

| Module | Role |
|--------|------|
| `vision/config.py` | `VisionConfig.from_env()` — central env parsing |
| `vision/detector.py` | Wraps YOLO / Roboflow inference → `ButtonDetection` |
| `vision/camera.py` | Frame acquisition (USB or GStreamer) |
| `vision/types.py` | Dataclasses for detections / boxes |
| `vision/motion_client.py` | TCP line-delimited JSON RPC client |
| `vision/mjpeg_server.py` | Browser MJPEG stream |
| `vision/window_servo.py` | **Main loop:** state machine, phases, calls into detector + motion |
| `vision/runner.py` | Log detections only (no servo) |
| `vision/server.py` | TCP server exposing latest detection JSON (port `VISION_SERVER_PORT`, default 5001) |

---

## 8. Behavior of `window_servo` (conceptual)

### 8.1 State machine (high level)

- **SEARCH** — Hunt pattern on pan/tilt (and related params); may use bilateral sweep, dwell times, or “heavy scanner” style sweeps depending on version — tune `VISION_SEARCH_*`.
- **ALIGN** — Minimize pixel error between detection center and image center (with optional EMA smoothing, adaptive Kp bands). Requires consecutive stable frames before approach.
- **APPROACH** — Repeated `multi_servo_move` with configured servo/delta vector; pan/tilt may **freeze** during lunge and **re-align** if error exceeds `VISION_REALIGN_PX`. Stops when bbox area fraction ≥ `VISION_APPROACH_AREA_FRAC` (or equivalent config).
- **Post phases** — Optional horizontal offset (joint deltas), vertical actuator timing, pre-actuator tilt, `VISION_PRESS_JSON`, `VISION_POST_CYCLE_BACK_JSON`, revert clips — see `window_servo.py` and `VisionConfig` for exact ordering.

### 8.2 Motion server performance note

A **background feedback thread** refreshes servo positions so RPC handlers can return quickly with cached positions instead of blocking on every serial read. Fast approach steps use **`multi_servo_move`** to avoid playback-thread latency for small incremental moves.

---

## 9. Motion server — TCP protocol

- **Transport:** TCP, one JSON object per line (newline-terminated).
- **Motions directory:** JSON paths are resolved under `MOTIONS_DIR` (typically `carbot_main/actions/` when started from that directory).

### 9.1 Commands (summary)

| `cmd` | Purpose |
|-------|---------|
| `play` | `{"cmd":"play","file":"actions/foo.json","loop":false}` |
| `stop` | `{"cmd":"stop","mode":"soft\|hard"}` |
| `status` | Cached positions + `is_playing` |
| `neutral` | Torque off (optional `servo_id`) |
| `freeze` | Torque on (optional `servo_id`) |
| `torque` | `enable` true/false, optional `servo_id` |
| `servo_move` | `servo_id`, `value`, `speed`, optional `mode` (`abs`/`rel`) |
| `multi_servo_move` | `servos`: `{id: value, ...}`, `speed`, optional `mode` |
| `play_frame` | Single normalized frame dict |
| `actuator` | `action`: `extend` / `retract` / `stop`; optional `distance_mm`, `duration` |
| `record` | Append snapshot frame to a JSON file |
| `list_files` / `get_file` / `save_file` | Manage JSON under motions dir |

### 9.2 Quick manual test

```bash
nc 127.0.0.1 5000
```

Then type (and press Enter):

```json
{"cmd":"status"}
```

---

## 10. Motion JSON schema (playback)

Actions are JSON arrays of **frames**. Each frame may include:

- `delay` — Seconds before this frame’s motion starts.
- `duration` — Hold time after the frame completes.
- `speed` — Dynamixel profile speed (clamped in engine; 0 = fast per firmware semantics).
- `servos` — Map of servo ID → either **absolute** `{ "type": "absolute", "value": <raw> }` or **relative** `{ "type": "relative", "diff": ..., "sign": "+|-", "ref_pos": ... }` for head servos.
- `actuator` — Optional `{ "action": "extend"|"retract", "distance_mm": <number> }`.

The interactive **`carbot_record.py`** menu can record and edit these files.

---

## 11. Optional Docker workflow (vision on GPU)

Standard desktop PyTorch wheels may not use Jetson GPU. **`dustynv/l4t-pytorch`** images bundle Jetson CUDA builds. Typical pattern:

- Run **`motion_server.py` on the host** (GPIO + serial).
- Run **vision inside the container** with `--network host` or correct `MOTION_HOST`, mounting the repo and `/dev`.

Inside the container, when installing extra pip packages, use **`pip install ... --no-deps`** where needed so you do not overwrite the image’s `torch`.

---

## 12. Optional components

- **`carbotUI/`** — Install `requirements-ui.txt`, run per that package’s entry/README for browser control.
- **`autonomous-nav/`** — BLDC hub motor / JYQD driver notes were consolidated here only at a high level; scripts live under that tree for wheel experiments.

---

## 13. Troubleshooting

| Symptom | Things to check |
|---------|------------------|
| Vision cannot move arm | `motion_server` running? `MOTION_HOST`/`MOTION_PORT`? Firewall? |
| Immediate crash with `--preview` | Ensure detector warm-up completes before MJPEG (current `window_servo` ordering); update code if regressing. |
| Oscillating pan/tilt | Lower `VISION_KP_*`, raise `VISION_DEADZONE_PX`, lower `VISION_MAX_DELTA`. |
| No detections | `VISION_CONFIDENCE`, lighting, model classes vs `VISION_LABEL_ALLOWLIST`, camera index. |
| `servo_move failed` | USB power, baud, servo IDs, cable; serial contention. |
| Actuator errors | Run on real Jetson with `Jetson.GPIO`; check L298N wiring and shared ground. |

---

## 14. Quick command reference

```bash
# Motion (terminal 1)
cd carbot_main && python3 motion_server.py

# Vision logger
export PYTHONPATH=.
python3 -m vision.runner

# Vision TCP feed of latest detection
python3 -m vision.server

# Full visual servo + browser preview
python3 -m vision.window_servo --preview

# Launcher (startup motions + menu + env)
./carbot.sh
```

---

## 15. Reference files under `docs/` (PDFs and diagrams)

Markdown handbooks were merged into this guide. **Binary references** remain on disk:

| Location | Contents |
|----------|----------|
| `docs/01-hardware/` | Carrier / module PDFs (Eagle 201, Orin NX datasheet, U-Bot circuit) |
| `docs/02-architecture/` | Orin NX architecture / boot / memory diagrams (PNG) |
| `docs/03-bldc-motor/` | BLDC + Jetson schematic (PDF) |

Use these alongside NVIDIA Jetson and JetPack documentation for bring-up and wiring.

---

*This guide replaces scattered Markdown files previously under the repo root, `docs/**/*.md`, and `carbot_main/docs/` so operators and developers have one place to read.*
