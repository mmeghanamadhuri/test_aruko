# Sirena Control Center

Desktop app (PyQt5) that fronts the Nina robot on the Jetson's 10.1"
touchscreen. It uses a persistent left sidebar, a centred red header,
and a charcoal status bar so the experience feels like a polished
consumer cockpit.

## Information architecture

```
+-- Home        Quick-action dashboard with Nina photo, status strip
+-- Drive       Manual BLDC control: virtual D-pad, speed slider, brake
+-- Vision      USB camera feed + face / object recognition controls
+-- Map         SLAM occupancy grid, sensor health, auto-dock
+-- Actions     Existing record / play / audio - now in one screen
|     +-- Playback   list registered actions, smooth replay (+ optional audio)
|     +-- Record     release torque, capture frames, save into manifest
|     +-- Audio      gTTS author / tune / remove per-action audio clips
+-- Settings    Sub-sidebar: General / Network / Display / Audio / Privacy
|                Autodock / Voice (ESP) / Power / OTA Update
+-- Health      Donut + 13-row subsystem table, Run-all-checks
```

The first release ships fully working **Home**, **Actions**, **Drive**,
**Vision**, **Map** and **Health** flows.

- **Drive** is wired to the real JYQD_V7.3E2 BLDC drivers via
  `workers/drive_controller.py` (a Qt facade over
  `nina.controllers.navigation_manager.NavigationManager`); on dev hosts
  without `Jetson.GPIO` it gracefully falls back to a "Simulation" pill.
  An **Autonomous mode** toggle on the Drive screen mirrors the same
  toggle on the Map screen and disables the manual D-pad while autonomy
  is in charge of the wheels.
- **Vision** is wired to a USB camera and runs face detection
  (**YuNet** via `cv2.FaceDetectorYN`) and object detection
  (**Ultralytics YOLOv8n**, auto-exported to **TensorRT FP16** on
  Jetson so it runs on the GPU) through `workers/vision_pipeline.py` +
  `workers/vision_worker.py`. On hosts without OpenCV /
  Ultralytics / a camera the screen surfaces a clear "Vision
  unavailable" pill with the exact reason. Face _recognition_
  (identity matching) and person tracking are tagged for the next
  iteration.
- **Map** is wired to a real lidar-driven SLAM stack:
  - `nina/sensors/` ships drivers for the **RPLIDAR A1M8** (head),
    **HC-SR04** ultrasonic ring (chassis), **Sharp GP2Y0E02B** IR
    cliff sensor (front bumper), and **Intel RealSense D435** depth
    camera (front of chassis, ~10 deg downtilt).
  - `nina/slam/` wraps **BreezySLAM** (CoreSLAM port) into a thread-
    safe `SlamEngine` that publishes a 2D occupancy grid + pose.
  - `nina/navigation/autonomous_pilot.py` is a reactive sensor-fusion
    pilot: when the **Autonomous mode** toggle is on, the obstacle
    field combines lidar, ultrasonic, IR and depth into per-sector
    minimum distances and steers the BLDCs accordingly. The IR sensor
    is treated as a hard cliff alarm.
  - All four sensors degrade independently - if `pyrealsense2` /
    `rplidar` / `smbus2` is missing, or the device file isn't there,
    the relevant pill switches to "sim" and the pilot keeps running on
    whatever sensors are alive.

**Settings** and the non-Dynamixel rows on **Health** remain polished
UI scaffolds with in-process stubs so the firmware team can swap each
stub for a real driver without touching the UI.

## Action audio

Action JSON files live in `nina/actions/recordings/` (the `Record`
sub-tab writes there too). Manifest entries can be either a string
(`"recordings/namaste.json"`) or a dict:

```json
{
  "namaste": {
    "file": "recordings/namaste.json",
    "audio": "audio/namaste.mp3",
    "audio_offset": 2.0
  }
}
```

### From the GUI (Actions -> Audio)

Pick an action from the dropdown and:

- Type the words to speak (defaults to the action name).
- Pick a voice preset (US English by default, plus UK, Australian, Indian, Hindi, etc.).
- Set the **audio offset** (seconds the runtime waits after motion
  starts before firing the clip).
- **Preview** the existing clip, **Generate & Save** a new one,
  **Save offset** without re-generating, or **Remove** the audio.

The Playback sub-tab still shows the audio summary on each row and
exposes an **Audio** shortcut that jumps straight to the editor with
the right action pre-selected.

The MP3 is rendered with gTTS (needs internet on the Jetson the first
time you click *Generate*), saved to
`nina/actions/audio/<action>.mp3`, and the manifest is updated
atomically. Install gTTS with:

```bash
pip install --user gTTS
sudo apt install -y mpg123    # so the generated MP3 can be played
```

### From the CLI

```bash
# Generate the MP3 with gTTS and register it in the manifest
python3 scripts/generate-action-audio.py namaste

# Tune the offset later without re-generating audio
python3 scripts/generate-action-audio.py namaste --offset 2.5 --skip-tts
```

## Vision pipeline

The Vision screen drives a USB camera and runs two GPU-accelerated
detectors that the operator toggles independently:

- **Face detection** - YuNet (`cv2.FaceDetectorYN`). Ships with
  OpenCV >= 4.5.4. The 340 KB ONNX model is downloaded once to
  `nina/models/weights/face_detection_yunet_2023mar.onnx`.
- **Face recognition** - SFace (`cv2.FaceRecognizerSF`). Loaded
  alongside YuNet; the ~38 MB ONNX is cached at
  `nina/models/weights/face_recognition_sface_2021dec.onnx`. Click
  **Train a new face** in the Vision tab to enrol an embedding; the
  averaged 128-d feature lands in `nina/data/faces.json`. Recognised
  faces trigger an auto-greeting ("Hello <name>") via gTTS, with the
  per-name MP3 cached at `nina/data/greetings/<name>.mp3` and a 30 s
  cooldown per person so you don't get spammed.
- **Object detection** - Ultralytics YOLOv8n (COCO 80 classes). On
  Jetson the pipeline auto-exports a TensorRT FP16 engine on first
  run (`nina/models/weights/yolov8n.engine`); thereafter inference
  runs on the GPU. On dev hosts the same code falls back to PyTorch.

Useful env vars:

```bash
# /dev/video<N> the camera lives on (default: 0)
export NINA_VISION_CAMERA=0

# Disable the TensorRT path even on a Jetson (defaults to on)
export NINA_VISION_TRT=0

# Override the YOLO weights file (defaults to nina/models/weights/yolov8n.pt)
export NINA_VISION_YOLO_WEIGHTS=/path/to/your.pt
```

The first time **Object detection** is toggled on a Jetson the
TensorRT export takes ~2-3 minutes and the screen shows a "Loading
object detector..." pill so the user knows to wait. Subsequent toggles
load the cached engine in seconds.

Snapshots from the **Snapshot** button save to
`~/Pictures/nina-snapshots/`.

## Map / SLAM / autonomous nav

Hardware:

| Role         | Part           | Notes                                                |
| ------------ | -------------- | ---------------------------------------------------- |
| 360 lidar    | RPLIDAR A1M8   | Head-mounted; USB serial, default `/dev/ttyUSB0`     |
| Depth camera | RealSense D435 | Front of chassis, ~10 deg downtilt, USB 3            |
| IR cliff     | GP2Y0E02B      | Front bumper, downward; I2C bus 1, default 0x40      |
| Ultrasonics  | 4x HC-SR04     | Chassis ring; FL/FR/RL/RR. BCM pins are env-overridable |

The **Autonomous mode** toggle is mirrored on the **Map** screen and
the **Drive** screen. Turning it on:

1. Starts the SLAM worker (lidar + BreezySLAM) so the occupancy grid
   builds while autonomy runs.
2. Opens the HC-SR04 ring, the IR cliff sensor, and the D435.
3. Spawns the `AutonomousPilot` reactive controller (5 Hz default).

Pilot behaviour (V1 - "safe wander"):

- Forward when `forward >= NINA_AUTO_FWD_CLEAR_MM` (default 700 mm)
  AND both side margins exceed `NINA_AUTO_SIDE_CLEAR_MM` (350 mm).
- Otherwise commit a brief in-place turn toward the clearer side for
  `NINA_AUTO_TURN_MS` ms (default 350) and re-evaluate.
- If any sector drops below `NINA_AUTO_ESTOP_MM` (300 mm) **or** the
  IR sensor fires the cliff alarm, reverse for
  `NINA_AUTO_BACKOFF_MS` ms and re-pick a direction.

While autonomy is on, the Drive screen disables the D-pad / brake /
reverse / speed slider so the operator can't fight it on the wheels.
Toggle off to take back manual control - the wheels park on the way
out.

Useful env vars:

```bash
# RPLIDAR
export NINA_LIDAR_PORT=/dev/ttyUSB0
export NINA_LIDAR_BAUD=115200

# HC-SR04 ring (BCM pin numbers); set NINA_HCSR04_DISABLE=1 to skip
export NINA_HCSR04_FL_TRIG=19   NINA_HCSR04_FL_ECHO=9
export NINA_HCSR04_FR_TRIG=7    NINA_HCSR04_FR_ECHO=8
export NINA_HCSR04_RL_TRIG=11   NINA_HCSR04_RL_ECHO=4
export NINA_HCSR04_RR_TRIG=6    NINA_HCSR04_RR_ECHO=26

# IR (i2c bus / address)
export NINA_IR_I2C_BUS=1
export NINA_IR_I2C_ADDR=0x40

# Depth camera; NINA_DEPTH_DISABLE=1 to skip the D435
export NINA_DEPTH_FPS=15

# Pilot tuning
export NINA_AUTO_TICK_HZ=5
export NINA_AUTO_CRUISE_PCT=18
export NINA_AUTO_FWD_CLEAR_MM=700
export NINA_AUTO_ESTOP_MM=300
export NINA_AUTO_CLIFF_MIN_MM=60

# SLAM map sizing
export NINA_SLAM_PIXELS=800
export NINA_SLAM_METERS=20
```

The Map screen also exposes **Start mapping** (SLAM only, no
autonomy), **Save map** (PGM dump of the current grid) and **Clear**
(reset the grid + replay live scans into a fresh map).

## Install dependencies on Jetson Nano

```bash
sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg
# Or, inside a venv:
pip install -r sirena_ui/requirements.txt
```

## One-time permissions (no `sudo` at runtime)

The app deliberately avoids `sudo` - running a Qt GUI as root breaks
your X11/Wayland session, leaves root-owned files in your home, and
defeats the security model. Instead, do these once:

```bash
# Serial port (Dynamixel bus on /dev/ttyUSB0)
sudo usermod -aG dialout $USER

# Optional: drops FTDI latency_timer to 1ms so reads are reliable
sudo bash scripts/install-ftdi-udev.sh

# Make sure the repo is owned by your user (not root from a sudo-clone)
sudo chown -R $USER:$USER ~/Nvidia-jetson-platform
```

Then **log out and log back in** (a reboot is the simplest test) so
the new `dialout` group membership applies to your desktop session.
Verify with:

```bash
groups | grep dialout
ls -l /dev/ttyUSB0       # should show "crw-rw---- root dialout"
```

If recording or playback ever shows "Permission denied", the
in-app error message now tells you exactly which fix to apply.

## Add the icon to the Jetson home screen

```bash
./scripts/install-sirena-desktop.sh
```

This drops a `Sirena.desktop` launcher into both the application menu
and the user's Desktop folder, pointing the Exec line at this repo and
this venv. Re-run the script after moving the repo.

After running the installer, double-click the **Sirena** icon on the
Desktop (or pick *Sirena* from the application menu) and the GUI
launches with the same environment as `python3 -m sirena_ui`. The
launcher (`scripts/launch-sirena.sh`) sources `~/.profile` /
`~/.bashrc`, adds the standard Jetson CUDA / cuDNN / TensorRT lib
directories to `LD_LIBRARY_PATH`, forces `QT_QPA_PLATFORM=xcb`, and
ensures the repo root is on `PYTHONPATH`. Anything it prints is
appended to `~/.cache/sirena/launch.log`, and a fatal error pops a
zenity / notify-send dialog so you don't get a silent dead icon.

> **First time GNOME shows "Untrusted application launcher"?** That's
> the file manager being cautious. Right-click the icon and pick
> **Allow Launching** (older Ubuntu) or just run the installer again
> -- it `chmod +x`'s the file and sets the `metadata::trusted` flag,
> which is what GNOME / Nautilus look for.

## Launch from a terminal

```bash
PYTHONPATH=. python3 -m sirena_ui
```

## Threading model

- `NinaService` owns the single `DynamixelManager` and exposes a
  `bus_lock` (`threading.RLock`).
- `PlaybackWorker` and `RecordWorker` are `QThread`s that acquire the
  bus lock for their duration, so they can never race on the serial
  port.
- The UI never touches the bus directly; it only signals workers to
  start/stop and reads progress over Qt signals.
- Lazy screen construction: each screen is built the first time a
  user navigates to it, so launch is fast.

## File layout

```
sirena_ui/
  __main__.py             # entry point
  main_window.py          # red header / charcoal sidebar / charcoal footer
  styles.py               # v2 theme tokens + Qt stylesheet
  assets/                 # logo, Nina photo, app icon
  screens/
    home_screen.py        # dashboard
    actions_screen.py     # Playback / Record / Audio sub-tabs
    drive_screen.py       # BLDC manual control (live)
    vision_screen.py      # USB camera + face/object recognition (live)
    map_screen.py         # SLAM (stub)
    settings_screen.py    # sub-sidebar with 9 categories
    health_screen.py      # donut + 13-row subsystem table
  widgets/
    sidebar.py            # persistent dark nav
    header_bar.py         # red top bar with clock / wifi / battery
    status_bar.py         # charcoal footer with status dots
    common.py             # Card, CardTitle, Pill, Breadcrumb, ...
    nina_image_panel.py   # left rail of the Actions screen
    playback_panel.py
    record_panel.py
    audio_panel.py        # action picker + audio editor
    audio_editor_dialog.py# voice presets shared with the panel
    dpad.py               # virtual D-pad on the Drive screen
    donut_gauge.py        # health donut with Nina photo in the hole
  workers/
    nina_service.py       # DynamixelManager + ActionRunner facade
    playback_worker.py
    record_worker.py
    audio_gen_worker.py   # gTTS rendering off the UI thread
    drive_controller.py   # Qt facade over NavigationManager (BLDC)
    vision_pipeline.py    # camera + YuNet face + YOLOv8 object pipeline
    vision_worker.py      # Qt facade running the vision pipeline on a thread
    vision_types.py       # Detection / VisionStatus dataclasses
    slam_worker.py        # Qt facade: lidar + BreezySLAM
    autonomy_controller.py# Qt facade: HC-SR04 + IR + D435 + AutonomousPilot
    health_collector.py   # subsystem statuses for the Health screen
    error_hints.py        # turn raw errors into actionable Jetson tips
```

The non-UI side of the SLAM / autonomy stack lives under `nina/`:

```
nina/
  sensors/
    types.py              # LidarScan / UltrasonicReading / IRReading / DepthFrame
    slamtec_s2e.py        # SLAMTEC RPLIDAR S2E Ethernet/UDP driver (default)
    rplidar_a1.py         # SLAMTEC RPLIDAR A1M8 USB-serial driver (legacy)
    lidar_factory.py      # Picks the right lidar driver from NINA_LIDAR_MODEL
    hcsr04.py             # HC-SR04 ultrasonic ring (BCM GPIO)
    gp2y0e02b.py          # Sharp GP2Y0E02B IR cliff sensor (I2C)
    realsense_d435.py     # Intel RealSense D435 depth camera
  slam/
    engine.py             # BreezySLAM RMHC_SLAM wrapper + occupancy grid
  navigation/
    obstacle_field.py     # Multi-sensor fusion -> per-sector min distances
    autonomous_pilot.py   # Reactive 'safe wander' pilot driving the BLDCs
```
