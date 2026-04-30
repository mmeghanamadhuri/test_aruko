# Sirena Nina — Requirements & Bring-up Reference

This is the single reference for cloning the repo, setting up a brand-new
Nina bot, and getting from "boxes on the desk" to "GUI drives the wheels"
on the Jetson 10.1" touchscreen.

The Nina platform is a two-board robot:

* **NVIDIA Jetson Orin Nano** — runs the GUI, vision (YuNet faces +
  YOLOv8 objects), SLAM (BreezySLAM + RPLIDAR), autonomy, action
  recording / playback, and audio.
* **Raspberry Pi 4** — dedicated motor controller. Owns the two
  JYQD_V7.3E2 BLDC drivers and nothing else.

The two boards talk over a 115 200 8N1 serial link
(40-pin UART crossover by default; CP2102 / FT232 USB-to-TTL adapter
also supported).

For deeper background on a particular subsystem after you finish this
doc, see:

* `pi_motor_bridge/README.md` — the canonical Pi-side bring-up
  walkthrough, including every Bookworm pothole.
* `pi_motor_bridge/PINMAP.md` — JYQD ↔ Pi GPIO wiring table.
* `sirena_ui/docs/NINA_APP.md` — full feature reference for the GUI
  (every screen, every env var, every tunable).

---

## 1. Hardware BOM

| # | Component | Spec / model | Qty | Notes |
|---|-----------|--------------|-----|-------|
| 1 | Brain SBC | **NVIDIA Jetson Orin Nano** dev kit, 8 GB | 1 | JetPack 5.x or 6.x. SD or NVMe storage both fine. |
| 2 | Motor SBC | **Raspberry Pi 4B** (2 GB+) | 1 | Pi 3 / Zero 2 W also work. **Pi 5 is NOT supported** — `pigpio` doesn't run on the Pi 5 GPIO controller. |
| 3 | BLDC drivers | **JYQD_V7.3E2** | 2 | One per wheel. Opto-isolated direction inputs. |
| 4 | BLDC motors | 24 V hub motors (whatever your build uses) | 2 | Match the JYQD output. |
| 5 | Motor battery | 24 V LiPo / Li-ion pack | 1 | Powers the JYQDs / motors only. |
| 6 | Logic supply | USB-C PSU for Jetson + USB-C PSU for Pi | 2 | Independent. Don't try to share rails between Jetson and Pi. |
| 7 | Display | **10.1" HDMI touchscreen, 1024 × 600** | 1 | The GUI is laid out for this exact panel. Larger panels work but are not the design target. |
| 8 | Serial link | 3× female-female dupont jumpers **OR** CP2102 / FT232 USB-to-TTL adapter | 1 | **Don't buy PL2303 or CH340** — neither chip's driver ships in JetPack's kernel. |
| 9 | USB camera | UVC / V4L2 USB cam | 1 | Used by the Vision screen. Any 720p+ webcam is fine. |
| 10 | Lidar (optional, for SLAM) | **SLAMTEC RPLIDAR A1M8** | 1 | USB serial, mounted on the head. |
| 11 | Depth camera (optional, for SLAM) | **Intel RealSense D435** | 1 | USB 3, ~10° downtilt at front of chassis. |
| 12 | IR cliff sensor (optional) | **Sharp GP2Y0E02B** | 1 | I²C bus 1, addr `0x40`, mounted under the front bumper. |
| 13 | Ultrasonic ring (optional) | **HC-SR04** | 4 | Chassis FL / FR / RL / RR. |
| 14 | Speaker (optional) | 3.5 mm or USB | 1 | Used by `gTTS` action audio + face-greet announcements. |

Wiring is documented per-component in `pi_motor_bridge/PINMAP.md`
(JYQDs ↔ Pi) and in `sirena_ui/docs/NINA_APP.md` (sensors ↔ Jetson).

---

## 2. Operating systems

| Host | OS | Why this version |
|------|----|------------------|
| Jetson Orin Nano | **JetPack 5.x or 6.x** (Ubuntu 20.04 / 22.04) | NVIDIA's official BSP. JetPack ships CUDA + cuDNN + TensorRT, which `ultralytics` auto-uses for FP16 YOLOv8 inference. |
| Raspberry Pi 4 | **Raspberry Pi OS Bookworm 64-bit** (Lite or Desktop) | Last release with `pigpio`-compatible GPIO controller. |
| Dev workstation | macOS / Linux / Windows | Anything with Python 3.9+; only needed for code editing + offscreen GUI smoke tests. |

---

## 3. Account / network requirements

* SSH or local terminal access to **both** the Jetson and the Pi.
* Each board on the same Wi-Fi (or hard-wired) so you can SSH back and
  forth during bring-up.
* GitHub access to `Sirena-Technologies/Nvidia-jetson-platform` (the
  bring-up scripts `git clone` the repo on each board).
* Internet on the Jetson at first launch — `gTTS` action-audio
  generation and the YuNet ONNX download both need it. After first
  use, both are fully cached on disk.
* The desktop user on each board must be in the **`dialout`** group so
  the Python serial code can open `/dev/ttyTHS*`, `/dev/ttyUSB*`, and
  `/dev/serial0` without `sudo`:
  ```bash
  sudo usermod -aG dialout $USER && newgrp dialout
  ```

---

## 4. Python dependencies

The repo ships **four** pinned-style requirements files. Install only
what you actually need on each host — the GUI, the vision stack, and
the Pi bridge are independent.

| File | Where you install it | What it pulls in |
|------|----------------------|------------------|
| `sirena_ui/requirements.txt` | **Jetson** | PyQt5, Pillow, gTTS (audio), `opencv-python-headless`, `ultralytics` (YOLOv8), `rplidar`, `breezyslam`, `smbus2`, `pyrealsense2` (x86 only). |
| `requirements-vision.txt` | Jetson (also CI) | numpy, `opencv-python-headless`, `inference`, `inference-sdk` for the standalone Roboflow vision runtime. |
| `requirements-ui.txt` | Jetson (only if you use the older FastAPI web UI) | `fastapi`, `uvicorn`, `pydantic`. Not needed for the PyQt5 GUI. |
| `requirements.txt` | Jetson (full stack) | Pulls in `requirements-vision.txt` plus `pyserial`, `rich`, `ultralytics`. |
| (apt only) | **Raspberry Pi** | `python3-pigpio`, `python3-serial`, plus `pigpiod` v79 built from source — see Pi section of `pi_motor_bridge/README.md`. The Pi doesn't use any pip requirements file. |

### Jetson — recommended install order

```bash
# Apt-installed PyQt is by far the fastest on Jetson (no compile).
sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg python3-numpy \
                    python3-pip python3-serial git mpg123

# Python deps for the GUI + vision + SLAM stacks.
cd ~/Nvidia-jetson-platform
pip install --user -r sirena_ui/requirements.txt
```

> If `pip install ultralytics` fails on torch, install the JetPack-
> matched PyTorch wheel **first** (see NVIDIA / Dusty-NV docs), then
> rerun `pip install --user --no-deps ultralytics`.
>
> `pyrealsense2` is gated to non-aarch64 in `sirena_ui/requirements.txt`
> because the pip wheel doesn't build for Jetson. On Jetson you must
> build librealsense from source against your JetPack (see
> librealsense's `doc/installation_jetson.md`). Skip if you don't have
> a D435.

### Pi — install order

`pi_motor_bridge/README.md` section 0.2 walks through this end-to-end.
The short version:

```bash
sudo apt install -y python3-serial python3-pigpio \
                    python3-setuptools python3-full \
                    build-essential wget

# Bookworm dropped pigpiod from apt; build v79 from source.
cd /tmp && wget https://github.com/joan2937/pigpio/archive/refs/tags/v79.tar.gz
tar zxf v79.tar.gz && cd pigpio-79 && make -j"$(nproc)" && sudo make install
sudo ldconfig

# Drop a systemd unit for pigpiod (the source install doesn't on Bookworm).
sudo tee /etc/systemd/system/pigpiod.service > /dev/null <<'EOF'
[Unit]
Description=Daemon required to control GPIO pins via pigpio
After=network.target
[Service]
Type=forking
ExecStart=/usr/local/bin/pigpiod -l
ExecStop=/bin/systemctl kill pigpiod
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now pigpiod
```

---

## 5. Bring-up checklist (fresh hardware → driving via GUI)

This is the canonical end-to-end order. Each step links to the deep
reference if you hit anything weird.

### 5.1 Raspberry Pi (motor controller)

1. Flash **Bookworm 64-bit** with `rpi-imager`. Set hostname / user /
   SSH / Wi-Fi in the imager's *OS customization* panel.
2. SSH in, `sudo apt update && sudo apt upgrade -y`, reboot.
3. Enable the UART, disable the serial-console login:
   ```bash
   sudo raspi-config
   # 3 Interface Options → I6 Serial Port
   #   "Login shell over serial?"        -> No
   #   "Serial port hardware enabled?"   -> Yes
   ```
4. **Force PL011 onto pins 8/10** (critical — without this the bridge
   sees garbled bytes at 115200):
   ```bash
   echo "dtoverlay=disable-bt" | sudo tee -a /boot/firmware/config.txt
   sudo reboot
   # verify after reboot:
   ls -l /dev/serial0    # expect: /dev/serial0 -> ttyAMA0  (NOT ttyS0)
   ```
5. Install pigpiod + pyserial as in section 4 above.
6. Add yourself to `dialout`: `sudo usermod -aG dialout $USER && newgrp dialout`.
7. Clone the repo and check out the active branch:
   ```bash
   cd ~/Desktop
   git clone https://github.com/Sirena-Technologies/Nvidia-jetson-platform.git
   cd Nvidia-jetson-platform && git checkout feature/nina-app
   ```
8. Wire the JYQDs to the Pi per `pi_motor_bridge/PINMAP.md`.
9. Smoke-test the motors directly (no Jetson involved):
   ```bash
   cd pi_motor_bridge
   sudo -E python3 -c "
   import time, navigation_bldc as nav
   assert nav.setup_gpio()
   nav.set_wheels(20, 'front', 20, 'front'); time.sleep(2)
   nav.soft_stop();                          time.sleep(1)
   nav.set_wheels(20, 'back',  20, 'back');  time.sleep(2)
   nav.emergency_stop()"
   ```
   Both wheels should run forward, stop, then backward. If a wheel
   spins the wrong way, leave it for now — fix in software via
   `NINA_NAV_INVERT_LEFT=1` on the Jetson side later (already set in
   the kiosk unit by default, see step 5.2.7).
10. Install the bridge as a systemd service:
    ```bash
    sudo bash install_service.sh
    sudo systemctl status motor-bridge      # expect: active (running)
    ```

### 5.2 Jetson Orin Nano (brain + GUI)

1. Flash **JetPack 5.x or 6.x** with the SDK Manager. Run through the
   first-boot wizard, set the desktop user.
2. `sudo apt update && sudo apt upgrade -y`.
3. Add yourself to `dialout`: `sudo usermod -aG dialout $USER && newgrp dialout`.
4. **For the direct-UART link** (default): enable UART1 on pins 8/10
   via `sudo /opt/nvidia/jetson-io/jetson-io.py`, save, reboot.
   Verify with a multimeter that **pin 8 idles HIGH at ~3.3 V** when
   transmitting (`pi_motor_bridge/README.md` section 0.3.c).
5. **For the USB-TTL link**: plug in the CP2102 / FT232 adapter,
   confirm it bound to `/dev/ttyUSB0` (`ls -l /dev/ttyUSB*`,
   `dmesg | tail`). PL2303 / CH340 will silently fail — buy a
   different adapter.
6. Install Python deps as in section 4 above.
7. Clone the repo and check out the active branch:
   ```bash
   cd ~
   git clone https://github.com/Sirena-Technologies/Nvidia-jetson-platform.git
   cd Nvidia-jetson-platform && git checkout feature/nina-app
   ```
8. Wire the **3-wire serial crossover** between the boards (or plug
   in the USB-TTL adapter):
   ```
   Jetson pin  8 (TX) ──> Pi pin 10 (RX, BCM 15)
   Jetson pin 10 (RX) <── Pi pin  8 (TX, BCM 14)
   Jetson pin  6 (GND) ↔ Pi pin  6 (GND)
   ```
   Both boards powered off while you wire. Don't run any 3.3 V or 5 V
   wires across.
9. End-to-end smoke test (no GUI, just the serial protocol):
   ```bash
   cd ~/Nvidia-jetson-platform
   PYTHONPATH=. python3 -m nina.app.nav_bridge_test --port /dev/ttyTHS1 --ping-only
   PYTHONPATH=. python3 -m nina.app.nav_bridge_test --port /dev/ttyTHS1 --speed 25 --duration 2
   ```
   `--ping-only` should print `PONG`. The drive command should spin
   both wheels forward at 25 % for 2 s, then stop.
10. Install the GUI kiosk autostart so the panel boots straight into
    the cockpit, fullscreen, on every reboot:
    ```bash
    ./scripts/install-nina-ui-kiosk.sh
    ```
    The installer:
    * drops a systemd user unit at
      `~/.config/systemd/user/nina-ui-kiosk.service`,
    * runs `loginctl enable-linger` so the unit survives reboot
      without a login,
    * sets `NINA_UI_FULLSCREEN=1`, `NINA_NAV_MODE=remote`,
      `NINA_NAV_REMOTE_PORT=/dev/ttyTHS1`, `NINA_NAV_INVERT_LEFT=1`,
    * `systemctl --user enable --now`s the unit, so the GUI is on
      the panel within a few seconds of running it.
11. Verify on the panel: the GUI should be up, fullscreen at
    1024 × 600. From the **Drive** screen, hold *Forward* — both
    wheels should turn the same direction. *Back* reverses both.
    *Left* / *Right* turn in place. *E-STOP* (button or `Esc` key)
    cuts torque immediately.

---

## 6. Verification commands

Use these any time something feels off. None of them require the GUI.

### On the Pi

```bash
# pigpio daemon up?
systemctl status pigpiod

# bridge service up?
systemctl status motor-bridge
journalctl -u motor-bridge -f          # tail live

# /dev/serial0 is the right device?
ls -l /dev/serial0                     # expect -> ttyAMA0

# Is anyone else holding the port?
sudo fuser -v /dev/serial0
```

### On the Jetson

```bash
# Is the kiosk service up?
systemctl --user status nina-ui-kiosk
journalctl --user -u nina-ui-kiosk -f
tail -f ~/.cache/sirena/launch.log

# Bridge ping
PYTHONPATH=. python3 -m nina.app.nav_bridge_test --port /dev/ttyTHS1 --ping-only

# Hardware-free unit tests (Jetson or dev workstation)
PYTHONPATH=. pytest tests/test_remote_navigation_manager.py -q
PYTHONPATH=. pytest tests/test_pi_motor_bridge.py -q
```

### Kiosk control (Jetson)

```bash
systemctl --user restart nina-ui-kiosk     # after a git pull
systemctl --user stop nina-ui-kiosk        # take the GUI down for the session
systemctl --user disable --now nina-ui-kiosk   # turn off autostart
systemctl --user edit nina-ui-kiosk        # override env vars per host
```

While the GUI is running:

* `F11` — toggle fullscreen / windowed
* `F10` — quit the GUI (the unit will auto-restart it)
* `Esc` (Drive screen) — EMERGENCY STOP

---

## 7. Repo layout

```
.
├── sirena_ui/              PyQt5 cockpit (Home, Drive, Vision, Map, Actions, Settings, Health)
│   ├── screens/            one file per top-level screen
│   ├── widgets/            shared widgets (sidebar, header, dpad, donut, etc.)
│   ├── workers/            QThread / background workers for each subsystem
│   ├── docs/NINA_APP.md    full feature reference for every screen
│   └── requirements.txt    Jetson-side pip deps for the GUI
│
├── nina/                   Backend the GUI talks to
│   ├── controllers/        navigation_manager (local Jetson GPIO),
│   │                       remote_navigation_manager (serial to Pi bridge),
│   │                       dynamixel_manager, action_runner
│   ├── sensors/            rplidar_a1, hcsr04, gp2y0e02b, realsense_d435
│   ├── slam/               BreezySLAM engine
│   ├── navigation/         autonomous_pilot + obstacle field
│   ├── app/                CLI entry points (main.py, nav_bridge_test.py, …)
│   └── config/             NinaSettings + env-var bindings
│
├── pi_motor_bridge/        Pi-side daemon. Owns the JYQDs.
│   ├── motor_bridge.py     serial listener + dispatch
│   ├── navigation_bldc.py  GPIO/PWM helpers
│   ├── PINMAP.md           JYQD ↔ Pi wiring table
│   ├── install_service.sh  installs motor-bridge.service into systemd
│   └── README.md           Pi bring-up walkthrough (Bookworm, pigpio, UART, …)
│
├── scripts/                One-shot installers + launchers
│   ├── launch-sirena.sh           wrapper that fixes up env + runs the GUI
│   ├── install-nina-ui-kiosk.sh   installs the kiosk systemd user unit
│   ├── install-sirena-desktop.sh  installs the desktop launcher icon
│   └── install-ftdi-udev.sh       udev rules for the FTDI adapter
│
├── desktop/                Templates the installers consume
│   ├── nina-ui-kiosk.service      systemd user unit (kiosk autostart)
│   └── sirena.desktop             freedesktop launcher entry
│
├── tests/                  Hardware-free pytest suite (mocks pigpio + serial)
├── REQUIREMENTS.md         this file
├── README.md               2-paragraph repo intro that points here
└── requirements{,-ui,-vision}.txt  pip files referenced by section 4
```

The `vision/`, `carbot_main/`, and `carbotUI/` directories belong to
the older Carbot product (window-button detection, Dynamixel arm).
They're not part of the Nina runtime and aren't required for any
section of this doc. Leave them in place — there's a separate motion-
server stack still using them.

---

## 8. Where to go next

* **Per-screen feature reference + every env var** —
  `sirena_ui/docs/NINA_APP.md`
* **Pi bring-up troubleshooting (every Bookworm pothole)** —
  `pi_motor_bridge/README.md`
* **JYQD ↔ Pi wiring table** —
  `pi_motor_bridge/PINMAP.md`
* **Action authoring (record, audio, manifest)** —
  `nina/README.md` and the *Actions* screen in the GUI
