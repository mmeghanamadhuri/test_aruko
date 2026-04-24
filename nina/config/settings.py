from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class NavigationSettings:
    backend_name: str
    pwm_frequency_hz: int
    default_speed_percent: int
    turn_duration_sec: float


@dataclass(frozen=True)
class NinaSettings:
    serial_port: str
    baudrate: int
    neutral_action_name: str
    actions_dir: Path
    manifest_path: Path
    recordings_dir: Path
    recording_sample_hz: float
    navigation: NavigationSettings


def load_settings(repo_root: Path) -> NinaSettings:
    actions_dir = repo_root / "nina" / "actions"
    recordings_dir = repo_root / "nina" / "actions" / "recordings"
    manifest_path = actions_dir / "manifest.json"

    recordings_dir.mkdir(parents=True, exist_ok=True)

    navigation = NavigationSettings(
        backend_name=os.environ.get("NINA_NAV_BACKEND", "jetson"),
        pwm_frequency_hz=int(os.environ.get("NINA_NAV_PWM_HZ", "2000")),
        default_speed_percent=int(os.environ.get("NINA_NAV_SPEED", "15")),
        turn_duration_sec=float(os.environ.get("NINA_NAV_TURN_SEC", "2.3")),
    )

    return NinaSettings(
        serial_port=os.environ.get("NINA_DXL_PORT", "/dev/ttyUSB0"),
        baudrate=int(os.environ.get("NINA_DXL_BAUD", "222222")),
        neutral_action_name=os.environ.get("NINA_NEUTRAL_ACTION", "neutral"),
        actions_dir=actions_dir,
        manifest_path=manifest_path,
        recordings_dir=recordings_dir,
        recording_sample_hz=float(os.environ.get("NINA_RECORD_HZ", "20")),
        navigation=navigation,
    )
