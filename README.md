# Sirena Nina — Jetson + Raspberry Pi robotics platform

Nina is a wheeled robot built on a two-board split: an **NVIDIA Jetson
Orin Nano** runs the GUI, vision, SLAM, autonomy and action playback,
and a **Raspberry Pi 4** is the dedicated motor controller for the
two JYQD_V7.3E2 BLDC drivers. The boards talk over a 115 200 8N1
serial link (40-pin UART crossover, or CP2102 / FT232 USB-to-TTL
adapter).

## Documentation

**[REQUIREMENTS.md](REQUIREMENTS.md)** — Single reference: hardware BOM, OS versions, Python deps, and the end-to-end bring-up checklist for fresh Jetson + Pi pair.

Deeper references for each subsystem:

- [`sirena_ui/docs/NINA_APP.md`](sirena_ui/docs/NINA_APP.md) — full feature reference for the PyQt5 cockpit (every screen, every env var, every tunable).
- [`pi_motor_bridge/README.md`](pi_motor_bridge/README.md) — Raspberry Pi bring-up walkthrough (Bookworm, pigpio, UART, every pothole).
- [`pi_motor_bridge/PINMAP.md`](pi_motor_bridge/PINMAP.md) — JYQD ↔ Pi GPIO wiring table.

## Quick layout

```
├── sirena_ui/         PyQt5 cockpit (Home, Drive, Vision, Map, Actions, Settings, Health)
├── nina/              Backend: navigation, sensors, SLAM, autonomy, action runner
├── pi_motor_bridge/   Pi-side serial daemon that owns the JYQDs
├── desktop/           Systemd user units + .desktop launcher templates
├── scripts/           Installers (kiosk autostart, FTDI udev, desktop icon)
├── tests/             Hardware-free pytest suite (mocks pigpio + serial)
└── requirements*.txt  Python deps — see REQUIREMENTS.md §4
```

## Quick start

For an end-to-end bring-up on fresh Jetson + Pi pair, follow the
checklist in **[REQUIREMENTS.md](REQUIREMENTS.md)** §5.

If both boards are already set up and you just want to run the GUI:

```bash
cd ~/Nvidia-jetson-platform
PYTHONPATH=. python3 -m sirena_ui
```

Or, to install the kiosk autostart (panel boots straight into the GUI
on every reboot):

```bash
./scripts/install-nina-ui-kiosk.sh
```
