"""
Collects subsystem health for the Health screen.

For systems that have a real driver (Dynamixel bus, FTDI, settings),
we read live values. For systems that are still being integrated
(camera, lidar, IR, ultrasonic, BLDC, ESP voice) we report 'pending'
so the UI shows the right amber chip.

The collector is sync today and called on the UI thread; the checks
are cheap (file existence, Dynamixel ping count from cached health).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import List, Optional

from sirena_ui.workers.nina_service import NinaService


STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_PENDING = "pending"


@dataclass
class HealthRow:
    key: str
    label: str
    glyph: str
    detail: str
    status: str

    @property
    def is_ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def is_warn(self) -> bool:
        return self.status == STATUS_WARN

    @property
    def is_error(self) -> bool:
        return self.status == STATUS_ERROR


def collect(service: NinaService) -> List[HealthRow]:
    rows: List[HealthRow] = []

    # 1) Dynamixel bus
    health = None
    try:
        with service.bus_lock:
            if service.dxl._is_initialized:  # type: ignore[attr-defined]
                health = service.dxl.run_health_check()
    except Exception:
        health = None
    if health is None:
        rows.append(HealthRow(
            "bus", "Dynamixel bus", "\u26A1",
            "Not initialized yet", STATUS_PENDING,
        ))
    else:
        connected = bool(getattr(health, "connected", False))
        det = getattr(health, "detected_motors", 0)
        exp = getattr(health, "expected_motors", 0)
        rows.append(HealthRow(
            "bus", "Dynamixel bus", "\u26A1",
            f"{det}/{exp} motors at {service.settings.baudrate} baud",
            STATUS_OK if connected and det == exp else (STATUS_WARN if det > 0 else STATUS_ERROR),
        ))

    # 2) FTDI / serial port file
    serial_port = service.settings.serial_port
    rows.append(HealthRow(
        "ftdi", "FTDI USB-serial", "\u2706",
        f"{serial_port}",
        STATUS_OK if _device_exists(serial_port) else STATUS_ERROR,
    ))

    # 3) USB camera (placeholder until vision pipeline lands)
    rows.append(HealthRow(
        "camera", "USB Camera", "\u25CE",
        "Vision service not yet integrated",
        STATUS_PENDING,
    ))

    # 4-6) Lidar / IR / Ultrasonic
    for key, label in (("lidar", "Lidar"), ("ir", "IR sensors"), ("ultra", "Ultrasonic")):
        rows.append(HealthRow(
            key, label, "\u25A6",
            "SLAM stack not yet integrated",
            STATUS_PENDING,
        ))

    # 7) BLDC drivers
    rows.append(HealthRow(
        "bldc", "BLDC drivers", "\u2B95",
        "Drive controller not yet integrated",
        STATUS_PENDING,
    ))

    # 8) Battery
    rows.append(HealthRow(
        "battery", "Battery", "\u2615",
        "Power telemetry pending",
        STATUS_PENDING,
    ))

    # 9) Wi-Fi / network
    rows.append(HealthRow(
        "wifi", "Wi-Fi", "\u2706",
        "Connected" if _wifi_ok() else "Offline",
        STATUS_OK if _wifi_ok() else STATUS_WARN,
    ))

    # 10) ESP voice
    rows.append(HealthRow(
        "voice", "ESP Voice Module", "\u266B",
        "Voice module not yet paired",
        STATUS_PENDING,
    ))

    # 11) Disk
    free_gb, total_gb = _disk_free_gb()
    if total_gb > 0:
        warn = free_gb < (total_gb * 0.20)
        rows.append(HealthRow(
            "disk", "Disk", "\u25A6",
            f"{free_gb:.1f} GB free of {total_gb:.0f} GB",
            STATUS_WARN if warn else STATUS_OK,
        ))
    else:
        rows.append(HealthRow(
            "disk", "Disk", "\u25A6",
            "Could not read disk usage",
            STATUS_WARN,
        ))

    # 12) CPU + 13) Temperature (best-effort, optional)
    rows.append(HealthRow(
        "cpu", "CPU", "\u2699",
        f"{_cpu_load_summary()}",
        STATUS_OK,
    ))
    rows.append(HealthRow(
        "temp", "Temperature", "\u2615",
        f"{_temp_summary()}",
        STATUS_OK,
    ))

    return rows


# ---------- helpers ----------

def _device_exists(path: str) -> bool:
    try:
        from os import path as op
        return op.exists(path)
    except Exception:
        return False


def _wifi_ok() -> bool:
    """Best-effort check using the default route."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        # Doesn't actually send a packet on UDP connect; just resolves a route.
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except Exception:
        return False


def _disk_free_gb() -> tuple:
    try:
        usage = shutil.disk_usage("/")
        return (usage.free / 1e9, usage.total / 1e9)
    except Exception:
        return (0.0, 0.0)


def _cpu_load_summary() -> str:
    try:
        import os
        load = os.getloadavg()
        return f"load {load[0]:.2f}"
    except Exception:
        return "load \u2014"


def _temp_summary() -> Optional[str]:
    """Try a few common Jetson thermal zones; fall back to a placeholder."""
    candidates = [
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
        "/sys/class/thermal/thermal_zone0/temp",
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            millideg = int(raw)
            return f"{millideg / 1000.0:.0f}\u00b0C"
        except Exception:
            continue
    return "\u2014"
