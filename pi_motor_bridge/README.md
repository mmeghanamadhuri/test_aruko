# Sirena Nina motor bridge (Raspberry Pi)

This directory contains the Raspberry-Pi-side software for the Nina bot.
The Pi is the **dedicated motor controller**: it owns the two
JYQD_V7.3E2 BLDC drivers and nothing else. All other logic (GUI,
vision, autonomy, sensors) stays on the Jetson Orin Nano. The Jetson
sends short ASCII commands over a serial link, and `motor_bridge.py`
on the Pi executes them.

```
┌──────────────────────────┐                     ┌──────────────────────┐
│ Jetson Orin Nano         │                     │ Raspberry Pi         │
│   GUI / vision / nav     │  ──── serial ───>   │   pigpiod            │
│   sensors / SLAM         │     115200 8N1      │   motor_bridge.py    │
│                          │                     │   navigation_bldc.py │
│   RemoteNavigationMgr    │  <── ack/event ──   │   ─────► JYQD x2     │
└──────────────────────────┘                     └──────────────────────┘
```

Why this split: the Jetson Orin Nano's general-purpose GPIOs couldn't
cleanly drive the JYQD opto-isolated direction inputs (voltage
collapsed under load, several pads were claimed by alt-functions or
just dead). The Pi GPIOs drive them fine - the user's prior RPi
prototype proved it. Rather than fight the Jetson with level shifters,
we let the Pi do what it's good at.

The serial link is **either** a USB-to-TTL adapter (CP2102 / FT232 -
NOT PL2303) plugged into a Jetson USB port, **or** a 3-wire crossover
between the two boards' 40-pin UARTs. See section 0 below for which
to pick on fresh hardware.

---

## Files

| File | Purpose |
|---|---|
| `navigation_bldc.py` | Pi GPIO/PWM helpers (the proven prototype reference) |
| `motor_bridge.py`    | Serial daemon. Listens on `/dev/serial0`, dispatches ASCII commands |
| `serial_test.py`     | Stand-alone CLI to verify the link / drive motors by hand |
| `motor-bridge.service` | systemd unit for unattended boot |
| `install_service.sh` | Copies files to `/opt/sirena/pi_motor_bridge` and enables the service |
| `PINMAP.md`          | JYQD-to-Pi wiring table (BCM and physical pin numbers) |

---

## 0. From-scratch bring-up on fresh hardware

Use this section if you have a brand-new Jetson Orin Nano + brand-new
Raspberry Pi pair and need to get from "boxes on the desk" to "GUI
drives the wheels". It's the distilled checklist from the very first
board bring-up, including every pothole we hit on Bookworm + JetPack.

If you already have a working setup, skip to section 1; everything
below 0 is the focused reference for individual concerns.

### 0.0 Pick the link type

Two ways the Jetson can talk to the Pi. Pick one **before** wiring:

| Link | Pros | Cons | Use when |
|---|---|---|---|
| **Direct UART crossover** (40-pin to 40-pin) | No extra hardware. Works on any Jetson kernel. | Needs `jetson-io.py` + `disable-bt` overlay. Sensitive to wire integrity. | Default. Recommended on Orin Nano. |
| **USB-to-TTL adapter** (CP2102 or FT232) | Plug-and-play once driver loads. Easier to swap cables. | JetPack 5.x kernel ships only `cp210x.ko` and `ftdi_sio.ko` - **PL2303 and CH340 will not work**. Buy a known-good CP2102 or FT232 ($3-8). | You have a CP2102 / FT232 in hand and want zero-config. |

> The PL2303 chip is the most common cheap USB-TTL adapter, and it
> simply will not work on JetPack's Linux kernel. Don't buy one for
> this project.

### 0.1 Hardware list

- Jetson Orin Nano dev kit (any storage, JetPack 5.x or 6.x).
- Raspberry Pi 4 with Bookworm 64-bit (Lite or Desktop). Pi 3 / Zero 2 W
  also fine. **Pi 5 is NOT supported** - `pigpio` doesn't run on the Pi 5
  GPIO controller.
- 3 jumper wires (female-female dupont) for the direct-UART path,
  or a CP2102 / FT232 USB-to-TTL adapter for the USB path.
- 2x JYQD_V7.3E2 BLDC drivers + motors + 24V battery.
- Network access to both boards (SSH or local terminal).

### 0.2 Raspberry Pi setup

#### a) OS install + first boot

Flash Bookworm 64-bit with `rpi-imager`. Set hostname / user / SSH /
Wi-Fi in the imager's "OS customization" panel. Boot, SSH in, update:

```bash
sudo apt update && sudo apt upgrade -y
sudo reboot
```

#### b) Enable the UART, disable serial-console login

```bash
sudo raspi-config
# 3 Interface Options
#   I6 Serial Port
#     "Login shell over serial?"        -> No
#     "Serial port hardware enabled?"   -> Yes
# Finish; reboot when asked.
```

#### c) Force PL011 onto pins 8/10 (not the mini UART)

This is **critical**. By default `serial0` symlinks to `ttyS0` (the
mini UART), whose clock jitters with CPU load and produces garbled
bytes at 115200. Disabling Bluetooth frees the PL011 (`ttyAMA0`) up
onto the 40-pin header.

```bash
echo "dtoverlay=disable-bt" | sudo tee -a /boot/firmware/config.txt
sudo reboot
```

**Verify after reboot:**

```bash
ls -l /dev/serial0
# expect:  /dev/serial0 -> ttyAMA0       (NOT -> ttyS0)
```

Side effect: onboard Bluetooth is disabled. Almost certainly fine.

#### d) Install pigpiod from source + pyserial via apt

(Bookworm dropped the `pigpio` daemon from apt; only the client
library and tools remain. PEP 668 also blocks `pip install`.)

```bash
sudo apt update
sudo apt install -y python3-serial python3-pigpio \
                    python3-setuptools python3-full \
                    build-essential wget

cd /tmp
wget https://github.com/joan2937/pigpio/archive/refs/tags/v79.tar.gz
tar zxf v79.tar.gz
cd pigpio-79
make -j"$(nproc)"
sudo make install
sudo ldconfig

# Drop a systemd unit (the v79 source install doesn't reliably do this
# on Bookworm/Trixie). The `-l` flag restricts to localhost.
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

sudo systemctl daemon-reload
sudo systemctl enable --now pigpiod
```

**Verify:**

```bash
systemctl status pigpiod --no-pager
python3 -c "import pigpio, serial; print('pigpio', pigpio.VERSION, 'pyserial', serial.VERSION)"
python3 -c "import pigpio; pi=pigpio.pi(); print('connected:', pi.connected); pi.stop()"
# expect:  connected: True
```

#### e) Get the bridge code onto the Pi

```bash
cd ~/Desktop
git clone https://github.com/Sirena-Technologies/Nvidia-jetson-platform.git
cd Nvidia-jetson-platform
git checkout feature/nina-app
cd pi_motor_bridge
ls   # motor_bridge.py, navigation_bldc.py, serial_test.py, install_service.sh, PINMAP.md
```

#### f) Wire JYQDs to the Pi per `PINMAP.md`

See `PINMAP.md` in this directory for the full table.

#### g) Smoke-test motors directly (no Jetson involved yet)

Proves Pi -> JYQD -> motors works in isolation:

```bash
sudo systemctl stop motor-bridge 2>/dev/null
sudo pkill -f motor_bridge.py 2>/dev/null
sudo systemctl start pigpiod

cd ~/Desktop/Nvidia-jetson-platform/pi_motor_bridge
sudo -E python3 -c "
import time, navigation_bldc as nav
assert nav.setup_gpio()
print('forward 2s'); nav.set_wheels(20, 'front', 20, 'front'); time.sleep(2)
print('stop');       nav.soft_stop();                          time.sleep(1)
print('backward 2s'); nav.set_wheels(20, 'back', 20, 'back');  time.sleep(2)
print('done');       nav.emergency_stop()
"
```

Both wheels should run forward, stop, then backward. If a wheel spins
the wrong way, leave it - we'll fix in software via env var on the
Jetson side later.

### 0.3 Jetson Orin Nano setup

#### a) OS install + first boot

Flash JetPack 5.x or 6.x with the SDK Manager. First-boot wizard, set
user. Update:

```bash
sudo apt update && sudo apt upgrade -y
```

#### b) Add yourself to `dialout`

So you don't need `sudo` for `/dev/ttyTHS*` or `/dev/ttyUSB*`:

```bash
sudo usermod -aG dialout $USER
newgrp dialout    # or log out + log back in
groups | grep dialout
```

#### c) For the direct-UART path: enable UART1 on pins 8/10

Without this step, `/dev/ttyTHS1` exists but isn't routed to physical
pins 8/10 of the 40-pin header.

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
```

In the TUI:

1. **Configure 40-pin expansion header**
2. Toggle **uart1** ON (often disabled by default)
3. **Save and reboot to reconfigure pins**

After reboot, **verify pin 8 idles HIGH at 3.3V** with a multimeter
(black probe on Jetson pin 39 GND):

```bash
# while this runs, probe pin 8 - should idle ~3.3V and dip during transmission
sudo bash -c 'while true; do printf "U" > /dev/ttyTHS1; sleep 0.05; done'
# Ctrl-C when done
```

If pin 8 sits at 0V regardless, that pad on this specific Orin Nano is
dead - skip to the USB-TTL path below (option (d)) and use a CP2102 /
FT232 adapter instead.

While you're in here, also disable `nvgetty` if it grabbed the UART:

```bash
sudo systemctl is-active nvgetty 2>/dev/null
sudo systemctl stop nvgetty 2>/dev/null
sudo systemctl disable nvgetty 2>/dev/null
```

#### d) For the USB-TTL path: confirm the adapter binds

Plug the adapter in, then:

```bash
ls -l /dev/ttyUSB*
sudo dmesg | tail -10
lsusb
```

You want a `cp210x converter now attached to ttyUSB0` or `ftdi_sio
converter now attached to ttyUSB0` line in `dmesg`. If you see
`Prolific PL2303` in `lsusb` but no `/dev/ttyUSB*` device, this Jetson
kernel doesn't have the `pl2303` driver - you need a different
adapter (CP2102 / FT232). Don't waste time on this.

To confirm what USB-serial drivers your kernel actually has:

```bash
ls /lib/modules/$(uname -r)/kernel/drivers/usb/serial/
# expect: cp210x.ko, ftdi_sio.ko, usbserial.ko (and option/usb_wwan)
# notably absent on JetPack 5.x: pl2303.ko, ch341.ko
```

#### e) Install Python deps

```bash
sudo apt install -y python3-pip python3-serial git
# only if you'll launch the GUI:
sudo apt install -y python3-pyqt5 python3-numpy
```

#### f) Get the project on the Jetson

```bash
cd ~
git clone https://github.com/Sirena-Technologies/Nvidia-jetson-platform.git
cd Nvidia-jetson-platform
git checkout feature/nina-app
```

#### g) Set the navigation env vars permanently

For the **direct-UART** path:

```bash
{
  echo ''
  echo '# Sirena Nina - talk to Pi motor bridge over the 40-pin UART'
  echo 'export NINA_NAV_MODE=remote'
  echo 'export NINA_NAV_REMOTE_PORT=/dev/ttyTHS1'
  echo 'export NINA_NAV_REMOTE_BAUD=115200'
} >> ~/.bashrc
source ~/.bashrc
```

For the **USB-TTL** path, swap the port:

```bash
echo 'export NINA_NAV_REMOTE_PORT=/dev/ttyUSB0' >> ~/.bashrc
```

Add wheel-direction inverts later if you find a wheel spins backwards
during section 0.5 below:

```bash
echo 'export NINA_NAV_INVERT_LEFT=1'  >> ~/.bashrc      # only if needed
echo 'export NINA_NAV_INVERT_RIGHT=1' >> ~/.bashrc      # only if needed
```

### 0.4 Wire the Jetson <-> Pi serial link

#### Direct-UART path (3 wires)

Power both boards **off**, then:

```
Jetson Orin Nano (40-pin)        Raspberry Pi (40-pin)
=========================        =====================
pin  8  (UART1_TX, ttyTHS1)  --> pin 10 (BCM 15, RXD,  serial0)
pin 10  (UART1_RX, ttyTHS1)  <-- pin  8 (BCM 14, TXD,  serial0)
pin  6  (GND)                <-> pin  6 (GND)        (or pin 39, doesn't matter)
```

Crossover - Jetson TX -> Pi RX. Don't run any 3.3V/5V wires across.

Pin 1 on the Orin Nano dev kit is at the **camera-connector end** of
the 40-pin header. If your pin 1 reads 0V and pin 39 reads 3.3V on a
multimeter, the header is mentally flipped - re-orient and re-count.

#### USB-TTL path (USB cable + 3 wires)

```
USB-to-TTL adapter        Raspberry Pi 40-pin header
==================        ==========================
   USB    -> Jetson USB-A
   TX     -> pin 10 (BCM 15, RXD)
   RX     -> pin  8 (BCM 14, TXD)
   GND    -> pin  6 (any GND)
   VCC    -> NOT CONNECTED (each board self-powered)
```

Power both boards back on.

### 0.5 End-to-end verification

The same three tests apply regardless of which link type you picked.
Substitute `/dev/ttyTHS1` <-> `/dev/ttyUSB0` to match your setup.

#### a) Loopback (proves the wire and clock)

**Pi:**

```bash
sudo systemctl stop motor-bridge 2>/dev/null
sudo pkill -f motor_bridge.py 2>/dev/null
cd ~/Desktop/Nvidia-jetson-platform/pi_motor_bridge
python3 serial_test.py loopback --port /dev/serial0
```

**Jetson:**

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=1)
time.sleep(0.3); s.reset_input_buffer()
for i in range(5):
    s.write(b'jetson-ping-%d\n' % i); s.flush()
    print('->', i, 'got:', s.readline())
    time.sleep(0.2)
"
```

**Expected:** all five `got: b'jetson-ping-N\n'` on the Jetson AND
all five `jetson-ping-N` lines on the Pi loopback. Clean.

If you see garbled bytes (e.g. `@`), the Pi is on the mini UART -
re-check step 0.2.c. If you see total silence, re-seat the jumpers /
re-verify pin 8 voltage on the Jetson. Ctrl-C the Pi loopback when
this passes.

#### b) Bridge protocol PING (proves the daemon)

**Pi:**

```bash
cd ~/Desktop/Nvidia-jetson-platform/pi_motor_bridge
sudo python3 motor_bridge.py --verbose
# wait for: [BRIDGE] Listening on /dev/serial0 @ 115200 8N1
```

**Jetson:**

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=1)
time.sleep(0.3); s.reset_input_buffer()
s.write(b'PING\n'); s.flush()
print('reply:', s.readline())
"
# expect:  reply: b'PONG\n'
```

You may see `b'READY\n'` first (boot greeting); re-run for the `PONG`.

#### c) Drive the motors via the bridge (proves the whole stack)

**Jetson** (Pi bridge still running):

```bash
cd ~/Nvidia-jetson-platform
PYTHONPATH=. python3 -m nina.app.nav_bridge_test --port /dev/ttyTHS1 --speed 25 --duration 3
```

Sequence: ping -> forward 3s -> stop -> backward 3s -> stop ->
in-place left turn -> in-place right turn. Watch the wheels and the
bridge log on the Pi.

If a wheel runs backwards, set `NINA_NAV_INVERT_LEFT=1` (or
`_RIGHT=1`) in `~/.bashrc` on the Jetson and re-run. No Pi-side
change needed.

#### d) GUI test (the real victory lap)

**Pi:** keep `motor_bridge.py --verbose` running.

**Jetson:**

```bash
cd ~/Nvidia-jetson-platform

# Workaround for OpenCV-vs-PyQt5 plugin conflict (see troubleshooting):
export QT_QPA_PLATFORM_PLUGIN_PATH=/usr/lib/aarch64-linux-gnu/qt5/plugins/platforms

PYTHONPATH=. python3 -m sirena_ui
```

Switch to the **Drive** screen. Press-and-hold Forward / Back / Left /
Right buttons (or W / A / S / D, Space for STOP). The bridge log
should scroll at ~10 Hz with `SET ...` lines. Permanent fix for the
OpenCV/Qt conflict (do once):

```bash
pip uninstall -y opencv-python opencv-contrib-python
pip install --user opencv-python-headless
```

### 0.6 Auto-start the Pi bridge on boot

Once 0.5.c passes, install the bridge as a systemd service so the bot
is operational the moment power comes up:

```bash
# On the Pi
cd ~/Desktop/Nvidia-jetson-platform/pi_motor_bridge
sudo bash install_service.sh
sudo systemctl status motor-bridge
```

After a cold reboot the bridge will be listening on `/dev/serial0`
before you log in. The Jetson can connect any time.

### 0.7 What to do when something doesn't work

See section 6 (Troubleshooting). The most common gotchas on fresh
hardware, in order of frequency:

1. Pi `serial0` -> `ttyS0` instead of `ttyAMA0` (mini UART). Fix: 0.2.c.
2. Jetson `/dev/ttyTHS1` exists but pin 8 is dead. Fix: 0.3.c jetson-io.
3. PL2303 USB-TTL adapter doesn't enumerate `/dev/ttyUSB*`. Fix: get a CP2102 / FT232.
4. `pigpiod.service does not exist`. Fix: 0.2.d unit file block.
5. GUI: "Could not load Qt platform plugin xcb". Fix: 0.5.d export.

---

## 1. Physical wiring

### Pi <-> JYQD (motor side)

See `PINMAP.md` for the full wiring table. This matches the proven RPi
prototype 1:1, so if your Pi was driving these motors before, **don't
move any wires** - just plug them back in.

### Pi <-> Jetson (command link)

Two options. On Jetson Orin Nano the **direct UART crossover** is the
recommended default (no driver hunting, no $3 part), and section 0 has
the full setup. The **USB-to-TTL adapter** path works too if you have
a CP2102 or FT232 in hand - just be aware that the JetPack kernel
ships only `cp210x.ko` and `ftdi_sio.ko`, so PL2303 / CH340 adapters
will enumerate but **not** create a `/dev/ttyUSB*` device.

#### Direct UART crossover (3 wires - default)

```
Jetson Orin Nano (40-pin)        Raspberry Pi (40-pin)
=========================        =====================
pin  8 (UART1_TX, ttyTHS1)  -->  pin 10 (BCM 15, RXD,  serial0)
pin 10 (UART1_RX, ttyTHS1)  <--  pin  8 (BCM 14, TXD,  serial0)
pin  6 (GND)                <->  pin  6 (GND)
```

Crossover - Jetson TX -> Pi RX, Jetson RX -> Pi TX. On the Jetson the
port is `/dev/ttyTHS1`. On the Pi it's `/dev/serial0`.

Two prep steps are required (see section 0 for full detail):
1. **On the Pi**: `dtoverlay=disable-bt` in `/boot/firmware/config.txt`
   so `/dev/serial0` symlinks to `ttyAMA0` (PL011) instead of `ttyS0`
   (mini UART). The mini UART can't reliably do 115200.
2. **On the Jetson**: `sudo /opt/nvidia/jetson-io/jetson-io.py` to
   enable `uart1` on the 40-pin header so `/dev/ttyTHS1` is actually
   wired to pins 8/10. By default the pad is left as GPIO.

#### USB-to-TTL adapter (CP2102 / FT232 only)

```
USB-to-TTL adapter        Raspberry Pi 40-pin header
==================        ==========================
   USB    -> Jetson USB-A
   TX     -> pin 10 (BCM 15, RXD)
   RX     -> pin  8 (BCM 14, TXD)
   GND    -> pin  6 (any GND on the Pi)
   VCC    -> NOT CONNECTED (each board has its own 5V supply)
```

Three wires plus the USB cable. The adapter shows up on the Jetson as
`/dev/ttyUSB0` (or `/dev/ttyUSB1` if Dynamixel already took USB0).

> **Heads-up**: the adapter's TX must reach the Pi's RX, and its RX
> must reach the Pi's TX. They're a crossover. Don't run VCC across.

> **Don't buy a PL2303 or CH340 adapter**. They're the cheapest and
> most common, but the JetPack kernel doesn't ship their drivers, so
> the device will appear in `lsusb` but no `/dev/ttyUSB*` will be
> created. Pay the extra dollar for a CP2102 (Silicon Labs) or FT232
> (FTDI) and save yourself an hour.

---

## 2. One-time Pi setup

Run these on the Pi.

### 2.1 Enable the UART, disable the serial login console

```bash
sudo raspi-config
# 3 Interface Options
#   I6 Serial Port
#     "Login shell over serial?"     -> No
#     "Serial port hardware enabled?"-> Yes
# Finish, reboot when asked.
```

After reboot, `/dev/serial0` exists and the kernel is not stealing
characters off it.

### 2.2 Install pigpio + pyserial

> **Pi 5 warning**: `pigpio` does **not** work on the Raspberry Pi 5
> (different GPIO controller). This bridge targets the Pi 4 / Pi 3 /
> Zero 2 W. If you're on a Pi 5, swap to `lgpio` first - that's a
> separate port, not covered here.

On modern Raspberry Pi OS (Bookworm and newer) the `pigpio` daemon was
dropped from the apt repos, and PEP 668 blocks `pip install` into the
system Python. So the install is a touch longer than it used to be:

```bash
sudo apt update
# pyserial + the pigpio Python client + build deps
sudo apt install -y python3-serial python3-pigpio \
                    python3-setuptools python3-full \
                    build-essential wget

# Build pigpiod from source (~1-2 min on a Pi 4)
cd /tmp
wget https://github.com/joan2937/pigpio/archive/refs/tags/v79.tar.gz
tar zxf v79.tar.gz
cd pigpio-79
make -j"$(nproc)"
sudo make install
sudo ldconfig

# Drop a systemd unit (the v79 source install doesn't reliably do this
# on Bookworm/Trixie - the binary lands but no service file does).
# `-l` makes pigpiod listen only on localhost, which is all we need.
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

sudo systemctl daemon-reload
sudo systemctl enable --now pigpiod
```

Verify both libraries are importable and `pigpiod` is up:

```bash
systemctl status pigpiod --no-pager
python3 -c "import pigpio, serial; print('pigpio', pigpio.VERSION, 'pyserial', serial.VERSION)"
python3 -c "import pigpio; pi=pigpio.pi(); print('connected:', pi.connected); pi.stop()"
```

The second command should print `connected: True`. If it prints `False`,
the daemon isn't reachable - re-check `systemctl status pigpiod`.

> If you ever do need to install a Python package that isn't apt-packaged,
> use a venv (`python3 -m venv ~/.sirena-venv && source
> ~/.sirena-venv/bin/activate`) instead of `pip3 install --break-system-packages`,
> which can corrupt the system Python.

### 2.3 Get the bridge files onto the Pi

If the whole `Nvidia-jetson-platform` repo is checked out on the Pi
(e.g. by `scp` or a git clone):

```bash
cd Nvidia-jetson-platform/pi_motor_bridge
```

If you only want to copy this one directory across, scp it from the
Jetson:

```bash
# from the Jetson
scp -r ~/Nvidia-jetson-platform/pi_motor_bridge pi@<pi-ip>:~/
# then on the Pi
cd ~/pi_motor_bridge
```

---

## 3. Smoke tests (do these in order)

### 3.1 Verify the cable BEFORE wiring up motors

Power off the JYQDs (or just leave the motor phase plugs unplugged) so
nothing physical moves while you debug serial.

On the Pi:

```bash
python3 serial_test.py loopback --port /dev/serial0
```

On the Jetson, install pyserial if you haven't (`sudo apt install -y
python3-serial`) then (substitute `/dev/ttyTHS1` for `/dev/ttyUSB0` if
you're on the direct-UART path):

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
time.sleep(0.2); s.reset_input_buffer()
s.write(b'hello\n')
print(s.readline())
"
```

You should see `b'hello\n'` printed on the Jetson (loopback echoed it
back) **and** `hello` printed on the Pi terminal. If you see neither,
the cable / port / baud is wrong - fix that before going further.

### 3.2 Start the bridge

```bash
sudo python3 motor_bridge.py --verbose
```

You should see:

```
[GPIO] Setup complete
[BRIDGE] Listening on /dev/serial0 @ 115200 8N1
[BRIDGE] Watchdog timeout: 1.5s
```

The Pi sends a `READY\n` line on the wire as soon as it's listening.

### 3.3 Drive the motors by hand

In a second terminal **on the Pi** (any terminal, the serial port
isn't busy from the Pi's own side):

```bash
# Don't run this here - the bridge is already holding /dev/serial0.
# Use the Jetson side instead:
```

So instead, on the Jetson:

```bash
python3 -m nina.app.nav_bridge_test --port /dev/ttyUSB0 --speed 25 --duration 3
```

This pings, then forward 3 s, stop, backward 3 s, stop, left turn,
right turn. Watch the wheels.

If anything misbehaves (wheel spins wrong way, doesn't move, etc.),
fall back to manual mode on the Jetson:

```bash
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyUSB0', 115200, timeout=1); time.sleep(0.2); s.reset_input_buffer()
def send(line):
    s.write((line+'\n').encode()); s.flush()
    print('>>', line, '<<', s.readline().decode().strip())
send('PING')
send('SET F 25 F 25')   # both forward
import time; time.sleep(2)
send('STOP')
"
```

---

## 4. Auto-start on boot (optional, for production)

```bash
cd pi_motor_bridge
sudo bash install_service.sh
```

This copies the files to `/opt/sirena/pi_motor_bridge`, drops the unit
file in `/etc/systemd/system`, enables both `pigpiod` and
`motor-bridge`, and starts them. Verify:

```bash
sudo systemctl status motor-bridge
sudo journalctl -u motor-bridge -f
```

Boot the Pi cold and the bridge will be up and listening before you
log in. The Jetson can then connect any time.

---

## 5. Wire protocol (for reference)

ASCII over 115200 8N1, newline-terminated.

| Direction | Line                                  | Reply       | Effect                                    |
|-----------|----------------------------------------|-------------|-------------------------------------------|
| J -> Pi   | `PING`                                 | `PONG`      | health-check                              |
| J -> Pi   | `SET <ldir> <lspeed> <rdir> <rspeed>`  | `OK`/`ERR`  | per-wheel direction + speed               |
| J -> Pi   | `STOP`                                 | `OK`        | PWM=0, EL stays HIGH (chip armed)         |
| J -> Pi   | `ESTOP`                                | `OK`        | PWM=0, EL LOW (chip disabled, no torque)  |
| J -> Pi   | `LED <CONNECTED|ERROR|WAITING|OFF>`    | `OK`/`ERR`  | status LED                                |
| Pi -> J   | `READY`                                | -           | bridge has finished GPIO init             |
| Pi -> J   | `EVT WATCHDOG`                         | -           | bridge stopped wheels because Jetson went silent while moving |

`<ldir>` / `<rdir>` are `F` (forward) or `B` (backward). Speeds are
integers 0..100.

The bridge's **watchdog** stops the wheels if no command arrives for
`--watchdog` seconds (default 1.5) **while the wheels are commanded
to move**. Send a fresh `SET ...` at >= 1 Hz from the Jetson while
driving, or the bot will park itself - by design.

---

## 6. Troubleshooting

| Symptom                                      | Likely cause                                                    |
|----------------------------------------------|-----------------------------------------------------------------|
| `[FATAL] Cannot open /dev/serial0`           | UART not enabled in raspi-config, or device is `/dev/ttyAMA0`   |
| `[FATAL] pigpio connection failed`           | `pigpiod` isn't running -> `sudo systemctl start pigpiod`       |
| `apt: Package 'pigpio' has no installation candidate` | Pi OS Bookworm/Trixie dropped the daemon from apt - build from source per section 2.2 |
| `Failed to enable unit: Unit pigpiod.service does not exist` | The v79 source install didn't drop a systemd unit - create `/etc/systemd/system/pigpiod.service` per section 2.2 |
| `pip: error: externally-managed-environment` | PEP 668 - install with apt (`python3-serial`, `python3-pigpio`) or use a venv, never `--break-system-packages` |
| Loopback test sees nothing                   | TX/RX swapped, wrong adapter port, GND missing                  |
| `READY` arrives but `PING` times out         | Pi -> Jetson direction is broken (Pi TX -> adapter RX wrong)    |
| `OK` echoes but motors don't move            | JYQD power off, motor phase cable unplugged, EL screw loose. (The bridge already auto-applies the JYQD startup kick on every transition out of stop; if it's still silent the chip isn't seeing power or its phase outputs aren't reaching the motor.) |
| `EVT WATCHDOG` keeps firing during driving   | Jetson isn't sending continuation `SET` calls fast enough. The Nina GUI's `DriveController` heartbeats SETs every 300 ms while a button is held - if you see this firing, check the GUI thread isn't starved (e.g. heavy SLAM tick blocking the worker queue) or raise `--watchdog` on the bridge |
| Wheels stop after ~1.5 s when holding a D-pad / arrow key | Pre-heartbeat behaviour. The fix lives in `sirena_ui/workers/drive_controller.py` - confirm the deployed Jetson code includes `_heartbeat_loop` (`grep -q _heartbeat_loop ...`). If you're driving from a custom script, your script needs to re-issue SET at >1 Hz |
| One wheel spins backwards                    | set `NINA_NAV_INVERT_LEFT=1` or `NINA_NAV_INVERT_RIGHT=1` on the Jetson (no Pi-side change needed) |
| Loopback gets garbled bytes (e.g. only `@`)  | Pi `serial0` is on the mini UART (`ttyS0`) - clock jitter at 115200. Add `dtoverlay=disable-bt` to `/boot/firmware/config.txt` and reboot, then `ls -l /dev/serial0` should say `-> ttyAMA0` (PL011) |
| Jetson sees no `/dev/ttyUSB*` for a USB-TTL adapter | JetPack kernel ships only `cp210x.ko` and `ftdi_sio.ko` - PL2303 / CH340 won't bind. Get a CP2102 or FT232. Verify with `ls /lib/modules/$(uname -r)/kernel/drivers/usb/serial/` |
| Jetson `/dev/ttyTHS1` exists but pin 8 idles at 0V | Pinmux on the 40-pin header has UART1 disabled. Run `sudo /opt/nvidia/jetson-io/jetson-io.py` -> Configure 40-pin -> enable `uart1` -> Save & reboot |
| Pin 8 still 0V after enabling uart1 in jetson-io | Dead pad on this specific Orin Nano. Fall back to a CP2102 / FT232 USB-TTL adapter |
| Jetson `Permission denied` opening `/dev/ttyTHS1` or `/dev/ttyUSB0` | User not in `dialout` group: `sudo usermod -aG dialout $USER && newgrp dialout` |
| GUI: `Could not load the Qt platform plugin "xcb"` | OpenCV's bundled Qt plugins clash with system PyQt5. Quick fix: `export QT_QPA_PLATFORM_PLUGIN_PATH=/usr/lib/aarch64-linux-gnu/qt5/plugins/platforms`. Permanent fix: `pip uninstall opencv-python opencv-contrib-python && pip install --user opencv-python-headless` |
| Wheel buzzes but doesn't rotate              | PWM duty too low to overcome static friction (≤15% on JYQDs is unreliable). Bump to ≥25%. (The bridge already kicks the JYQD with the EL/PWM edge sequence the chip needs; the warm-up itself uses 15% which is below the torque threshold by design - it's only there so the falling/rising edges that follow are clean, not to actually move the wheel.) |
| Motors only spin on the *second* movement command | This was the OLD prototype's bug - the JYQD needs an explicit EL falling-then-rising edge plus a PWM 0->N rising edge to start commutation, and the OLD code only produced both edges by accident on the second invocation. The new bridge handles this with `nav.kick_and_set()` (see `navigation_bldc.py`) on every transition out of stop or direction change. If you see this with the new bridge, check that you're actually running `motor_bridge.py` and not the old `motor_control.py` |
| Forward works but reverse stalls / jerks (one wheel or both) | Cheap BLDC hub motors with non-canonical hall-sensor wiring (ACB instead of ABC) can't commutate in reverse from a stopped rotor - the chip's fallback table only covers forward. The new bridge handles this with `nav.warm_reverse_and_set()` (see `navigation_bldc.py`) which adds a brief forward "puff" (~150 ms at 25%) before the reverse kick so the rotor has momentum the chip can latch onto. If you still see a wheel stall in reverse after that: try a **swap test** (physically swap the two JYQD drivers; if the bad-reverse symptom moves with the chip it's a chip issue, otherwise it's a motor wiring issue), then either swap that motor's bad hall + phase pairs or live with that wheel being forward-only. Tunables live in `navigation_bldc.py` (`PUFF_PWM_PERCENT`, `PUFF_FWD_SEC`, `PUFF_COAST_SEC`). |
