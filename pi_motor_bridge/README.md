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
│   GUI / vision / nav     │  ── USB-UART ──>    │   pigpiod            │
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

## 1. Physical wiring

### Pi <-> JYQD (motor side)

See `PINMAP.md` for the full wiring table. This matches the proven RPi
prototype 1:1, so if your Pi was driving these motors before, **don't
move any wires** - just plug them back in.

### Pi <-> Jetson (command link)

The recommended setup is a **USB-to-TTL adapter** (CP2102, FT232,
CH340 - any cheap one). It plugs into a USB port on the Jetson and
the TTL side wires to the Pi's UART pins. This avoids any GPIO
conflicts on either board.

```
USB-to-TTL adapter        Raspberry Pi 40-pin header
==================        ==========================
   USB    -> Jetson USB-A
   TX     -> pin 10 (BCM 15, RXD)
   RX     -> pin  8 (BCM 14, TXD)
   GND    -> pin  6 (any GND on the Pi)
   VCC    -> NOT CONNECTED (each board has its own 5 V supply)
```

That's three wires plus the USB cable. The adapter shows up on the
Jetson as `/dev/ttyUSB0` (or `/dev/ttyUSB1` if Dynamixel already took
USB0).

> **Heads-up**: the adapter's TX must reach the Pi's RX, and its RX
> must reach the Pi's TX. They're a crossover.

#### Alternative (no adapter): direct UART

If you prefer four wires across the two boards (TX/RX/GND), you can
cross the GPIO UARTs directly:

```
Jetson Orin Nano (40-pin)        Raspberry Pi (40-pin)
=========================        =====================
pin  8 (BCM 14, TXD)   ----->    pin 10 (BCM 15, RXD)
pin 10 (BCM 15, RXD)   <-----    pin  8 (BCM 14, TXD)
pin  6 (GND)           <----->   pin  6 (GND)
```

On the Jetson you'd use `/dev/ttyTHS1`. On the Pi the device stays
`/dev/serial0`. This option needs the Jetson UART to not be claimed
by the kernel console - tedious to set up, so the USB adapter is
strongly preferred.

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

On the Jetson, install pyserial if you haven't (`pip3 install
pyserial`) then:

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
| `OK` echoes but motors don't move            | JYQD power off, motor phase cable unplugged, EL screw loose     |
| `EVT WATCHDOG` keeps firing during driving   | Jetson isn't sending continuation `SET` calls fast enough; raise the watchdog or have the GUI tick faster |
| One wheel spins backwards                    | set `NINA_NAV_INVERT_LEFT=1` or `NINA_NAV_INVERT_RIGHT=1` on the Jetson (no Pi-side change needed) |
