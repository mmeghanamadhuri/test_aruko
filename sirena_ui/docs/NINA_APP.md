# Nina app — feature reference

PyQt5 desktop cockpit (`sirena_ui/`) for the Nina robot, designed for the Jetson's 10.1" touchscreen. Persistent left charcoal sidebar, Sirena red header (clock / Wi‑Fi / battery), charcoal status footer.

> Every screenshot in this document is captured from the **real running app**
> (`PYTHONPATH=. python3 -m sirena_ui` under offscreen Qt) — no design
> mockups. Hardware that isn't present on the host shows up as
> "sim" / "Not connected" pills, which is the honest state when the
> docs are read on a non‑Jetson machine. On a Jetson Nano with the
> sensors wired in, those pills go green.

## Information architecture

```
+-- Home        Quick-action dashboard with Nina photo, status strip
+-- Drive       Manual BLDC control: virtual D-pad, speed slider, brake
+-- Vision      USB camera feed + face / object recognition controls
+-- Map         SLAM occupancy grid, sensor health, autonomous nav
+-- Actions     Existing record / play / audio - now in one screen
|     +-- Playback   list registered actions, smooth replay (+ optional audio)
|     +-- Record     release torque, capture frames, save into manifest
|     +-- Audio      gTTS author / tune / remove per-action audio clips
+-- Settings    Sub-sidebar: General / Network / Display / Audio / Privacy
|                Autodock / Voice (ESP) / Power / OTA Update
+-- Health      Donut + 13-row subsystem table, Run-all-checks
```

Cross-cutting design rules:

- **Single shared `NinaService`** owns the Dynamixel bus, drive, vision,
  SLAM and autonomy as **lazy singletons** with a deterministic
  shutdown order (autonomy → SLAM → vision → drive → DXL).
- All long-running work happens on `QThread`s / background queues —
  the UI thread never blocks on hardware.
- **Lazy screen construction**: each screen is built the first time
  the user navigates to it, so launch is instant.
- **Graceful degradation**: every hardware-touching screen surfaces a
  clear "sim" / "unavailable" pill if a driver, library or device is
  missing instead of crashing the app.
- **Explainable errors**: `workers/error_hints.py` rewrites raw OS
  errors (Permission denied, missing FTDI, no `dialout` group, …)
  into actionable Jetson-specific tips.

---

## Home — dashboard

![Home screen](screens/screen-home.png)

- Hero card with the real Nina photo and current state pills
  (`Idle`, `Torque ON`, `Voice ready`).
- Two prominent CTAs — **Play actions** and **Record new** — that
  jump straight into the Actions screen.
- 8-tile **Quick actions** grid (Play action, Record, Audio, Drive,
  Vision, Map, Health, Settings) for one-tap navigation.
- **System overview** strip with at-a-glance pills for Bus, Camera,
  Lidar, Battery and Wi-Fi; tapping the title opens Health.

---

## Drive — manual BLDC control + autonomy hand-off

![Drive screen](screens/screen-drive.png)

- **Front camera** preview pane (live USB feed when connected, helpful
  empty state when not).
- **Manual D-pad** with a big **STOP** in the centre and a **Brake** /
  **Reverse** state row underneath. Hold-to-drive, release-to-stop.
  Forward, Back, Left and Right are all held-while-pressed (release the
  button and the wheels coast to a stop), so steering during a left or
  right turn matches forward / back behaviour exactly.
- **Speed slider** (0–100 %) with `−` / `+` increment buttons. Sliding
  while a direction button is held now pushes the new duty straight to
  the motors — the wheels speed up / slow down live, no need to
  release-and-re-press to apply the change.
- **EMERGENCY STOP** — big red panic button below the toggle row.
  Drains pending drive commands, sets PWM duty to 0, engages the brake
  (EL low on both drivers) and lights all three status-LED channels.
  Independent of the regular Brake toggle so it works even mid-drive
  without first releasing the D-pad.
- Live **telemetry strip**: Speed, Heading, Distance, Battery.
- **Autonomous mode** toggle (mirrored on Map). When ON, the D-pad,
  brake, reverse and slider are disabled so the operator can't fight
  the autonomous pilot on the wheels.
- Top status pills surface the live driver state — `Autonomous: OFF`
  plus, on a non-Jetson host, an honest `Simulation — Jetson.GPIO is
  required on Jetson Nano. Install with: pip install Jetson.GPIO`.

Wiring: `workers/drive_controller.py` is a Qt facade over a
navigation manager. Two backends are supported, picked by
`NINA_NAV_MODE` and instantiated by
`nina.controllers.navigation_factory.build_navigation_manager`:

- **`NINA_NAV_MODE=local` (default).**
  `nina.controllers.navigation_manager.NavigationManager` drives the
  JYQDs directly from the Jetson Orin Nano's GPIOs. Pin map and quirks
  are documented immediately below.
- **`NINA_NAV_MODE=remote`.**
  `nina.controllers.remote_navigation_manager.RemoteNavigationManager`
  sends ASCII commands over a serial port to a Raspberry Pi running
  `pi_motor_bridge/motor_bridge.py`. The Pi owns the JYQDs. See
  [Remote mode (Pi motor bridge)](#remote-mode-pi-motor-bridge) below
  and `pi_motor_bridge/README.md` for the full setup.

Either way the GUI flow is identical: `drive_continuous(...)` for
D-pad presses (non-blocking, no auto-stop) and `set_wheels(...)` for
live speed updates and autonomous-pilot commands. The CLI tools still
use the timed `turn_left()` / `turn_right()` for scripted turns of a
fixed duration.

The local-mode driver is a clean port of the Sirena Raspberry Pi
reference build onto the Jetson Orin Nano. Pin map (mostly mirrors the
RPi; three pads are remapped because the Orin Nano image / carrier
doesn't expose them as plain GPIO — see notes below):

| Function    | BCM | Physical pin | Notes                  |
| ----------- | --- | ------------ | ---------------------- |
| L-EL        | 24  | 18           | digital out (see note) |
| L-DIR (Z/F) |  6  | 31           | digital out (see note) |
| L-PWM (VR)  | 12  | 32           | hardware PWM0          |
| R-EL        | 10  | 19           | digital out            |
| R-DIR (Z/F) | 23  | 16           | digital out (see note) |
| R-PWM (VR)  | 13  | 33           | hardware PWM2          |

> **R-DIR note.** The RPi reference puts R-DIR on BCM 22 / pin 15, but
> pin 15 is dead as a GPIO output on the Orin Nano carrier this bot
> uses (probed at constant 1.5 V regardless of what the kernel writes).
> BCM 23 / pin 16 is the workaround.
>
> **L-EL note.** The RPi reference puts L-EL on BCM 18 / pin 12, but
> pin 12 (PCM_CLK / I2S2_SCLK in the SoC pin table) is partially
> claimed by the Orin Nano audio device tree by default — GPIO writes
> get overridden, and the pad sits at a non-logic ~2.4 V ↔ ~4 V swing
> that the JYQD reads as "kind of HIGH most of the time." The visible
> symptom is a left wheel that occasionally spins, often jerks, and
> dies at higher PWM duty. BCM 24 / pin 18 is the workaround.
>
> **L-DIR note.** The RPi reference puts L-DIR on BCM 25 / pin 22, but
> pin 22 sits at a degraded ~0 V ↔ ~1.5 V intermittent toggle on this
> carrier (same alt-function-claim class as L-EL above). The JYQD
> reads 1.5 V as ambiguous and locks the left wheel's direction to
> whichever side of its threshold it last saw — the wheel can never
> reverse. BCM 6 / pin 31 is the workaround. Note BCM 6 collides with
> the default HC-SR04 rear-right TRIG channel; if you wire that
> ultrasonic sensor, override either pin via env var.

Both PWM pins must be enabled once via
`sudo /opt/nvidia/jetson-io/jetson-io.py` (Configure 40-pin Header →
manual → enable `pwm0` and `pwm2` → save → reboot).

Notable simplifications from earlier Jetson code (all driven by what
the working RPi build actually does):
- **No Signal pin.** The JYQD's `Signal` screw is left disconnected;
  the chip commutates fine with it floating.
- **No EL re-edge.** EL stays HIGH; direction is sampled
  level-sensitive. `stop()` zeroes PWM but keeps EL HIGH.
- **No kick-start, no deadband.** PWM ramps directly from 0 to the
  requested duty - same as the RPi reference.
- **Per-side hardware PWM.** L on BCM 12, R on BCM 13. True
  differential drive is supported.

Override individual pins via `NINA_NAV_L_EN`, `NINA_NAV_L_DIR`,
`NINA_NAV_L_PWM`, `NINA_NAV_R_EN`, `NINA_NAV_R_DIR`, `NINA_NAV_R_PWM`.
Flip wheel polarity with `NINA_NAV_INVERT_LEFT=1` /
`NINA_NAV_INVERT_RIGHT=1`. Default cruise speed is `NINA_NAV_SPEED=15`
(matching the RPi reference build).

### Remote mode (Pi motor bridge)

If the Jetson's GPIOs aren't a reliable way to drive the JYQDs (drive
strength, dead pads, alt-function claims), Nina supports offloading
**only** the motor switching to a Raspberry Pi. The Jetson keeps
running the GUI, vision, autonomy, sensors, SLAM, etc.; the Pi runs
`pi_motor_bridge/motor_bridge.py`, which owns the JYQDs and listens
for short ASCII commands over a serial link.

Architecture:

```
┌──────────────────────────┐                  ┌──────────────────────┐
│ Jetson Orin Nano         │                  │ Raspberry Pi         │
│   GUI / vision / nav     │  ── USB-UART ──> │   pigpiod            │
│   sensors / SLAM         │     115200 8N1   │   motor_bridge.py    │
│                          │  <── ack/event ──│   navigation_bldc.py │
│   RemoteNavigationMgr    │                  │   ─────► JYQD x2     │
└──────────────────────────┘                  └──────────────────────┘
```

The selection happens at `NinaService` construction time and applies
everywhere the GUI / autonomy / CLI tools touch motors — there's no
"local vs remote" branch in any caller; both managers implement the
same surface.

#### Wiring (recommended: USB-to-TTL adapter)

| USB-to-TTL adapter | Connect to                        |
|--------------------|-----------------------------------|
| USB                | Jetson USB-A (any free port)      |
| TX                 | Raspberry Pi pin 10 (BCM 15, RXD) |
| RX                 | Raspberry Pi pin 8  (BCM 14, TXD) |
| GND                | Raspberry Pi pin 6  (any GND)     |
| VCC                | NOT CONNECTED                     |

Adapter shows up on the Jetson as `/dev/ttyUSB0` (or `/dev/ttyUSB1` if
the Dynamixel adapter already took USB0).

> **Cross-over.** Adapter TX → Pi RX, Adapter RX → Pi TX. Wire the
> opposite of what the labels say.

The Pi-side wiring (JYQD ↔ Pi GPIO) is the proven RPi prototype
mapping, documented in `pi_motor_bridge/PINMAP.md`. If the Pi was
already driving these motors before, don't move any wires — just put
the JYQDs back where they were.

#### One-time Pi setup

```bash
# On the Raspberry Pi:
sudo raspi-config
#   3 Interface Options
#     I6 Serial Port
#       "Login shell over serial?"     -> No
#       "Serial port hardware enabled?"-> Yes
#   reboot

sudo apt install -y pigpio python3-pigpio
sudo pip3 install pyserial
sudo systemctl enable --now pigpiod

# Get the bridge files onto the Pi (e.g. scp the whole pi_motor_bridge/
# directory over from the Jetson, or git clone the repo on the Pi).

cd pi_motor_bridge
# (one-shot test in foreground:)
sudo python3 motor_bridge.py --verbose

# (or install as a systemd service so it auto-starts on boot:)
sudo bash install_service.sh
sudo systemctl status motor-bridge
```

#### Switch the Jetson over to remote mode

```bash
# On the Jetson:
export NINA_NAV_MODE=remote
export NINA_NAV_REMOTE_PORT=/dev/ttyUSB0   # or /dev/ttyUSB1 if Dynamixel took USB0
export NINA_NAV_REMOTE_BAUD=115200

# Smoke-test the link end-to-end (no GUI):
python3 -m nina.app.nav_bridge_test --port /dev/ttyUSB0 --speed 25 --duration 3

# Quick connectivity check via the main CLI:
python3 -m nina.app.main nav-bridge-ping
```

If the smoke test passes, launch the GUI normally — `NinaService`
reads `NINA_NAV_MODE` at startup and the Drive screen, autonomy pilot,
and CLI tools all route through the bridge automatically.

#### Wire protocol (for reference)

ASCII over 115200 8N1, newline-terminated. Documented in detail in
`pi_motor_bridge/motor_bridge.py`.

| Direction | Line                                  | Reply       | Effect                                    |
|-----------|----------------------------------------|-------------|-------------------------------------------|
| J → Pi    | `PING`                                 | `PONG`      | health-check                              |
| J → Pi    | `SET <ldir> <lspeed> <rdir> <rspeed>`  | `OK`/`ERR`  | per-wheel direction + speed               |
| J → Pi    | `STOP`                                 | `OK`        | PWM=0, EL stays HIGH (chip armed)         |
| J → Pi    | `ESTOP`                                | `OK`        | PWM=0, EL LOW (chip disabled, no torque)  |
| J → Pi    | `LED <CONNECTED|ERROR|WAITING|OFF>`    | `OK`/`ERR`  | status LED                                |
| Pi → J    | `READY`                                | -           | bridge has finished GPIO init             |
| Pi → J    | `EVT WATCHDOG`                         | -           | Pi stopped wheels because Jetson went silent while moving |

A 1.5 s **watchdog** on the Pi side stops the wheels if no command
arrives while they're commanded to move, so a Jetson crash or unplugged
cable can't run the bot away. Tune via `NINA_BRIDGE_WATCHDOG_SEC` on
the Pi.

#### Remote-mode env vars

| Var                            | Default            | Meaning                                            |
|--------------------------------|--------------------|----------------------------------------------------|
| `NINA_NAV_MODE`                | `local`            | `local` (Jetson GPIO) or `remote` (Pi bridge)      |
| `NINA_NAV_REMOTE_PORT`         | `/dev/ttyUSB0`     | Serial device on the Jetson                        |
| `NINA_NAV_REMOTE_BAUD`         | `115200`           | Must match `motor_bridge.py --baud` on the Pi      |
| `NINA_NAV_REMOTE_TIMEOUT_SEC`  | `0.4`              | Per-line response wait                             |
| `NINA_NAV_INVERT_LEFT`         | `0`                | Flip left wheel forward/backward (works in both modes) |
| `NINA_NAV_INVERT_RIGHT`        | `0`                | Flip right wheel forward/backward (works in both modes) |
| `NINA_BRIDGE_PORT` (on the Pi) | `/dev/serial0`     | Serial device on the Pi                            |
| `NINA_BRIDGE_BAUD` (on the Pi) | `115200`           | Pi-side baud                                       |
| `NINA_BRIDGE_WATCHDOG_SEC` (on the Pi) | `1.5`      | Stop wheels if Jetson goes silent while moving     |

The local-mode `nav-test-direction` and `nav-test-pin` CLI commands
refuse to run in remote mode — they probe Jetson GPIOs, which the Pi
now owns.

---

## Vision — USB camera + perception

![Vision screen](screens/screen-vision.png)

- **Live camera card** showing the USB feed (`/dev/video<N>`,
  configurable via `NINA_VISION_CAMERA`) with bounding-box overlays.
- **Recognition** toggles on the right rail:
  - **Face detection** — YuNet (`cv2.FaceDetectorYN`); ships with
    OpenCV ≥ 4.5.4. The 340 KB ONNX model is downloaded once to
    `nina/models/weights/face_detection_yunet_2023mar.onnx`. When
    enabled, also lazy-loads the **SFace** recogniser
    (`cv2.FaceRecognizerSF`, ~38 MB ONNX cached at
    `nina/models/weights/face_recognition_sface_2021dec.onnx`) so
    enrolled faces are matched to a name in real time.
  - **Object detection** — Ultralytics YOLOv8n on COCO-80. On Jetson
    the pipeline auto-exports a **TensorRT FP16** engine on first
    run (`nina/models/weights/yolov8n.engine`) and caches it; PyTorch
    CPU fallback on dev hosts.
  - **Object confidence** slider (50–99%). YOLO's per-prediction
    confidence floor is set live via `VisionWorker.set_object_confidence`,
    so dragging the slider tightens / loosens detections without
    rebuilding the TensorRT engine. Defaults to **80%** (override via
    `NINA_VISION_OBJECT_CONF=0.85` env var if you want a different
    starting point).
  - **Person tracking** — toggle for the next iteration's tracker.
- **Detected** rolling list shows the class (or recognised name) and
  match score for each visible detection.
- **Camera** controls: resolution dropdown (640×480 / 1280×720 / etc),
  brightness slider, exposure mode.
- **Train a new face** opens the enrolment dialog. Type a name, look
  at the camera, and Nina captures 8 high-confidence samples of a
  single face. The averaged 128-d SFace embedding is persisted to
  `nina/data/faces.json`. Subsequent recognitions draw the matched
  name + cosine score on the bbox and trigger an automatic **"Hello
  <name>"** greeting (cached at `nina/data/greetings/<name>.mp3`,
  cooldown 30 s per person to avoid spam).
- **Snapshot** saves the current frame to `~/Pictures/nina-snapshots/`.
- **Play Objects** speaks the labels currently visible to the YOLO
  detector ("I see a person, two chairs and a bottle.") via gTTS +
  the standard `AudioPlayer`. Disabled until the detector reports at
  least one box. Each unique sentence is cached as an MP3 under
  `nina/data/announcements/` so repeat scenes replay instantly with
  no internet round-trip; a 1.5 s cooldown stops a double-click from
  overlapping playbacks.
- Top pill diagnoses missing hardware: `Camera /dev/video0 not
  found`, `OpenCV not installed`, `Ultralytics not installed`, etc.

---

## Map — SLAM + sensor fusion + autonomy

![Map screen](screens/screen-map.png)

- **Occupancy map** card on the left, drawing BreezySLAM's RMHC\_SLAM
  byte-map (walls black, free space light, unknown grey) plus a
  Sirena red triangle pose marker. Falls back to a passthrough
  rasteriser if BreezySLAM isn't installed, so the screen still
  renders raw lidar points.
- **Autonomous mode** toggle (mirrored on Drive). Turning it on:
  1. Starts the SLAM worker (lidar + BreezySLAM).
  2. Opens the HC-SR04 ring, the IR cliff sensor and the D435.
  3. Spawns the `AutonomousPilot` reactive controller (5 Hz default).
- **Mapping** action row: **Start mapping** (SLAM only, no driving),
  **Save map** (PGM dump of the current grid), **Clear** (reset and
  replay live scans into a fresh map).
- **Sensor health** pills — one per sensor (`Lidar`, `Depth`, `IR`,
  `Ultra`) with live / sim / error status.
- **Pose** card: live `x / y / θ` from the SLAM engine.
- **Pilot** card: last decision + reason from `AutonomousPilot`
  (`cruising · forward clear 1.4 m`, `turning right · left blocked
  280 mm`, `e-stop · cliff alarm`, `idle`).
- Top pill explains any degraded state in plain English (`SLAM
  fallback - breezyslam not installed`).

### Sensors (`nina/sensors/`)

| Role            | Part            | Mount                                          |
| --------------- | --------------- | ---------------------------------------------- |
| 360° lidar      | RPLIDAR A1M8    | Head, USB serial (`/dev/ttyUSB0`)              |
| Depth camera    | Intel RealSense D435 | Front of chassis, ~10° downtilt, USB 3    |
| IR cliff        | Sharp GP2Y0E02B | Front bumper, downward, I²C bus 1, addr `0x40` |
| Ultrasonic ring | 4× HC-SR04      | Chassis FL/FR/RL/RR, BCM GPIO                  |

Each driver soft-imports its vendor library, exposes an
`is_available()` check, and runs its own background thread so the
SLAM engine can read the latest reading without blocking. A missing
sensor doesn't take down the rest — the pilot keeps running on
whatever sensors are alive.

### Pilot behaviour ("safe wander", V1)

- Forward when `forward ≥ NINA_AUTO_FWD_CLEAR_MM` (default 700 mm)
  AND both side margins exceed `NINA_AUTO_SIDE_CLEAR_MM` (350 mm).
- Otherwise commit a brief in-place turn toward the clearer side for
  `NINA_AUTO_TURN_MS` ms (default 350) and re-evaluate.
- If any sector drops below `NINA_AUTO_ESTOP_MM` (300 mm) **or** the
  IR sensor fires the cliff alarm, reverse for
  `NINA_AUTO_BACKOFF_MS` ms and re-pick a direction.
- **Layer 0 safety**: if every sensor returns no reading, the pilot
  stops and reports "all sensors blind" instead of driving deaf.

---

## Actions — record / play / audio

![Actions screen](screens/screen-actions.png)

Three sub-tabs share a left-rail Nina photo + status line:

- **Playback** *(shown above)*. Lists every action registered in the
  manifest (`namaste`, `neutral`, …) with duration, frame count and
  audio summary. Each row exposes a Sirena red **Play** button (smooth
  replay through `NinaService` with synchronised audio if a clip
  exists) and an **Audio** shortcut that jumps to the editor with the
  right action pre-selected. **Refresh from manifest** picks up new
  recordings without restarting the app.
- **Record**. Releases torque on the configured Dynamixel IDs,
  captures frames at the chosen rate, and saves a JSON clip into
  `nina/actions/recordings/`. The new clip is registered in the
  manifest atomically.
- **Audio** (gTTS). Pick an action from the dropdown, type the words
  to speak (defaults to the action name), pick a voice preset (US,
  UK, Australian, Indian, Hindi, …), set an `audio_offset` (seconds
  of motion before the clip fires), then **Preview**, **Generate &
  Save**, **Save offset** or **Remove**. The MP3 is rendered with
  gTTS, saved to `nina/actions/audio/<action>.mp3`, and the manifest
  is updated atomically.

The same authoring is also exposed as
`scripts/generate-action-audio.py` for headless use.

---

## Settings — 9 categories with sub-sidebar

![Settings screen](screens/screen-settings.png)

A secondary light-grey sub-sidebar lists the nine categories
(General, Network · Wi-Fi, Display, Audio, Privacy, Autodock, Voice
Module · ESP, Power, OTA Update). The top of the right pane shows a
small **Nina identity card** (photo, robot name, version, serial)
with a **View health** shortcut.

The **General** pane (shown above) is fully wired:

- Robot name, time zone, default language, boot action.
- Toggles: *Speak greeting on boot*, *Show diagnostic overlay on
  screen*.
- **Save changes** persists to `nina/config/settings.py`’s
  `NinaSettings`; **Discard** rolls back; **Reset all** wipes to
  defaults.

The non-General categories ship as polished UI scaffolds backed by
in-process stubs — firmware can replace each stub without touching
the UI.

---

## Health — donut + subsystem table

![Health screen](screens/screen-health.png)

- **Donut gauge** with the real Nina photo inset in the hole. The
  ring shows OK / Warn / Error split (green / amber / red) and the
  centre text shows `<ok>/<total>` checks passing.
- Banner heading flips between **All systems nominal** and **Action
  required** based on the worst row.
- **Subsystem table** with 13 rows: Dynamixel bus, FTDI USB-serial,
  USB Camera, Lidar, IR sensors, Ultrasonic, BLDC drivers, Battery,
  Temperature, CPU, Storage, Network, Audio. Each row has its own
  `View logs` shortcut and a status pill (`OK`, `Pending`, `Warning`,
  `Error`).
- **Run all checks** kicks off a fresh sweep through
  `workers/health_collector.py`; **Export report** dumps a JSON
  snapshot for support tickets.

---

## Threading & service model

```
sirena_ui/                       nina/
  workers/                         controllers/
    nina_service.py  ──────────►   navigation_manager.py  (BLDC)
       │                           dynamixel_manager.py    (DXL bus)
       ├── drive_controller.py
       ├── vision_worker.py        sensors/
       ├── slam_worker.py    ────► rplidar_a1.py
       ├── autonomy_controller.py  hcsr04.py
       │                           gp2y0e02b.py
       ├── playback_worker.py      realsense_d435.py
       ├── record_worker.py
       ├── audio_gen_worker.py     slam/
       └── health_collector.py     engine.py            (BreezySLAM)

                                   navigation/
                                     obstacle_field.py
                                     autonomous_pilot.py
```

- `NinaService` owns the single `DynamixelManager` and exposes a
  `bus_lock` (`threading.RLock`).
- `PlaybackWorker` and `RecordWorker` are `QThread`s that acquire
  the bus lock for their duration, so they can never race on the
  serial port.
- `DriveController`, `VisionWorker`, `SlamWorker` and
  `AutonomyController` each own a dedicated background thread for
  their hardware loop and emit Qt signals back to the UI thread.

---

## Tunable env vars (high-traffic)

```bash
# Navigation backend (local Jetson GPIO vs remote Pi serial bridge)
# See "Remote mode (Pi motor bridge)" above for the full setup.
export NINA_NAV_MODE=local                     # or 'remote' for the Pi bridge
export NINA_NAV_REMOTE_PORT=/dev/ttyUSB0       # only used in remote mode
export NINA_NAV_REMOTE_BAUD=115200             # must match motor_bridge.py on Pi
export NINA_NAV_INVERT_LEFT=0                  # flip left wheel forward/backward
export NINA_NAV_INVERT_RIGHT=0                 # flip right wheel forward/backward

# Vision
export NINA_VISION_CAMERA=0
export NINA_VISION_TRT=1                       # 0 = force PyTorch CPU
export NINA_VISION_YOLO_WEIGHTS=/path/to.pt

# Lidar
export NINA_LIDAR_PORT=/dev/ttyUSB0
export NINA_LIDAR_BAUD=115200

# Ultrasonic ring (BCM pin numbers; defaults shown)
export NINA_HCSR04_FL_TRIG=19 NINA_HCSR04_FL_ECHO=9
export NINA_HCSR04_FR_TRIG=7  NINA_HCSR04_FR_ECHO=8
export NINA_HCSR04_RL_TRIG=11 NINA_HCSR04_RL_ECHO=4
export NINA_HCSR04_RR_TRIG=6  NINA_HCSR04_RR_ECHO=26
# NINA_HCSR04_DISABLE=1 to skip the ring entirely

# IR cliff
export NINA_IR_I2C_BUS=1
export NINA_IR_I2C_ADDR=0x40

# Depth
export NINA_DEPTH_FPS=15
# NINA_DEPTH_DISABLE=1 to skip the D435

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

---

## Re-capturing the screenshots

The PNGs in `sirena_ui/docs/screens/` are produced by booting the
real `MainWindow` under offscreen Qt and saving each screen via
`QWidget.grab()`. To regenerate them after a UI change:

```bash
PYTHONPATH=. python3 sirena_ui/docs/_capture_screens.py
```

The script monkey-patches `QMessageBox` so dialogs don't block, and
it intentionally runs without any robot hardware — every "sim" /
"Not connected" pill in the captures is real screen state, not a
mockup.

---

## Status of each screen

| Screen   | Wiring                                       | Notes                                          |
| -------- | -------------------------------------------- | ---------------------------------------------- |
| Home     | Live status pills + manifest                  | Quick-action tiles deep-link into every screen |
| Drive    | Live BLDCs (`NavigationManager` local OR `RemoteNavigationManager` via Pi bridge) | Sim fallback when backend isn't reachable     |
| Vision   | Live USB cam + YuNet + YOLOv8 (TensorRT FP16) | `Person tracking` is next-iteration            |
| Map      | Live RPLIDAR + HC-SR04 + IR + D435 + SLAM     | `safe wander` autonomy V1                      |
| Actions  | Live Dynamixel record / playback + gTTS audio | Manifest is the source of truth                |
| Settings | General pane is live                          | Other 8 categories are scaffolds + stubs       |
| Health   | Live donut + Dynamixel rows                   | Non-DXL rows wait on subsystem integrations    |
