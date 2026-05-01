"""
Collects subsystem health for the Health screen.

Design rule: never instantiate a worker just to ask it how it is. The
sensor singletons on `NinaService` (vision, slam, autonomy, drive) are
all *lazy* - touching the public property opens hardware (USB camera,
RPLIDAR, RealSense pipeline, GPIO/serial). The Health screen is supposed
to be a passive read-out, so we peek at the underscore-prefixed
`_vision` / `_slam` / `_autonomy` / `_drive` attributes and report
"Not opened yet" for the ones the operator hasn't kicked off.

That gives an honest 4-state model per row:

    OK       - opened and reporting healthy
    WARN     - opened but partial (e.g. some Dynamixels missing,
               low disk, network up but slow)
    ERROR    - opened and failing (camera missing, serial port gone)
    PENDING  - not opened yet (operator hasn't visited Vision /
               Map / Drive / enabled Autonomous mode)

The previous version reported every sensor as PENDING with the message
"not yet integrated" even after the integrations had landed; operators
saw a wall of amber chips and assumed the bot was unhealthy when it
was actually idle. This rewrite is the fix.
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

    # 1) Dynamixel bus -------------------------------------------------
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
            STATUS_OK if connected and det == exp
            else (STATUS_WARN if det > 0 else STATUS_ERROR),
        ))

    # 2) FTDI / serial port file ---------------------------------------
    serial_port = service.settings.serial_port
    rows.append(HealthRow(
        "ftdi", "FTDI USB-serial", "\u2706",
        f"{serial_port}",
        STATUS_OK if _device_exists(serial_port) else STATUS_ERROR,
    ))

    # 3) USB camera + vision pipeline ----------------------------------
    rows.append(_vision_row(service))

    # 4) Lidar (driven by SlamWorker) ----------------------------------
    rows.append(_lidar_row(service))

    # 5) IR cliff sensor (driven by AutonomyController) ----------------
    rows.append(_ir_row(service))

    # 6) Ultrasonic ring (driven by AutonomyController) ----------------
    rows.append(_ultrasonic_row(service))

    # 7) Depth camera (driven by AutonomyController) -------------------
    rows.append(_depth_row(service))

    # 8) BLDC drive controller -----------------------------------------
    rows.append(_drive_row(service))

    # 9) Battery -------------------------------------------------------
    rows.append(HealthRow(
        "battery", "Battery", "\u2615",
        "Power telemetry pending",
        STATUS_PENDING,
    ))

    # 10) Wi-Fi --------------------------------------------------------
    rows.append(HealthRow(
        "wifi", "Wi-Fi", "\u2706",
        "Connected" if _wifi_ok() else "Offline",
        STATUS_OK if _wifi_ok() else STATUS_WARN,
    ))

    # 11) ESP voice ----------------------------------------------------
    rows.append(HealthRow(
        "voice", "ESP Voice Module", "\u266B",
        "Voice module not yet paired",
        STATUS_PENDING,
    ))

    # 12) Disk ---------------------------------------------------------
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

    # 13) CPU + 14) Temperature ----------------------------------------
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


# ----------------------------------------------------------------------
# Per-subsystem rows
# ----------------------------------------------------------------------

def _vision_row(service: NinaService) -> HealthRow:
    # `_vision` is the private backing field of `NinaService.vision`;
    # accessing the public property would lazy-instantiate the worker
    # (and open the camera) just to ask "are you open?" - exactly the
    # opposite of what a passive health screen should do.
    vision = getattr(service, "_vision", None)
    if vision is None:
        return HealthRow(
            "camera", "USB Camera", "\u25CE",
            "Not opened yet (visit Vision tab to start)",
            STATUS_PENDING,
        )
    try:
        st = vision.status()
        camera_open = bool(getattr(st, "camera_open", False))
        face_ready = bool(getattr(st, "face_ready", False))
        object_ready = bool(getattr(st, "object_ready", False))
        msg = str(getattr(st, "message", "") or "")
    except Exception as exc:
        return HealthRow(
            "camera", "USB Camera", "\u25CE",
            f"status query failed: {exc}", STATUS_ERROR,
        )

    if not camera_open:
        return HealthRow(
            "camera", "USB Camera", "\u25CE",
            msg or "Camera not open", STATUS_ERROR,
        )
    bits: List[str] = []
    if face_ready:
        bits.append("face")
    if object_ready:
        bits.append("object")
    suffix = f" ({'+'.join(bits)})" if bits else ""
    # If the camera is up but neither model loaded, it's a partial
    # win - the operator can still see the feed but autonomy that
    # depends on detections has nothing to chew on.
    status = STATUS_OK if (face_ready or object_ready) else STATUS_WARN
    return HealthRow(
        "camera", "USB Camera", "\u25CE",
        f"{msg}{suffix}", status,
    )


def _lidar_row(service: NinaService) -> HealthRow:
    slam = getattr(service, "_slam", None)
    if slam is None:
        return HealthRow(
            "lidar", "Lidar (RPLIDAR A1)", "\u25A6",
            "Not opened yet (open Map tab or enable Autonomous mode)",
            STATUS_PENDING,
        )
    try:
        st = slam.status()
        connected = bool(st.get("lidar_connected", False))
        msg = str(st.get("lidar_message", "") or "")
        running = bool(st.get("running", False))
    except Exception as exc:
        return HealthRow(
            "lidar", "Lidar (RPLIDAR A1)", "\u25A6",
            f"status query failed: {exc}", STATUS_ERROR,
        )
    if not running:
        return HealthRow(
            "lidar", "Lidar (RPLIDAR A1)", "\u25A6",
            msg or "stopped", STATUS_PENDING,
        )
    if not connected:
        return HealthRow(
            "lidar", "Lidar (RPLIDAR A1)", "\u25A6",
            msg or "Lidar not detected", STATUS_ERROR,
        )
    return HealthRow(
        "lidar", "Lidar (RPLIDAR A1)", "\u25A6",
        msg or "scanning", STATUS_OK,
    )


def _ir_row(service: NinaService) -> HealthRow:
    health = _autonomy_health_dict(service)
    if health is None:
        return HealthRow(
            "ir", "IR cliff (GP2Y0E02B)", "\u25A6",
            "Not opened yet (enable Autonomous mode)", STATUS_PENDING,
        )
    state = health.get("ir") or (False, "no data")
    ok, msg = _unpack_pair(state)
    return HealthRow(
        "ir", "IR cliff (GP2Y0E02B)", "\u25A6",
        msg or ("ready" if ok else "not detected"),
        STATUS_OK if ok else STATUS_WARN,
    )


def _ultrasonic_row(service: NinaService) -> HealthRow:
    health = _autonomy_health_dict(service)
    if health is None:
        return HealthRow(
            "ultra", "Ultrasonic (HC-SR04)", "\u25A6",
            "Not opened yet (enable Autonomous mode)", STATUS_PENDING,
        )
    ring = health.get("ultrasonic") or []
    # SensorHealth.ultrasonic is List[(name, connected, message)].
    if not ring:
        return HealthRow(
            "ultra", "Ultrasonic (HC-SR04)", "\u25A6",
            "no sensors reported", STATUS_WARN,
        )
    up = sum(1 for _, ok, _ in ring if ok)
    total = len(ring)
    if up == total:
        status = STATUS_OK
    elif up > 0:
        status = STATUS_WARN
    else:
        status = STATUS_ERROR
    return HealthRow(
        "ultra", "Ultrasonic (HC-SR04)", "\u25A6",
        f"{up}/{total} channels up", status,
    )


def _depth_row(service: NinaService) -> HealthRow:
    health = _autonomy_health_dict(service)
    if health is None:
        return HealthRow(
            "depth", "Depth camera (D435)", "\u25CE",
            "Not opened yet (enable Autonomous mode)", STATUS_PENDING,
        )
    state = health.get("depth") or (False, "no data")
    ok, msg = _unpack_pair(state)
    return HealthRow(
        "depth", "Depth camera (D435)", "\u25CE",
        msg or ("streaming" if ok else "not detected"),
        STATUS_OK if ok else STATUS_WARN,
    )


def _drive_row(service: NinaService) -> HealthRow:
    drive = getattr(service, "_drive", None)
    if drive is None:
        return HealthRow(
            "bldc", "BLDC drive (JYQD V7.3E2)", "\u2B95",
            "Not opened yet (visit Drive tab)", STATUS_PENDING,
        )
    try:
        st = drive.state()
    except Exception as exc:
        return HealthRow(
            "bldc", "BLDC drive (JYQD V7.3E2)", "\u2B95",
            f"state() failed: {exc}", STATUS_ERROR,
        )
    if not isinstance(st, dict):
        return HealthRow(
            "bldc", "BLDC drive (JYQD V7.3E2)", "\u2B95",
            "state() returned unexpected type", STATUS_ERROR,
        )
    connected = bool(st.get("connected", False))
    msg = str(st.get("driver_message", "") or "")
    speed = st.get("speed_pct", "?")
    direction = st.get("direction", "?")
    detail = f"{direction} @ {speed}%" + (f" - {msg}" if msg else "")
    if connected:
        return HealthRow(
            "bldc", "BLDC drive (JYQD V7.3E2)", "\u2B95",
            detail, STATUS_OK,
        )
    # Drive controller exists but the underlying nav backend hasn't
    # come up yet (lazy init waits for the worker thread to drain its
    # _do_init task). That's normal during the first ~second after the
    # operator opens the Drive tab; reporting WARN is honest because
    # the user can't actually drive yet.
    return HealthRow(
        "bldc", "BLDC drive (JYQD V7.3E2)", "\u2B95",
        msg or "initialising", STATUS_WARN,
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _autonomy_health_dict(service: NinaService) -> Optional[dict]:
    """Return the autonomy health dict, or None if autonomy was never
    started. We never lazy-construct - that would open all four
    short-range sensors just to ask 'how are you?'."""
    autonomy = getattr(service, "_autonomy", None)
    if autonomy is None:
        return None
    try:
        st = autonomy.state()
    except Exception:
        return None
    return st.get("health") if isinstance(st, dict) else None


def _unpack_pair(value) -> tuple:
    """Tolerantly unpack a (bool, str) sensor-state tuple."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return bool(value[0]), str(value[1] or "")
    return False, ""


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
