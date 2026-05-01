"""Environment-driven configuration for the link daemon."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _default_state_path() -> Path:
    candidate = os.environ.get("NINA_LINK_STATE_PATH")
    if candidate:
        return Path(candidate)
    home = Path.home()
    for p in (Path("/var/lib/nina"), home / ".cache" / "sirena"):
        try:
            p.mkdir(parents=True, exist_ok=True)
            if os.access(p, os.W_OK):
                return p / "link_state.json"
        except OSError:
            continue
    return home / ".cache" / "sirena" / "link_state.json"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on", "y"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class LinkDaemonConfig:
    host: str = "0.0.0.0"
    port: int = 8787
    mock_nm: bool = False
    token: Optional[str] = None
    ap_ssid: str = "Nina-Setup"
    ap_password: str = "ninsetup"
    ap_wait_sec: int = 30
    state_path: Path = field(default_factory=_default_state_path)
    #: Turn off NM autoconnect on saved Wi-Fi at startup and for new profiles (STA only via app).
    disable_wifi_autoconnect: bool = True
    #: Max seconds to wait for Wi-Fi to leave NM "unavailable" (supplicant) before hotspot.
    wifi_ready_timeout_sec: int = 240
    wifi_ready_poll_sec: float = 2.0
    #: Retries for `nmcli device wifi hotspot` after disconnect (transient NM races).
    hotspot_attempts: int = 5
    #: When True, POST /v1/robot/drive may command BLDC hardware (conflicts with desktop UI if both run).
    enable_robot_bridge: bool = False
    robot_drive_speed_percent: int = 35
    robot_drive_default_duration_ms: int = 280

    def auth_required(self) -> bool:
        return bool(self.token and self.token.strip())


def load_config() -> LinkDaemonConfig:
    token_raw = os.environ.get("NINA_LINK_TOKEN", "").strip()
    ssid = os.environ.get("NINA_LINK_AP_SSID", "Nina-Setup").strip() or "Nina-Setup"
    pwd = os.environ.get("NINA_LINK_AP_PASSWORD", "ninsetup") or "ninsetup"
    cfg = LinkDaemonConfig(
        host=os.environ.get("NINA_LINK_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_env_int("NINA_LINK_PORT", 8787),
        mock_nm=_env_bool("NINA_LINK_MOCK", False),
        token=token_raw or None,
        ap_ssid=ssid,
        ap_password=pwd,
        ap_wait_sec=max(5, _env_int("NINA_LINK_AP_WAIT_SEC", 30)),
        state_path=Path(
            os.environ.get("NINA_LINK_STATE_PATH", str(_default_state_path()))
        ),
        disable_wifi_autoconnect=_env_bool(
            "NINA_LINK_DISABLE_WIFI_AUTOCONNECT", True
        ),
        wifi_ready_timeout_sec=max(
            5,
            _env_int("NINA_LINK_WIFI_READY_TIMEOUT", 240),
        ),
        wifi_ready_poll_sec=max(
            0.5,
            _env_float("NINA_LINK_WIFI_READY_POLL", 2.0),
        ),
        hotspot_attempts=max(1, _env_int("NINA_LINK_HOTSPOT_ATTEMPTS", 5)),
        enable_robot_bridge=_env_bool("NINA_LINK_ENABLE_ROBOT_BRIDGE", False),
        robot_drive_speed_percent=max(
            5, min(100, _env_int("NINA_LINK_DRIVE_SPEED_PERCENT", 35))
        ),
        robot_drive_default_duration_ms=max(
            50, min(5000, _env_int("NINA_LINK_DRIVE_DURATION_MS", 280))
        ),
    )
    if _env_bool("NINA_LINK_MOCK", False):
        cfg.mock_nm = True
    return cfg
