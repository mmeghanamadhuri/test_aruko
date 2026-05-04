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
| 11 | Depth camera (optional, for autonomy) | **Intel RealSense D435** | 1 | USB 3, mounted **below** the RGB camera, tilted **10–15° down**. See §1.1 for the recommended sensor stack. |
| 12 | IR cliff sensor (optional) | **Sharp GP2Y0E02B** | 1 | I²C bus 1, addr `0x40`, mounted under the front bumper. |
| 13 | Ultrasonic ring (optional) | **HC-SR04** | 4 | Chassis FL / FR / RL / RR. |
| 14 | Speaker (optional) | 3.5 mm or USB | 1 | Used by `gTTS` action audio + face-greet announcements. |

Wiring is documented per-component in `pi_motor_bridge/PINMAP.md`
(JYQDs ↔ Pi) and in `sirena_ui/docs/NINA_APP.md` (sensors ↔ Jetson).

### 1.1 Sensor stack — recommended physical layout

The three perception sensors have non-overlapping jobs and need to be
mounted at non-overlapping heights so they don't shadow each other:

```
┌──────────┐  Top (head)        — RPLIDAR A1M8: 360° scan at one
│  LiDAR   │                      horizontal plane. Used by SLAM
├──────────┤                      for mapping/localisation and by
│ RGB cam  │  Face height       — autonomy for static obstacles.
├──────────┤                      USB camera: looks straight out for
│ Depth    │  ~30–50 cm above   — face detection (YuNet) + object
│ camera   │  the floor,          ID (YOLOv8). NOT used by autonomy
│ ↘ 10–15° │  tilted 10–15°       directly today.
├──────────┤  down              — RealSense D435: covers the volume
│   bot    │                      LiDAR can't see (wheel-level
└──────────┘                      obstacles, low furniture edges,
                                  drop-offs). Tilt-down catches the
                                  floor at ~1 m and gives drop-off
                                  detection for free.
```

Why these specific positions:

* **LiDAR on top, unobstructed.** Anything in front of the LiDAR's
  scan plane (your own chassis, the depth camera, cables) creates a
  blind cone in the SLAM map. Easiest to keep it clean by putting the
  LiDAR above everything else.
* **Depth camera below the RGB camera, not above it.** RealSense
  D435 has an IR projector that can speckle the RGB frame if it sits
  in the camera's line of sight. Below + tilted-down also means the
  D435's ~28 cm minimum range hits the floor (where the dead zone
  doesn't matter) instead of mid-room (where a person could walk
  into the dead zone and become invisible).
* **Forward-facing only.** None of the three sensors look behind the
  bot. Reverse driving is "best effort" until you add a rear
  ultrasonic or second depth camera; the autonomy stack today
  reverses only as part of stuck-recovery (~1 s pulses), not for
  free-form backwards travel.

Three-sensor coverage is enough for indoor mapped autonomy. The four
known classes of failure that IR / ultrasonic later (BoM rows 12 & 13)
fix are spelled out in `sirena_ui/docs/NINA_APP.md` under "Autonomy
sensor coverage" — read that before doing your first untethered run.

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
> because Intel doesn't publish an aarch64 wheel. On Jetson, run the
> ready-made installer:
>
> ```bash
> ./scripts/install-realsense-jetson.sh
> ```
>
> It clones librealsense, builds with `BUILD_PYTHON_BINDINGS=ON` +
> `FORCE_RSUSB_BACKEND=ON` (skips the kernel-patch step), installs
> the udev rules so non-root processes can open the camera, and
> wires `/usr/local/lib/python*/dist-packages` into your Python
> user site so `import pyrealsense2` works from the Nina venv.
> Takes ~10–20 min on an Orin Nano. Skip entirely if you don't have
> a D435 — the autonomy stack will run lidar-only.

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
   spins the wrong way, leave it for now — once the GUI is up, the
   Drive screen's **Flip L** / **Flip R** toggles will fix it in two
   clicks (saved to `~/.config/sirena/drive_polarity.json`, survives
   reboot). The legacy `NINA_NAV_INVERT_LEFT=1` env var on the Jetson
   still works as a boot-time default and is shipped in the kiosk
   unit (see step 5.2.7), but you no longer have to SSH in to flip a
   wheel — do it from the GUI.
10. Install the bridge as a systemd service. The installer enables
    **both** `pigpiod` and `motor-bridge.service` for autostart, so
    after this one-time step the bridge comes up on every Pi reboot
    with no further action — including ordering: `motor-bridge.service`
    waits for `pigpiod` (`After=` + `Wants=` in the unit file), and a
    crashed bridge auto-restarts after 2 s (`Restart=on-failure`):
    ```bash
    sudo bash install_service.sh
    sudo systemctl status motor-bridge      # expect: active (running)

    # Confirm autostart on next boot (both should print "enabled"):
    systemctl is-enabled pigpiod
    systemctl is-enabled motor-bridge
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
    sudo apt install -y x11-xserver-utils    # provides xrandr (next step depends on it)
    ./scripts/install-nina-ui-kiosk.sh
    ```
    The installer:
    * drops a systemd user unit at
      `~/.config/systemd/user/nina-ui-kiosk.service`,
    * runs `loginctl enable-linger` so the unit survives reboot
      without a login,
    * sets `NINA_UI_FULLSCREEN=1`, `NINA_NAV_MODE=remote`,
      `NINA_NAV_REMOTE_PORT=/dev/ttyTHS1`, `NINA_NAV_INVERT_LEFT=1`,
    * `apt-get install -y onboard` for the touchscreen on-screen
      keyboard (Settings password fields, recording renames, etc. all
      auto-pop the OSK on focus — see `NINA_UI_OSK*` env vars in
      `sirena_ui/docs/NINA_APP.md` for tuning),
    * `systemctl --user enable --now`s the unit, so the GUI is on
      the panel within a few seconds of running it.

    `launch-sirena.sh` runs `xrandr` on the kiosk path **only** to
    force the panel into a real 1024 × 600 mode before Qt starts. The
    cheap HDMI 10.1" panels almost universally advertise a 1920 × 1080
    EDID and rely on their internal scaler — without this step the
    GUI launches frameless across that virtual surface and the
    layouts (which are pinned to 1024 × 600 design pixels) end up
    stretched and clipped.
11. Verify on the panel: the GUI should be up, fullscreen at
    1024 × 600. From the **Drive** screen, hold *Forward* — both
    wheels should turn the same direction. *Back* reverses both.
    *Left* / *Right* turn in place. *E-STOP* (button or `Esc` key)
    cuts torque immediately.

### 5.3 Bring-up: autonomous navigation (lidar + RGB + depth)

This section assumes 5.1 + 5.2 already pass — operator can drive the
bot manually from the GUI. Goal here is to get the **Autonomous mode**
toggle on the Drive (or Map) screen working.

The autonomy stack is reactive obstacle-avoiding wander, not goto-
waypoint navigation. It runs `nina/navigation/autonomous_pilot.py`
which fuses four sensor channels via `obstacle_field.fuse()`:

| Sensor | Physical role | Software path |
|---|---|---|
| Slamtec S2E lidar (default) | 360° long-range static-obstacle layer (~30 m, dToF, Ethernet/UDP) | `nina.sensors.slamtec_s2e.SlamtecS2E` → `SlamWorker` → `bundle.lidar` |
| RPLIDAR A1M8 (legacy) | 360° short-range static-obstacle layer (~12 m, USB-serial) | `nina.sensors.rplidar_a1.RPLidarA1` → `SlamWorker` → `bundle.lidar` |
| RealSense D435 | Forward volumetric layer (low obstacles, drop-offs) | `nina.sensors.realsense_d435.RealSenseD435` → `AutonomyController._depth` → `bundle.depth` |
| HC-SR04 ring (later) | Sub-30 cm and glass/mirror coverage | `nina.sensors.hcsr04.HCSR04Array` |
| GP2Y0E02B IR (later) | Cliff / table-edge detection | `nina.sensors.gp2y0e02b.GP2Y0E02B` |

The active lidar is selected at startup by the
`nina.sensors.lidar_factory.build_lidar` factory based on the
`NINA_LIDAR_MODEL` env var (default `s2e`). The `auto` mode probes the
S2E first and falls back to the A1 driver if `pyrplidarsdk` isn't
installed — useful when one disk image needs to run on both lidar
generations.

The RGB camera + face / object detection (YuNet + YOLOv8) is **not**
fed into autonomy today — those run on the Vision tab for operator
situational awareness only. Adding semantic obstacles ("don't drive
toward a person", "approach the dog bowl") would mean wiring
`VisionWorker.detections_changed` into `obstacle_field.fuse()` later.

#### 5.3.1 Mount the sensors

See §1.1 for the recommended height stack. Quick checklist:

- [ ] LiDAR on top. For the **Slamtec S2E** (default): both the 12 V
      power barrel jack and the Ethernet cable need to be routed
      clear of the scan plane — the S2E sees ~25 m indoors so a
      cable that loops above the optical centre will paint a
      half-room phantom wall on the map. For the **legacy A1**:
      same advice, except the cable is USB-serial and 12 V isn't
      involved.
- [ ] RGB camera at face height, looking straight forward
- [ ] D435 below the RGB camera, **30–50 cm above the floor**, tilted
      **10–15° down** (use a small bracket — vertical-mount is fine)
- [ ] D435 plugged into a **USB 3** port (blue inside) on the Jetson.
      USB 2 limits depth to 480p @ 6 fps and the autonomy tick rate
      starves.
- [ ] (S2E only) The Jetson's wired Ethernet port is plugged into
      the lidar's Ethernet adapter board. The S2E ships configured
      for IP `192.168.11.2` so the Jetson side has to be in the
      same subnet — `scripts/install-slamtec-s2e-jetson.sh` handles
      this automatically. If you're running both wired LAN and the
      lidar, plug the lidar into a separate USB-Ethernet dongle
      and let that dongle hold the 192.168.11.10 static IP.

#### 5.3.2 Install pyrealsense2 on the Jetson

```bash
cd ~/Nvidia-jetson-platform
./scripts/install-realsense-jetson.sh
```

Then verify:

```bash
python3 -c "import pyrealsense2 as rs; print(rs.__version__)"
rs-enumerate-devices    # shipped by librealsense; lists the D435
```

If `rs-enumerate-devices` lists the camera but `python3 -c "import
pyrealsense2"` fails, the installer's `.pth` step didn't pick the
right Python. Re-run with `PYTHON_EXEC=/path/to/your/venv/python3
./scripts/install-realsense-jetson.sh`.

#### 5.3.3 Install breezyslam on the Jetson

`breezyslam` is **not** on PyPI — a bare `pip install breezyslam`
fails with `No matching distribution found for breezyslam>=0.5.0`.
The package lives only on GitHub
([simondlevy/BreezySLAM](https://github.com/simondlevy/BreezySLAM))
and ships a small C extension that won't compile on a fresh Jetson
without `python3-dev`. When the GUI can't import it, the Map /
Perception pane shows
`breezyslam not installed - run scripts/install-breezyslam-jetson.sh …`
in the SLAM pill. Run the installer once:

```bash
cd ~/Nvidia-jetson-platform
./scripts/install-breezyslam-jetson.sh
```

The script:

1. apt-installs the C build deps (`python3-dev`, `build-essential`,
   `git`),
2. clones `simondlevy/BreezySLAM` into `/tmp/BreezySLAM`,
3. runs `pip install --user .` from the `python/` subdir (with the
   `--break-system-packages` PEP 668 escape hatch on JetPack 6 /
   Ubuntu 22.04 as a fallback),
4. smoke-tests an `RMHC_SLAM` constructor against the RPLIDAR A1
   sensor model, so a half-broken C extension is caught immediately
   instead of at first GUI launch.

After it finishes:

```bash
python3 -c "from breezyslam.algorithms import RMHC_SLAM; print('ok')"
```

Re-launch the Nina UI; the Map / Perception lidar pane will now
build a real occupancy grid as the bot moves (was rendering single
rasterised scans in fallback mode).

> If you ever pip-install `sirena_ui/requirements.txt` directly
> (rare on Jetson — usually preferred to use apt + this installer),
> the requirements file now references the GitHub source
> (`breezyslam @ git+https://…`) instead of the broken PyPI name,
> so a plain `pip install -r` will resolve. You still need the
> apt build deps from step 1 above for the C extension to compile.

#### 5.3.4 Verify the lidar separately

For the **Slamtec S2E** (default), run the bring-up script — it sets
up the host's Ethernet IP, pings the lidar, and runs an end-to-end
driver smoke test in one shot:

```bash
cd ~/Nvidia-jetson-platform
./scripts/install-slamtec-s2e-jetson.sh
```

It will:

1. apt-install build deps + ping/iproute helpers,
2. pip-install `pyrplidarsdk` (PyPI; with the `--break-system-packages`
   PEP 668 fallback for JetPack 6),
3. configure your wired interface to `192.168.11.10/24` (via nmcli
   when NetworkManager is active, else a `systemd-networkd` drop-in),
4. `ping -c 3 192.168.11.2` so a cable / power problem is loud,
5. open the device through `pyrplidarsdk.RplidarDriver` and pull
   3 s of scan data, printing the model / firmware / serial number
   on success.

If you'd rather poke the device by hand:

```bash
ping -c 3 192.168.11.2     # default S2E IP
python3 -c "
from nina.sensors.slamtec_s2e import SlamtecS2E
import time
l = SlamtecS2E()
l.open()
time.sleep(2)
print(l.read())
l.close()
"
```

Should print a `LidarScan` object with several hundred returns.

For the **legacy A1M8** (only when `NINA_LIDAR_MODEL=a1`):

```bash
ls -l /dev/ttyUSB0    # RPLIDAR A1 default port
sudo usermod -aG dialout $USER && newgrp dialout    # if not already
python3 -c "
from nina.sensors.rplidar_a1 import RPLidarA1
import time
l = RPLidarA1()
l.open()
time.sleep(2)
print(l.read())
l.close()
"
```

#### 5.3.5 Environment variables that gate the sensors

All optional; defaults work for the recommended hardware. Set in
`desktop/nina-ui-kiosk.service` if you need to override on the bot.

| Var | Default | What it does |
|---|---|---|
| `NINA_VISION_CAMERA` | 0 | `/dev/videoN` index for the USB RGB camera the Vision / Drive / Perception screens consume. The pipeline auto-probes other indices when this one fails (Jetson Orin's `video0..video2` are usually ISP / encoder nodes, not cameras), but pinning the right one here skips the probe and shaves a few seconds off startup. |
| `NINA_VISION_AUTO_PROBE` | `1` | When the configured index doesn't deliver a frame, fall through to probing the rest of `/dev/video*`. Set to `0` to fail fast (useful for test rigs with multiple cameras where wrong-index = wrong-camera = silent bug). |
| `NINA_VISION_CANDIDATES` | (auto) | Comma-separated list of indices to probe in order, e.g. `3,8,2`. Defaults to enumerating real `/dev/video*` device files. |
| `NINA_VISION_ALLOW_REALSENSE` | `0` | The auto-probe **skips** any `/dev/video*` whose V4L2 card name mentions "RealSense" because the D435's color UVC stream would otherwise be picked up as the "RGB camera" pane (depth-camera frames showed in the Drive / Perception RGB view, real USB webcam never opened). Set to `1` only on rigs with no separate webcam where you want one camera doing double duty. |
| `NINA_DEPTH_DISABLE` | unset | `1` skips opening the D435 (autonomy runs lidar-only). Useful for debugging without the depth camera plugged in. |
| `NINA_DEPTH_WIDTH` / `_HEIGHT` / `_FPS` | 640 / 480 / 15 | D435 stream config. Lower these on USB 2. |
| `NINA_DEPTH_MAX_MM` / `_MIN_MM` | 5000 / 300 | Depth values outside this range are dropped. The lower bound is set to 300 mm (D435's published reliable minimum is ~280 mm); below that the sensor mostly returns IR projector saturation and floor reflections, which on glossy / polished floors look like phantom forward obstacles to the autonomy. |
| `NINA_DEPTH_MIN_CLUSTER_PX` | 50 | The forward / left / right region "min" requires at least this many pixels at-or-closer than the reported distance before the autonomy treats it as a real obstacle. Single-pixel IR splash from a reflective floor used to hijack `forward_min_mm` (bot spun in place even on an empty hallway); 50 px ≈ 5×10 cluster, comfortably above the noise floor and small enough to still catch a chair leg at typical cruise distance. |
| `NINA_DEPTH_TOP_SKIP_PCT` | 10 | Vertical % of the depth image discarded from the **top** before the forward / left / right cone min is computed. Defaults skip direct overhead glare. (Was 25% — too aggressive: chest-high tabletops at 1–2 m were masked out, so the bot drove into them.) |
| `NINA_DEPTH_BOT_SKIP_PCT` | 35 | Vertical % discarded from the **bottom**. Defaults skip the floor right in front of the bot — without this mask a tilted-down D435 reads the floor at ~480 mm and the autonomy spins in place forever (see §5.3.6). |
| `NINA_LIDAR_MODEL` | `s2e` | Lidar driver to load. `s2e` = Slamtec S2E (Ethernet/UDP, ~30 m, default), `a1` = legacy RPLIDAR A1M8 (USB-serial, ~12 m), `auto` = probe S2E first then fall back to A1. |
| `NINA_LIDAR_HOST` | `192.168.11.2` | Slamtec S2E IP address. The factory default; change only if you've reflashed the lidar's IP through the Slamtec SDK or RoboStudio. |
| `NINA_LIDAR_UDP_PORT` | `8089` | UDP port the S2E listens on (factory default). |
| `NINA_SLAMTEC_S2E_SUBPROCESS` | `1` | `1` (default) runs `pyrplidarsdk` in a separate **spawned** Python process and pipes scan batches back to Nina. The published wheel holds the **GIL** during blocking `connect()` / `get_scan_data()` calls; doing that in a `thread` inside the same interpreter as **Qt freezes the Map / Perception UI** for seconds. Set to `0` only for low-level debugging (single-process, GDB-friendly) — expect the GUI to stall whenever the lidar blocks. |
| `NINA_LIDAR_PORT` | `/dev/ttyUSB0` | RPLIDAR A1 serial device (only used when `NINA_LIDAR_MODEL=a1`). |
| `NINA_LIDAR_BAUD` | `115200` | RPLIDAR A1 baud rate (only used when `NINA_LIDAR_MODEL=a1`). |
| `NINA_LIDAR_BINS` | `400` (S2E) | Bin count for the per-revolution scan vector. The S2E publishes ~32k samples/s at 10 Hz so 400 bins ≈ 0.9° angular resolution. The A1 driver hard-codes 360. |
| `NINA_LIDAR_MAX_RANGE_MM` | `28000` | (S2E) Distance returns past this are treated as "no return" — clips multipath reflections in indoor rooms. The S2E's published max is 30 m; we clip at 28 m by default. |
| `NINA_LIDAR_MIN_RANGE_MM` | `100` | (S2E) Distance returns closer than this are treated as the lidar seeing its own housing / the bot's own structure. |
| `NINA_LIDAR_DISABLE` | unset | `1` skips lidar; SLAM and autonomy both degrade gracefully. |
| `NINA_SLAM_METERS` | 12 (S2E) / 8 (A1) | Side length (m) of the square SLAM world. The S2E reliably ranges ~25 m indoors so a 12 m world covers a typical hallway loop without overflowing the BreezySLAM particle filter. (Was 8 m on the A1 path; that stays the A1 default because the A1's 6 m range can't fill anything bigger.) |
| `NINA_SLAM_PIXELS` | 1000 (S2E) / 800 (A1) | Square map resolution. With the 12 m default world this is 12 mm/px — fine enough to render walls as multi-pixel features after letterboxing into the Perception card. |
| `NINA_SLAM_LASER_MAX_MM` | 28000 (S2E) / 12000 (A1) | The `distance_no_detection_mm` parameter passed to BreezySLAM's Laser model. Must match the physical lidar's effective range or the particle filter mis-weights long returns. |
| `NINA_SLAM_LASER_SCAN_SIZE` | 400 (S2E) / 360 (A1) | The `scan_size` parameter passed to BreezySLAM's Laser model. Drives the resampling cadence the SlamWorker uses before calling `slam.update()`. |
| `NINA_SLAM_LASER_SCAN_RATE_HZ` | 10 (S2E) / 5.5 (A1) | The `scan_rate_hz` parameter passed to BreezySLAM's Laser model. Used by the Markov-chain particle filter to gauge expected motion between sweeps. |
| `NINA_AUTO_TICK_HZ` | 8 | Autonomy decision rate. (Was 5 Hz — at 15% PWM the bot coasts a few cm per 200 ms tick, enough to overshoot a turn decision; 8 Hz halves that.) |
| `NINA_AUTO_CRUISE_PCT` | 15 | Forward cruise speed during autonomous mode, as % of full PWM. Matches the manual-mode minimum so a handover doesn't change pace. |
| `NINA_AUTO_TURN_PCT` | 16 | Turn-in-place speed % during obstacle avoidance. |
| `NINA_AUTO_FWD_CLEAR_MM` | 1200 | Required forward clearance (closest sensor reading) before the pilot will commit to a forward step. (Was 700 mm — at walking speed the BLDCs coasted to within 50–60 cm of people before stopping; 1200 mm leaves the bot ~1 m of buffer for braking.) |
| `NINA_AUTO_SIDE_CLEAR_MM` | 450 | Per-side clearance for forward to be allowed. |
| `NINA_AUTO_ESTOP_MM` | 600 | Anything closer than this in front triggers an immediate reverse. (Was 300 mm — too late; reverse only engaged once the bot was already 30 cm away.) |
| `NINA_GOTO_ARRIVAL_MM` | 250 | Distance from the goal under which `GotoPilot` reports `arrived` and stops. Set to roughly half the chassis width so the bot doesn't pursue the exact tap pixel forever. |
| `NINA_GOTO_INFLATE_MM` | 250 | Footprint inflation in mm — the bot's body half-width. The A* planner dilates every wall pixel by *at least* this radius so the resulting path leaves a Nina-shaped buffer. Bump for wider bots; for tighter safety margins use `NINA_GOTO_MIN_PASSAGE_MM` instead. |
| `NINA_GOTO_MIN_PASSAGE_MM` | 610 | Minimum corridor width (between facing walls) the planner is allowed to route through, in mm. Default = **2 ft / 24 in / 610 mm**, the smallest passage Nina is supposed to thread in lab + corridor environments. The effective dilation is `max(NINA_GOTO_INFLATE_MM, ⌈min_passage / 2⌉)`, so this knob is the right place to express **operator policy** ("I want 3 ft of buffer in the showroom" → set to 914) rather than bot geometry. Set to 0 to disable the floor and fall back to footprint-only inflation. |
| `NINA_GOTO_CRUISE_PCT` | 15 | Goto forward speed (matches `NINA_AUTO_CRUISE_PCT` so a wander → goto handoff doesn't change pace). |
| `NINA_GOTO_TURN_PCT` | 16 | Goto in-place spin speed. |
| `NINA_GOTO_HEAD_DEG` | 18.0 | Heading-error deadband. Inside this window the pilot drives forward (still steering), outside it turns in place. Wider than the wander pilot's implicit binary so noisy SLAM headings don't flip the pilot into spin during normal driving. |
| `NINA_GOTO_LOOKAHEAD_MM` | 600 | Pure-pursuit lookahead distance. Larger = smoother arcs, smaller = tighter follow at the cost of wobble. |
| `NINA_GOTO_REPLAN_SEC` | 3.0 | Periodic replan cadence even if everything looks fine. As the SLAM map grows the optimal path may shorten — this picks that up. |
| `NINA_GOTO_STUCK_SEC` | 5.0 | Stuck-detection window in seconds. |
| `NINA_GOTO_STUCK_MM` | 50 | If the pose moved < this many mm in the stuck window, the pilot reports `stuck` and stops. |
| `NINA_GOTO_TICK_HZ` | 8 | Goto control loop rate. Matches the wander pilot's `NINA_AUTO_TICK_HZ`. |
| `NINA_GOTO_UNKNOWN_COST` | 1.5 | A* cost multiplier for grey/unknown grid cells. >1 nudges the planner to prefer mapped corridors but still routes into unexplored space when needed. |

#### 5.3.6 First autonomy run

1. Place the bot in an open area with at least 1.5 m clearance on all sides.
2. From the GUI, open **Drive** → **Settings** chip → confirm the
   speed slider is at 15 % (the autonomy cruise default and the
   BLDC manual-mode floor; the slider is also capped at 25 % to
   keep the BLDCs in their stable PWM band).
3. Tap **Map (SLAM)** so the lidar/SLAM worker starts and you can
   see scans coming in. A coarse occupancy grid should fill in
   within ~5 s of motion.
4. Back to **Drive**. Tap **Autonomous mode**. The Map / Drive screen
   sensor pills go live: green = sensor up, amber = sensor opened
   but no readings yet, red = open failed.
5. The bot should start wandering: forward when the path is clear,
   turn-in-place when an obstacle is in the depth or lidar cone,
   short reverse-and-rotate if it gets stuck.
6. Hit the on-screen **E-STOP** (or the `Esc` key) any time. The
   pilot stops the wheels and engages the brake within one tick
   (`AutonomySettings.tick_hz`, default 5 Hz → ≤ 200 ms).

#### 5.3.7 First goto-point run

1. Drive Nina around the room first with the manual D-pad until
   the SLAM grid has filled in — at least the immediate corridors
   the bot will use. The A* planner refuses to route into a wall,
   and an empty grey grid is a wall by default until something
   useful gets carved out (an unknown cell on the planner edge
   between the bot and the goal still routes through, just at the
   `NINA_GOTO_UNKNOWN_COST` premium).
2. Open the **Map** screen and press **Tap on map** in the
   Go to point card. The button toggles to `ARMED`, the cursor
   becomes a pointing-finger hand, and the pill reads
   "Tap a point to start".
3. Tap a free-ish (light-coloured) cell on the occupancy grid.
   Nina:
   1. Plans an A* path with walls dilated by
      `max(NINA_GOTO_INFLATE_MM, ⌈NINA_GOTO_MIN_PASSAGE_MM / 2⌉)`.
      The default 610 mm passage floor means the planner refuses
      any corridor narrower than 2 ft, regardless of how thin you
      claim the bot is.
   2. Renders the planned waypoints as a dashed red polyline plus
      a flag pin at the goal.
   3. Turns in place to align with the path lookahead, then drives
      forward, replanning every `NINA_GOTO_REPLAN_SEC` and
      whenever the live obstacle field disagrees with the path.
   4. Reports `arrived` and stops on its own once it's within
      `NINA_GOTO_ARRIVAL_MM` of the goal. **Stop-and-stay** is
      the default — the bot stays put until the operator either
      taps a new goal, presses **Cancel goto**, or toggles
      autonomy off.
4. If the click landed on a wall, the planner snaps the goal to
   the nearest free cell (`snap_radius_mm = 1500` by default).
   The map pin renders as a hollow ring at the click and a filled
   flag at the snapped goal so the operator sees both.
5. **Cancel goto** stops the goto pilot. If autonomy was OFF when
   the goto arm-tap fired, autonomy also turns off; if autonomy
   was already ON in wander mode before the tap, the bot returns
   to wander.
6. The same flow works from the Android companion when
   `NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1` on the Jetson — see
   `docs/COMPANION_APP.md` for the `POST /v1/autonomy/goal` and
   `DELETE /v1/autonomy/goal` REST endpoints.

**Troubleshooting: "the bot just spins, never moves forward"**

Almost always one of the forward-cone sensors is reading a phantom
obstacle. The autonomy log spells out exactly which one:

```
journalctl --user -u nina-ui-kiosk -f | grep autonomy
# … autonomy turn=turn_left reason=forward_blocked forward=480mm
#    clear=700mm left=2100mm right=2200mm
#    by_source={'lidar': 2100, 'depth': 480}
```

In that example, lidar reports 2.1 m of clearance straight ahead but
depth reports 480 mm — the floor leaking through the bottom of the
D435 frame. The fix is the floor-mask defaults
(`NINA_DEPTH_BOT_SKIP_PCT=35`); if your D435 is mounted lower or
tilted further down, raise it (e.g. `=45`). If your D435 sits at face
height with no tilt, you can lower it (`=10`) to let the bottom rows
back in.

**Reflective floors specifically.** Polished concrete, vinyl, and
glossy tile bounce the D435's IR projector light back as
single-pixel hot returns at 100–300 mm scattered through the *middle*
of the depth image — well above the bottom-mask line. The cluster
filter (`NINA_DEPTH_MIN_CLUSTER_PX=50`) is the real defence here:
single-pixel splash can't pass it. If the bot is still spinning on a
particularly mirror-finish floor, raise the floor:

* `NINA_DEPTH_MIN_CLUSTER_PX=100` — require a 10×10 cluster instead
  of 5×10; better tolerance to scattered IR splash, slightly less
  sensitivity to thin objects (chair legs at >2 m).
* `NINA_DEPTH_MIN_MM=400` — drop the lower depth bound further; the
  RealSense already publishes nothing useful in 280–400 mm anyway,
  this just makes the autonomy ignore those bins. Lidar / ultrasonic
  still cover the sub-400 mm zone.

If the breakdown shows `lidar=120` (or similar low value) the lidar
itself is reading something close in its forward sector — most
commonly the lidar is mounted with 0° pointing **at the bot's body**
instead of away from it. Rotate the lidar until the cable bundle
(power + Ethernet on the S2E, USB-serial on the A1) comes out the
side opposite the bot's "front".

**Troubleshooting: "the bot got too close to people / banged into a table"**

The defaults are tuned for indoor walking-speed wandering at 15 % PWM
(~0.4 m/s) on the BLDC drivetrain:

| Symptom | Knob | Default | Try |
|---|---|---|---|
| Stops too close to people / dogs | `NINA_AUTO_FWD_CLEAR_MM` | 1200 | Bump to `1500` for an extra arm's length of buffer |
| Bumps tabletops / desks (but lidar is fine) | `NINA_DEPTH_TOP_SKIP_PCT` | 10 | Lower to `5` — lets more of the upper image through; only do this if your room has no overhead halogens (those return as bright shorts) |
| Reverses too rarely / cuts a corner | `NINA_AUTO_ESTOP_MM` | 600 | Bump to `800` so reverse fires earlier |
| Reaction looks sluggish | `NINA_AUTO_TICK_HZ` | 8 | Push to `10` (more CPU; rarely needed on Orin NX) |

If lidar reports the table fine but the bot still hits it, check
**lidar height vs the obstacle**. The RPLIDAR A1 only sees its scan
plane (typically the top of the bot stack). A 70 cm-high tabletop is
INVISIBLE to a lidar mounted at 80 cm — the lidar sweeps over the
top of the table. Move the lidar lower, or rely on the depth camera
to catch chest-height obstacles (which is exactly what
`NINA_DEPTH_TOP_SKIP_PCT=10` is now set up for).

**Troubleshooting: "the LiDAR map view is mostly empty / a small patch"**

The default world size is now 8 m (10 mm/px on an 800 px grid). A
4–5 m room should fill ~50% of the rendered view with walls. If
you still see mostly grey:

1. Check the lidar pill on the Map / Perception screen — if it says
   `sim - …` the lidar isn't connected and the engine is rendering
   placeholder data. Fix the USB / serial wiring first.
2. Open Health and read `scans_processed` for the SLAM row. If it's
   stuck at 0, the lidar is connected but nothing is reaching the
   engine; confirm with `journalctl --user -u nina-ui-kiosk -f
   | grep -i 'rplidar\|slam'`.
3. If `scans_processed` is climbing but the map still looks empty,
   the bot is in an oversized room or outdoors (RPLIDAR A1 is only
   reliable to ~6 m). Bump `NINA_SLAM_METERS` to `12`–`16` to fit
   the larger space, accepting that mm/px gets coarser.

**Troubleshooting: "RGB camera not connecting / error 3 / black viewport"**

The Vision pipeline auto-probes `/dev/video*` on first open, so a
plug-and-play USB webcam usually just works. When it doesn't, the
pill on the Vision / Drive screen will tell you exactly which
indices were tried and why each one was rejected. Common shapes:

| Pill text | Meaning | Fix |
|---|---|---|
| `Camera /dev/video0 not readable: no permission …` | The user is not in the `video` group | `sudo usermod -aG video $USER` then log out / back in |
| `Camera nodes opened but delivered no frames (video0, video1, video2)` | Only Jetson ISP / encoder nodes were probed; no real USB webcam responded | Plug in (or re-plug) the webcam, then `ls -la /dev/video*` should show a NEW node appear (typically `video3`+) |
| `Camera not connected. Tried: video0(wont open), …` | Driver rejected every index | `dmesg \| tail -30` after re-plugging the webcam; look for a `uvcvideo` line. Missing UVC firmware / blacklisted module is the usual culprit |
| `Camera ready on /dev/video3 (auto-probed; configured was video0)` | Working, but probing every launch wastes ~1 s and ISP nodes still get touched. Pin it permanently. | Set `NINA_VISION_CAMERA=3` in `desktop/nina-ui-kiosk.service` |
| `RGB webcam not connected. The only camera I found (videoN, video(N+1)) is the Intel RealSense depth camera …` | The actual USB webcam isn't powered / plugged in. The probe deliberately skips the RealSense's UVC color stream (otherwise depth-camera frames would show in the "RGB camera" pane on the Drive / Perception screens — exactly the regression bot operators reported). | Re-plug the USB webcam (separate from the RealSense). If you genuinely want the RealSense color stream as the RGB feed (no separate webcam), set `NINA_VISION_ALLOW_REALSENSE=1`. |

If you see a numeric Argus / GStreamer error (e.g. `Argus error: 3
(INVALID_PARAMS)`) in `journalctl --user -u nina-ui-kiosk -f` or in
`~/.cache/sirena/launch.log`, that's the **Jetson CSI camera stack**
(`nvarguscamerasrc`) — not the USB pipeline. The Vision stack uses
`cv2.VideoCapture(/dev/videoN)`, not Argus. The Argus error usually
means a CSI / MIPI ribbon cable is loose or the camera isn't bound
to the right ISP. Reseat the ribbon and reboot; the USB Vision
stack is unaffected.

To enumerate what the bot actually sees on its USB ports:

```bash
ls -la /dev/video*                     # which V4L2 nodes exist
v4l2-ctl --list-devices                # which device a USB cam is bound to
v4l2-ctl -d /dev/video3 --all | head   # confirm the node accepts ioctls
ffplay /dev/video3                     # full-screen live preview (Ctrl-C to quit)
```

#### 5.3.7 Live perception view (LiDAR + RGB + Depth, side-by-side)

The Nina app ships a dedicated **Perception** screen (sidebar:
`⊙ Perception`, between Vision and Map) that shows what every
forward-looking sensor is publishing **right now** in three panes:

* **LiDAR pane** — the same BreezySLAM occupancy grid the Map screen
  draws, with Nina's pose triangle in red. Updates as the SLAM worker
  ingests scans.
* **RGB pane** — the live USB camera feed (same `VisionWorker` the
  Vision and Drive screens use). The Drive screen also shows this
  feed in its Front-camera card so manual driving stays first-person.
* **Depth pane** — RealSense D435 depth, JET-coloured (red = close,
  blue = far, BLACK = no return / out of range). Below the image is a
  numeric overlay showing the SAME `forward_min / left_min / right_min`
  values the autonomy stack consumes (`F: 1.42 m   L: 0.62 m   R: 0.31 m`).

The screen is read-only except for an **Autonomous mode** toggle at
the bottom (mirrored on Map and Drive — flipping it from any of the
three reflects in the others). Use Perception during autonomy to
verify *why* the bot turned the way it did against ground-truth
sensor data.

The depth camera is opened via a refcount on `AutonomyController`
(`acquire_depth()` / `release_depth()`), so the Perception screen
keeps the D435 open for visualization even when autonomy is OFF —
and a later autonomy-enable doesn't try to re-open the busy device.
Visualization (cv2 colorize, ~5–10 ms / frame on Jetson Nano) is
toggled on only while the Perception screen is the visible screen.

#### 5.3.8 Health-screen cross-check

Open **Health** while autonomy is running. The new perception rows
should all show **OK** (or at minimum a useful detail string):

* `Lidar (RPLIDAR A1)` — `RPLIDAR A1 @ /dev/ttyUSB0 / scanning`
* `Depth camera (D435)` — `D435 640x480@15fps`
* `IR cliff (GP2Y0E02B)` — `not detected` if you haven't installed
  the IR yet (that's PENDING/WARN, not ERROR — known no-IR build)
* `Ultrasonic (HC-SR04)` — `0/4 channels up` until you install them

If any of those four shows ERROR with a Python traceback in the
detail string, the message itself is the bug report — paste it and
the relevant `.venv-ui/bin/python -m sirena_ui` console log into
your issue.

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

# Both should autostart on every Pi reboot - verify without rebooting:
systemctl is-enabled pigpiod           # expect "enabled"
systemctl is-enabled motor-bridge      # expect "enabled"
systemctl is-active  pigpiod           # expect "active"
systemctl is-active  motor-bridge      # expect "active"

# Belt-and-braces - actually power-cycle the Pi and re-check after login:
sudo reboot
# (after SSH back in, ~30 s later)
systemctl is-active motor-bridge
journalctl -u motor-bridge -b --no-pager | head -20
# expect "[INFO] pigpiod is running" then "READY" within the first 10 lines

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

### Troubleshooting: "Sorry, Ubuntu 22.04 has experienced an internal error" pop-up

Symptom: an Ubuntu apport crash dialog appears over the kiosk GUI on
most reboots, often reporting `gnome-session-binary crashed with
SIGABRT in g_assertion_...`.

This is **not Nina** — it's the GNOME session manager itself
asserting. On JetPack 6 (Ubuntu 22.04 arm64) the kiosk launcher's
`xrandr` mode-switch on every launch occasionally tickles a known
mutter / gnome-session race.

Two fixes, both already in the repo:

1. `scripts/launch-sirena.sh` is now idempotent — if the panel is
   already at 1024 × 600 (`xrandr --query | awk '/\*current/'` returns
   `1024x600`), the launcher leaves xrandr alone instead of re-adding
   the CVT modeline and re-applying the same mode. After the first
   successful boot, no further xrandr calls happen on this Jetson.
2. `scripts/install-nina-ui-kiosk.sh` writes
   `~/.config/autostart/apport-gtk.desktop` with `Hidden=true`,
   which shadows the system-wide `/etc/xdg/autostart/apport-gtk.desktop`
   so apport's pop-up dialog does not appear on the kiosk panel.
   `apport.service` still runs and crash reports still get captured
   to `/var/crash/` — only the GUI dialog is suppressed.

To re-enable apport pop-ups on a given Jetson (e.g. for debugging),
delete `~/.config/autostart/apport-gtk.desktop` or set
`X-GNOME-Autostart-enabled=true` in it.

To pull the captured crash reports for analysis:

```bash
ls -lt /var/crash/ | head
sudo apport-cli /var/crash/_usr_libexec_gnome-session-binary.<id>.crash
```

### Troubleshooting: GUI 134s on launch with "Could not load the Qt platform plugin 'xcb'"

Symptom: launching the GUI (kiosk OR `python3 -m sirena_ui` from a
terminal) prints

```
qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in
"/home/.../site-packages/cv2/qt/plugins" even though it was found.
This application failed to start because no Qt platform plugin could
be initialized.
Aborted (core dumped)
```

and exits with code 134 (SIGABRT).

Root cause: the pip wheel **`opencv-python`** (NOT
`opencv-python-headless`, which is what we declare in the requirements
files) ships its own Qt5 platform plugins under `cv2/qt/plugins/`. Once
`cv2` is imported, Qt scans that directory first and tries to load
*its* `libqxcb.so`, which is built against a different Qt5 minor and
crashes when wired into the system PyQt5 runtime.

Two fixes — either is sufficient, both is best:

1. **Operator-side cleanup** (do this once on every Jetson):
   ```bash
   pip uninstall -y opencv-python opencv-python-headless
   pip install opencv-python-headless          # the ONE that doesn't bundle Qt
   ```
2. **Launcher-side belt-and-braces** (already in `scripts/launch-sirena.sh`):
   the launcher pins `QT_QPA_PLATFORM_PLUGIN_PATH` to the system
   PyQt5 plugin dir before exec-ing python, so Qt looks there first
   and never visits cv2's broken copy. Logged as `[qt] pinned ...`
   in `~/.cache/sirena/launch.log`.

Verify either fix worked:

```bash
python3 -c "import cv2; print(cv2.__file__)"
# If the path is …/site-packages/cv2/__init__.py and `ls` of that
# folder has NO `qt/` subfolder, you're on opencv-python-headless. Good.

tail -n 40 ~/.cache/sirena/launch.log
# Look for: "[qt] pinned QT_QPA_PLATFORM_PLUGIN_PATH=/usr/lib/.../qt5/plugins"
```

### Troubleshooting: GUI looks stretched / overflows the panel on boot

Symptom: the kiosk-launched GUI is huge and parts run off the screen,
but `python3 -m sirena_ui` from a terminal looks correct.

Root cause: the panel is being driven at >1024 × 600 (typically 1920 × 1080
from a generic EDID + the panel's internal scaler). Qt's
`showFullScreen()` happily fills that whole virtual surface; the
sirena_ui layouts are designed for 1024 × 600 and overflow.

Diagnose:

```bash
# What X11 actually thinks the panel is right now:
xrandr

# What size Qt grabbed when the kiosk app launched (look for "[kiosk] screen=..."):
tail -n 50 ~/.cache/sirena/launch.log
```

A correct kiosk launch logs something like:

```
[panel] forced HDMI-0 -> 1024x600 (existing mode)
[kiosk] screen='HDMI-0' geometry=1024x600 window=1024x600 devicePixelRatio=1.0
```

If the `[panel]` line is missing or warns, install xrandr
(`sudo apt install -y x11-xserver-utils`), restart the kiosk
(`systemctl --user restart nina-ui-kiosk`), and re-check the log. If
the `[kiosk] screen=` line still reports anything other than
`1024x600`, the panel won't accept the mode — work around it by
forcing a panel-friendly mode in `/etc/X11/xorg.conf.d/` or by editing
the `_force_panel_resolution_1024x600` helper in
`scripts/launch-sirena.sh` to use the modeline your panel does
support.

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
│   ├── sensors/            slamtec_s2e (default), rplidar_a1 (legacy),
│   │                       hcsr04, gp2y0e02b, realsense_d435,
│   │                       lidar_factory (model dispatch)
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
